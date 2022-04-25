# -*- coding: utf-8 -*-

import logging
import time
import importlib
import gevent
import uuid


from januscloud.common.error import JanusCloudError, JANUS_ERROR_SERVICE_UNAVAILABLE, JANUS_ERROR_BAD_GATEWAY
from januscloud.common.utils import random_uint64, create_janus_msg, get_host_ip, get_monotonic_time
from januscloud.core.backend_server import JANUS_SERVER_STATUS_ABNORMAL, JANUS_SERVER_STATUS_NORMAL, \
    JANUS_SERVER_STATUS_MAINTENANCE, JANUS_SERVER_STATUS_HWM
from januscloud.core.backend_session import BackendTransaction
from januscloud.core.backend_session import get_backend_session
from januscloud.common.error import JanusCloudError, JANUS_ERROR_UNKNOWN_REQUEST, JANUS_ERROR_INVALID_REQUEST_PATH, \
    JANUS_ERROR_BAD_GATEWAY, JANUS_ERROR_CONFLICT, JANUS_ERROR_NOT_IMPLEMENTED, JANUS_ERROR_INTERNAL_ERROR

log = logging.getLogger(__name__)

BACKEND_SESSION_AUTO_DESTROY_TIME = 10 
JANUS_VIDEOROOM_PACKAGE = 'janus.plugin.videoroom'
JANUS_VIDEOROOM_ERROR_UNKNOWN_ERROR = 499


def _send_backend_message(backend_handle, body, jsep=None):
    if backend_handle is None:
        raise JanusCloudError('Not connected', JANUS_ERROR_INTERNAL_ERROR)
    data, reply_jsep = backend_handle.send_message(body=body, jsep=jsep)
    if 'error_code' in data:
        raise JanusCloudError(data.get('error', 'unknown'),
                              data.get('error_code', JANUS_VIDEOROOM_ERROR_UNKNOWN_ERROR))

    return data, reply_jsep


class VideoroomSweeper(object):

    def __init__(self, server_ip, ws_port, des_filter='januscloud-',
                 check_interval=30, room_auto_destroy_timeout=600):

        self.server_ip = server_ip
        if server_ip == '':
            self.server_ip = '127.0.0.1'
        self.ws_port = ws_port
        self.check_interval = check_interval
        self.destroy_timeout = room_auto_destroy_timeout
        self.des_filwter = des_filter

        self._handle = None
        self._has_destroyed = False

        self._check_greenlet = None

        self._idle_rooms = {}

        self._server_status = JANUS_SERVER_STATUS_ABNORMAL

    def destroy(self):
        if self._has_destroy:
            return
        self._has_destroy = True


        if self._handle:
            handle = self._handle
            self._handle = None
            handle.detach()
        
    
        self._check_greenlet = None
        self._server_status = JANUS_SERVER_STATUS_ABNORMAL
        self._idle_rooms = {}


    def start(self):
        self._check_greenlet = gevent.spawn(self._check_routine)
        self._has_destroy = False


    @property
    def url(self):
        return 'ws://{}:{}'.format(self.server_ip, self.ws_port)


    def connect_server(self):
        if self._handle is not None:
            # already connect, just return
            return 

        # attach backend handle
        self._handle = get_backend_session(
            self.url,
            auto_destroy=self.check_interval*3
            ).attach_handle(JANUS_VIDEOROOM_PACKAGE, handle_listener=self)    

    def check_idle(self):
        try:
            if self._has_destroy:
                return

            if self._server_status == JANUS_SERVER_STATUS_ABNORMAL:
                # server abnormal, pass idel check
                return 

            if self._handle is None:
                # not connect server, connect first
                self.connect_server()

            handle = self._handle

            # 1. get room list
            reply_data, reply_jsep = _send_backend_message(handle, {
                'request': 'list'
            })
            room_list_info = reply_data.get('list', [])

            # 2. find out idle rooms and timeout rooms
            now = get_monotonic_time()
            idle_rooms = {}
            timeout_room_ids = []
            for room_info in room_list_info:
                room_id = int(room_info.get('room', 0))

                if room_id == 0:
                    continue   # pass invalid or in-service room
                elif not room_info.get('description', '').startswith(self.des_filwter):
                    continue   # pass not januscloud-created room
                elif room_info.get('num_participants', 1) > 0:
                    continue   # not idle
                
                # this is a idle room
                idle_ts = self._idle_rooms.get(room_id, 0)
                if idle_ts == 0:
                    # new idle room
                    idle_rooms[room_id] = now
                elif now - idle_ts > self.destroy_timeout:
                    # timeout room
                    timeout_room_ids.append(room_id)
                else:
                    # old idle room
                    idle_rooms[room_id] = idle_ts
            self._idle_rooms = idle_rooms
                  
            # 3. destroy the timeout rooms
            for room_id in timeout_room_ids:
                # auto destroy the idle room
                log.warning('Sweeper found the backend idle room {}, destroy it'.format(
                            room_id))
                handle.send_message({
                    'request': 'destroy',
                    'room': room_id
                })

        except Exception as e:
            if self._handle:
                handle = self._handle
                self._handle = None
                handle.detach()
            # ignore all exception when check
            log.debug('Videoroom sweeper check failed on server "{}" : {}. Retry in {} secs'.format(
                    self.url, str(e), self.check_interval))
            pass 

    def _check_routine(self):
        while not self._has_destroy:
            gevent.sleep(self.check_interval)
            self.check_idle()

    def on_status_changed(self, new_state):
        self._server_status = new_state

    # backend handle listener callback
    def on_async_event(self, handle, event_msg):
        # no event need to process for the backend room control handle
        pass

    def on_close(self, handle):
        if self._handle != None and self._handle == handle:
            self._handle = None # clean up the handle
 


if __name__ == '__main__':
    pass






