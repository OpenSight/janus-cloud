# -*- coding: utf-8 -*-

import logging
import time
import importlib
import gevent


from januscloud.common.error import JanusCloudError, JANUS_ERROR_SERVICE_UNAVAILABLE, JANUS_ERROR_NOT_IMPLEMENTED
from januscloud.common.utils import random_uint64, create_janus_msg, get_host_ip
from januscloud.core.backend_server import JANUS_SERVER_STATUS_ABNORMAL, JANUS_SERVER_STATUS_NORMAL, \
    JANUS_SERVER_STATUS_MAINTENANCE
from januscloud.core.backend_session import BackendTransaction
from januscloud.sentinel.process_mngr import PROC_RUNNING
from januscloud.transport.ws import WSClient

log = logging.getLogger(__name__)


class BasicPoster(object):
    def __init__(self, janus_server, post_type, name='', *args, **kwargs):
        self.post_type = post_type
        self._janus_server = janus_server
        self.name = name
        self.connected = False
        janus_server.register_listener(self)

    def on_status_changed(self, new_state):
        pass

    def on_stat_updated(self):
        pass

    def post(self):
        pass

_poster_types = {}

_posters = []


def register_poster_type(poster_type, poster_class):
    _poster_types[poster_type] = poster_class


def add_poster(janus_server, post_type, name='', *args, **kwargs):
    poster_class = _poster_types.get(post_type)
    if poster_class is None:
        raise JanusCloudError('poster type {} not register'.format(post_type),
                              JANUS_ERROR_NOT_IMPLEMENTED)
    poster = poster_class(janus_server, post_type, name, *args, **kwargs)
    _posters.append(poster)
    return poster


def list_posters():
    return list(_posters)


if __name__ == '__main__':
    pass






