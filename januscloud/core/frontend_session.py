# -*- coding: utf-8 -*-

import logging
from januscloud.common.utils import error_to_janus_msg, create_janus_msg, get_monotonic_time, random_uint64
from januscloud.common.error import JanusCloudError, JANUS_ERROR_SESSION_CONFLICT, \
    JANUS_ERROR_SESSION_NOT_FOUND, JANUS_ERROR_PLUGIN_NOT_FOUND, JANUS_ERROR_PLUGIN_ATTACH
from januscloud.common.schema import Schema, Optional, DoNotCare, \
    Use, IntVal, Default, SchemaError, BoolVal, StrRe, ListVal, Or, STRING, \
    FloatVal, AutoDel
import time
import gevent
import sys

from januscloud.core.plugin_base import get_plugin

log = logging.getLogger(__name__)


TIMEOUT_CHECK_INTERVAL  =  2


class FrontendSession(object):
    """ This frontend session represents a Janus session  """

    def __init__(self, session_id, transport=None):
        self.session_id = session_id
        self.ts = transport
        self._handles = {}
        self.last_activity = get_monotonic_time()
        self._has_destroyed = False

    def notify_event(self, event):
        try:
            if self.ts:
                self.ts.send_message(event)
                log.debug('an asynchronous event messge ({}) is sent back asynchronous for session "{}" '.format(event, self.session_id))
        except Exception as e:
            log.debug('Failed to send backe Asynchronous event ({}) on session (id:{}): {}, Ignore'.format(event, self.session_id, e))

    def destroy(self):
        """ destroy the session

        return:
            no value
        note: no exception would be raised
        """
        if self._has_destroyed:
            return
        else:
            self._has_destroyed = True

        self.ts = None
        detach_handles = self._handles
        self._handles = {}

        # detach all handles on it
        for handle in detach_handles.values():
            handle.detach()       # would result into IO block
        detach_handles.clear()

        log.info('session: {} has destroyed '.format(self.session_id))

    def transport_claim(self, new_transport):
        if self.ts:
            self.ts.session_over(self.session_id, False, True)
        self.ts = new_transport
        new_transport.session_claimed(self.session_id)

    def get_handle(self, handle_id):
        return self._handles.get(handle_id)

    def activate(self):
        self.last_activity = get_monotonic_time()

    def attach_handle(self, plugin, opaque_id=None):
        if self._has_destroyed:
            raise JanusCloudError('session {} has been destroy'.format(self.session_id), JANUS_ERROR_PLUGIN_ATTACH)
        plugin = get_plugin(plugin)
        if plugin is None:
            raise JanusCloudError("No such plugin '%s'".format(plugin), JANUS_ERROR_PLUGIN_NOT_FOUND)
        handle_id = random_uint64()
        while handle_id in self._handles:
            handle_id = random_uint64()
        handle = plugin.create_handle(handle_id, self, opaque_id)
        self._handles[handle_id] = handle
        log.info('a new handle {} on session {} is attached for plugin {}'.format(
            handle_id, self.session_id, plugin))
        return handle

    def detach_handle(self, handle_id):
        handle = self._handles.pop(handle_id, None)
        if handle is None:
            return
        handle.detach()

class FrontendSessionManager(object):

    def __init__(self, session_timeout):
        self._sessions = {}
        self._session_timeout = session_timeout
        self._started = True
        self.check_greenlet = gevent.spawn(self._check_session_timeout_routine)

    def create_new_session(self, session_id=0, transport=None):
        if session_id == 0:
            session_id = random_uint64()
            while session_id in self._sessions:
                session_id = random_uint64()
        if session_id in self._sessions:
            raise JanusCloudError('Session ID already in use', JANUS_ERROR_SESSION_CONFLICT)
        session = FrontendSession(session_id, transport)
        self._sessions[session_id] = session
        if transport:
            transport.session_created(session_id)

        log.info('Creating new session: {} '.format(session_id))

        return session

    def find_session(self, session_id):
        session = self._sessions.get(session_id)
        if session is None:
            log.error("Couldn't find any session {}".format(session_id))
            raise JanusCloudError('No such session {}'.format(session_id), JANUS_ERROR_SESSION_NOT_FOUND)
        return session

    def destroy_session(self, session_id):
        session = self._sessions.pop(session_id, None)
        if session is None:
            log.error("Couldn't find any session {}".format(session_id))
            raise JanusCloudError('No such session {}'.format(session_id), JANUS_ERROR_SESSION_NOT_FOUND)
        transport = session.ts
        session.destroy()
        if transport:
            transport.session_over(session_id, False, False)

    def transport_gone(self, transport):
        gone_sessions = []
        for session in self._sessions.values():
            if session.ts == transport:
                # destroy the session because of the underlayer transport session is gone
                gone_sessions.append(session)
        for session in gone_sessions:
                log.debug('  -- Session "{}" will be over for transport gone '.format(session.session_id))
                self._sessions.pop(session.session_id, None)
        for session in gone_sessions:
            session_id = session.session_id
            try:
                session.destroy()
            except Exception as e:
                log.exception('Failed to destroy transport-gone session "{}"'.format(session_id))

    def _check_session_timeout_routine(self):
        while True:
            if self._session_timeout > 0:
                # session timeout check is enable
                now = get_monotonic_time()
                timeout_sessions = []
                for session in self._sessions.values():
                    if now - session.last_activity > self._session_timeout:
                        timeout_sessions.append(session)
                for session in timeout_sessions:
                    self._sessions.pop(session.session_id, None)  # avoid future usage

                # kick out all timeout session
                for session in timeout_sessions:
                    try:
                        self._kick_timeout_sessions(session)
                    except Exception as e:
                        log.exception('Failed to kick out the timeout session "{}"'.format(session.session_id))
                timeout_sessions.clear()
                delta_time = get_monotonic_time() - now
                if delta_time < TIMEOUT_CHECK_INTERVAL:
                    gevent.sleep(TIMEOUT_CHECK_INTERVAL - delta_time)
            else:
                # session timeout check is disable, just None loop
                gevent.sleep(TIMEOUT_CHECK_INTERVAL)

    def _kick_timeout_sessions(self, session):
        session_id = session.session_id
        transport = session.ts
        session.notify_event(create_janus_msg('timeout', session_id))
        session.destroy()
        if transport:
            # notify the transport
            transport.session_over(session_id, True, False)



if __name__ == '__main__':

    pass




