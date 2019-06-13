# -*- coding: utf-8 -*-

from gevent.queue import Queue
from januscloud.common import gsqlite3


class SqliteConnPool(object):
    def __init__(self, file_path, size=1):
        self._conn_queue = Queue
        self._size = size
        if '?' in file_path:
            raise Exception('Sqlite filename cannot include \'?\' character')
        self._db_uri = 'file:{}?cache=shared'.format(file_path)
        for i in range(size):
            conn = gsqlite3.connect(self._db_uri, uri=True)
            self._conn_queue.put(conn)

    def get(self, timeout=None):
        return self._conn_queue.get(timeout=timeout)

    def put(self, conn):
        self._conn_queue.put(conn)


