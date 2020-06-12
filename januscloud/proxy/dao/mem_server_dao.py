# -*- coding: utf-8 -*-
import copy

from januscloud.common.error import JANUS_ERROR_SESSION_CONFLICT, JanusCloudError, JANUS_ERROR_CONFLICT, \
    JANUS_ERROR_NOT_FOUND


class MemServerDao(object):
    def __init__(self):
        self._servers_by_name = {}

    def get_by_name(self, server_name):
        server = self._servers_by_name.get(server_name)
        if server:
            return copy.copy(server)
        else:
            return None

    def del_by_name(self, server_name):
        self._servers_by_name.pop(server_name, None)

    def add(self, server):
        if server.name in self._servers_by_name:
            raise JanusCloudError('server {} already in repo'.format(server.name), JANUS_ERROR_CONFLICT)
        self._servers_by_name[server.name] = copy.copy(server)


    def update(self, server):
        org_server = self._servers_by_name.get(server.name)
        if not org_server:
            raise JanusCloudError('server {} NOT found'.format(server.name), JANUS_ERROR_NOT_FOUND)
        org_server.__dict__.update(server.__dict__)

    def get_list(self):
        return [copy.copy(server) for server in self._servers_by_name.values()]









