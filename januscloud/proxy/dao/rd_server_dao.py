# -*- coding: utf-8 -*-
import logging
from januscloud.common.utils import to_redis_hash
from januscloud.proxy.core.backend_server import BackendServer
log = logging.getLogger(__name__)

"""
januscloud:backend_servers:<server_name>       hash map of the backend janus server info, will expired 

"""


class RDServerDao(object):

    def __init__(self, redis_client=None):
        self._redis_client = redis_client

    def get_by_name(self, server_name):
        rd_server = self._redis_client.hgetall(self._key_server(server_name))
        if rd_server:
            return self._from_rd_server(rd_server)
        else:
            return None

    def del_by_name(self, server_name):
        self._redis_client.delete(self._key_server(server_name))

    def add(self, server):
        server_key = self._key_server(server.name)
        with self._redis_client.pipeline() as p:
            p.hmset(
                server_key,
                to_redis_hash(server),
            )
            p.expire(server_key, server.expire)
            p.execute()

    def update(self, server):
        server_key = self._key_server(server.name)
        with self._redis_client.pipeline() as p:
            p.hmset(
                server_key,
                to_redis_hash(server),
            )
            p.expire(server_key, server.expire)
            p.execute()

    def get_list(self):
        server_list = []
        server_key_list = self._redis_client.keys(pattern='januscloud:backend_servers:*')
        for server_key in server_key_list:
            rd_server = self._redis_client.hgetall(server_key)
            if rd_server:
                server_list.append(self._from_rd_server(rd_server))
        return server_list

    @staticmethod
    def _key_server(self, server_name):
        return 'januscloud:backend_servers:{0}'.format(server_name)

    @staticmethod
    def _from_rd_server(rd_server):
        server = BackendServer(name=str(rd_server['name']),
                               url=str(rd_server['url']),
                               status=int(rd_server['status']),
                               session_timeout=int(rd_server.get('session_timeout', 0)),
                               location=str(rd_server.get('location', '')),
                               isp=str(rd_server.get('isp', '')),
                               session_num=int(rd_server.get('session_num', 0)),
                               handle_num=int(rd_server.get('handle_num', 0)),
                               expire=int(rd_server.get('expire', 60)),
                               start_time=float(rd_server.get('start_time', 0.0)))
        if 'ctime' in rd_server:
            server.ctime = float(rd_server['ctime'])
        if 'utime' in rd_server:
            server.utime = float(rd_server['utime'])

        return server
