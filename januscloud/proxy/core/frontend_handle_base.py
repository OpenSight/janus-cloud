# -*- coding: utf-8 -*-
import copy

import logging
import time
from gevent.queue import Queue
import gevent
from januscloud.common.utils import error_to_janus_msg, create_janus_msg
from januscloud.common.error import JanusCloudError, JANUS_ERROR_UNKNOWN_REQUEST, JANUS_ERROR_PLUGIN_MESSAGE, \
    JANUS_ERROR_MISSING_REQUEST
from januscloud.common.schema import Schema, Optional, DoNotCare, \
    Use, IntVal, Default, SchemaError, BoolVal, StrRe, ListVal, Or, STRING, \
    FloatVal, AutoDel


log = logging.getLogger(__name__)

JANUS_PLUGIN_OK = 0
JANUS_PLUGIN_OK_WAIT = 1

stop_message = object()


class FrontendHandleBase(object):
    """ This base class for frontend handle """

    def __init__(self, handle_id, session, plugin, opaque_id=None):
        self.handle_id = handle_id
        self.opaque_id = opaque_id
        self._session = session
        self._plugin = plugin
        self._has_destroy = False
        self.created = time.time()

        self.plugin_package_name = plugin.get_package()
        self._async_message_queue = Queue(maxsize=1024)
        self._async_message_greenlet = gevent.spawn(self._async_message_handler_routine)


    def detach(self):

        if self._has_destroy:
            return
        self._has_destroy = True

        # stop async message greenlet
        if not self._async_message_queue.full():
            self._async_message_queue.put(stop_message)
        self._async_message_greenlet = None

        # send detach event
        event = create_janus_msg('detached', self._session.session_id)
        event['sender'] = self.handle_id
        if self.opaque_id:
            event['opaque_id'] = self.opaque_id
        self._session.notify_event(event)

        log.info('handle {} is detach from plugin {}'.format(self.handle_id, self.plugin_package_name))



    def has_destroy(self):
        return self._has_destroy

    def handle_hangup(self):
        raise JanusCloudError('hangup not support\'hangup\'', JANUS_ERROR_MISSING_REQUEST)

    def handle_message(self, transaction, body, jsep=None):
        raise JanusCloudError('message not support\'message\'', JANUS_ERROR_PLUGIN_MESSAGE)

    def handle_trickle(self, candidate=None, candidates=None):
        raise JanusCloudError('hangup not support\'trickle\'', JANUS_ERROR_MISSING_REQUEST)

    def _enqueue_async_message(self, transaction, body, jsep=None):
        self._async_message_queue.put_nowait((transaction, body, jsep))

    def _async_message_handler_routine(self):
        while not self._has_destroy:
            msg = self._async_message_queue.get()
            if self._has_destroy or msg == stop_message:
                return
            try:
                transaction, body, jsep = msg
                self._handle_async_message(transaction, body, jsep)
            except Exception:
                log.exception('Error when handle async message for handle {}'.format(self.handle_id))


    def _handle_async_message(self, transaction, body, jsep):
        """ need to override by subclass """
        raise JanusCloudError('async message handler not support\'message\'', JANUS_ERROR_PLUGIN_MESSAGE)

    def _push_plugin_event(self, data, jsep=None, transaction=None):
        params = dict()
        params['plugindata'] = {
            'plugin': self.plugin_package_name,
            'data': data
        }
        if jsep:
            params['jsep'] = jsep
        self._push_event('event', transaction, **params)

    def _push_event(self, method, transaction=None, **kwargs):
        if self._has_destroy:
            return
        event = create_janus_msg(method, self._session.session_id, transaction, **kwargs)
        event['sender'] = self.handle_id
        if self.opaque_id:
            event['opaque_id'] = self.opaque_id
        self._session.notify_event(event)






if __name__ == '__main__':
    pass




