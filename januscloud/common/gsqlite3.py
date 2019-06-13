""" A gevent friendly wrapper for the standard 'sqlite3' module.

The strategy used is a simple one.  All potentially time consuming
operations are run using the threadpool attached to the ``gevent`` hub.
"""

# We want to look as much like the sqlite3 DBAPI module as possible.
# The easiest way of exposing the same module interface is to do this.
from sqlite3 import *
from functools import wraps
import time

import sqlite3

from gevent.hub import get_hub


def init_moving_average(initial, window_size=10):
    return [None] + [initial] * (window_size - 1)


def update_average(value, values):
    i = values.index(None)
    values[i] = value
    average = sum(values) / len(values)
    values[(i + 1) % len(values)] = None
    return average


@wraps(sqlite3.connect)
def connect(*args, **kwargs):
    kwargs['factory'] = Connection
    return sqlite3.connect(*args, **kwargs)


def _using_threadpool(method):
    @wraps(method, ['__name__', '__doc__'])
    def apply(*args, **kwargs):
        return get_hub().threadpool.apply(method, args, kwargs)
    return apply


# OK so we share this between threads/greenlets, but
# ultimately the worst that will happen with
# simultaneous updates is that a query will move between
# being considered a fast query and a slow query
# so it isn't really worth locking (the GIL is enough here)
query_speed = {}
FAST_ENOUGH = object()
too_slow = 0.001

def _maybe_execute_using_threadpool(method):
    timefunc = time.time
    @wraps(method, ['__name__', '__doc__'])
    def apply(*args, **kwargs):
        sql = args[1:2]
        moving_average = query_speed.get(sql, None)
        if moving_average is FAST_ENOUGH:
            t0 = timefunc()
            # this query is usually fast so run it directly
            result = method(*args, **kwargs)
            duration = timefunc() - t0
            if duration >= too_slow:
                query_speed[sql] = init_moving_average(duration)
        else:
            t0 = timefunc()
            # this query is usually slow so run it in another thread
            result = get_hub().threadpool.apply(method, args, kwargs)
            duration = timefunc() - t0
            if moving_average is not None:
                avg = update_average(duration, moving_average)
                if avg < too_slow:
                    query_speed[sql] = FAST_ENOUGH
            else:
                # first time we've seen this query
                if duration > too_slow:
                    query_speed[sql] = init_moving_average(duration)
                else:
                    query_speed[sql] = FAST_ENOUGH
        return result
    return apply


class Cursor(sqlite3.Cursor):
    """ A greenlet friendly sub-class of sqlite3.Cursor. """


for method in [sqlite3.Cursor.executemany,
               sqlite3.Cursor.executescript,
               sqlite3.Cursor.fetchone,
               sqlite3.Cursor.fetchmany,
               sqlite3.Cursor.fetchall]:
    setattr(Cursor, method.__name__, _using_threadpool(method))


setattr(Cursor,
        'execute',
        _maybe_execute_using_threadpool(sqlite3.Cursor.execute))


class Connection(sqlite3.Connection):
    """ A greenlet friendly sub-class of sqlite3.Connection. """

    def __init__(self, *args, **kwargs):
        # by default [py]sqlite3 checks that object methods are run in the same
        # thread as the one that created the Connection or Cursor. If it finds
        # they are not then an exception is raised.
        # <https://docs.python.org/2/library/sqlite3.html#multithreading>
        # Luckily for us we can switch this check off.
        kwargs['check_same_thread'] = False
        super(Connection, self).__init__(*args, **kwargs)

    def cursor(self):
        return Cursor(self)

setattr(Connection,
        'execute',
        _maybe_execute_using_threadpool(sqlite3.Connection.execute))


for method in [sqlite3.Connection.commit,
               sqlite3.Connection.rollback]:
    setattr(Connection, method.__name__, _using_threadpool(method))


#
# A dialect for SQLAlchemy. For example 'sqlite+gsqlite3://'.

try:
    from sqlalchemy.dialects.sqlite.pysqlite import SQLiteDialect_pysqlite
except ImportError:
    pass
else:
    class SQLiteDialect_gsqlite3(SQLiteDialect_pysqlite):

        @classmethod
        def dbapi(cls):
            import gsqlite3
            return gsqlite3
