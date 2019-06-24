# -*- coding: utf-8 -*-


class SqliteRoomDao(object):
    def __init__(self, conn_pool):
        self._conn_pool = conn_pool
        self._create_room_table()

    def _create_room_table(self):
        pass

    def get_by_id(self, room_id):
        conn = self._conn_pool.get()
        try:
            with conn:
                pass
        except Exception:
            raise
        finally:
            self._conn_pool.put(conn)


    def delete_by_id(self, room_id):
        pass

    def add(self, room_info):
        pass

    def update(self, room_info):
        pass

    def get_list(self):
        pass




