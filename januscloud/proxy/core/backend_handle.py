# -*- coding: utf-8 -*-

import logging
from januscloud.common.utils import error_to_janus_msg, create_janus_msg, get_monotonic_time, random_uint64
from januscloud.common.error import JanusCloudError, JANUS_ERROR_INVALID_ELEMENT_TYPE, \
    JANUS_ERROR_PLUGIN_DETACH, JANUS_ERROR_BAD_GATEWAY
from gevent.queue import Queue
import gevent

log = logging.getLogger(__name__)

stop_message = object()

class HandleListener(object):
    def on_async_event(self, event_msg):
        """ call when receive an async event from Janus server
        :param event_msg:
        :return:
        """
        pass

    def on_close(self, handle_id):
        """ call when the related session is destroyed
        :param handle_id:
        :return:
        """
        pass


class BackendHandle(object):
    """ This backend handle represents a Janus handle  """

    def __init__(self, handle_id, plugin_package_name, session, opaque_id=None, handle_listener=None):
        self.handle_id = handle_id
        self.plugin_package_name = plugin_package_name
        self.opaque_id = opaque_id
        self._session = session
        self._has_detach = False
        self._handle_listener = handle_listener

        self._async_event_queue = Queue(maxsize=1024)
        self._async_event_greenlet = gevent.spawn(self._async_event_handler_routine)

    def detach(self):
        """ detach this handle from the session

        return:
            no value
        note: no exception would be raised
        """
        if self._has_detach:
            return
        self._has_detach = True

        # stop async event greenlet
        if not self._async_event_queue.full():
            self._async_event_queue.put(stop_message)
        self._async_event_greenlet = None

        if self._session:
            self._session.on_handle_detached(self.handle_id)
            try:
                detach_message = create_janus_msg('detach', handle_id=self.handle_id)
                self._session.send_request(detach_message)
            except Exception:
                log.exception('Detach backend handle {} error'.format(self.handle_id))

            self._session = None

    def send_message(self, params):
        if self._has_detach:
            raise JanusCloudError('backend handle {} has been destroyed'.format(self.handle_id),
                                  JANUS_ERROR_PLUGIN_DETACH)

        message = create_janus_msg('message', handle_id=self.handle_id, **params)
        response = self._session.send_request(message)
        if response['janus'] == 'event' or response['janus'] == 'success':
            return response
        elif response['janus'] == 'error':
            raise JanusCloudError(response['error']['reason'], response['error']['code'])
        else:
            raise JanusCloudError(
                'unknown backend response {}'.format(response),
                JANUS_ERROR_BAD_GATEWAY)

    def send_trickle(self, params):
        if self._has_detach:
            raise JanusCloudError('backend handle {} has been destroyed'.format(self.handle_id),
                                  JANUS_ERROR_PLUGIN_DETACH)
        trickle_msg = create_janus_msg('trickle', handle_id=self.handle_id, **params)
        response = self._session.send_request(trickle_msg, ignore_ack=False)
        if response['janus'] == 'ack':
            pass # successful
        elif response['janus'] == 'error':
            raise JanusCloudError(response['error']['reason'], response['error']['code'])
        else:
            raise JanusCloudError(
                'unknown backend response {}'.format(response),
                JANUS_ERROR_BAD_GATEWAY)

    def send_hangup(self):
        if self._has_detach:
            raise JanusCloudError('backend handle {} has been destroyed'.format(self.handle_id),
                                  JANUS_ERROR_PLUGIN_DETACH)
        hangup_msg = create_janus_msg('hangup', handle_id=self.handle_id)
        response = self._session.send_request(hangup_msg)
        if response['janus'] == 'success':
            pass # successful
        elif response['janus'] == 'error':
            raise JanusCloudError(response['error']['reason'], response['error']['code'])
        else:
            raise JanusCloudError(
                'unknown backend response {}'.format(response),
                JANUS_ERROR_BAD_GATEWAY)

    def on_async_event(self, event_msg):
        if not self._async_event_queue.full():
            self._async_event_queue.put(event_msg)
        else:
            # drop the event
            log.error("backend handle {} async event queue is full, drop the receiving event".format(self.handle_id))


    def on_close(self):
        if self._has_detach:
            return
        self._has_detach = True

        # stop async event greenlet
        if not self._async_event_queue.full():
            self._async_event_queue.put(stop_message)
        self._async_event_greenlet = None

        self._session = None

        if self._handle_listener:
            try:
                self._handle_listener.on_close(self.handle_id)
            except Exception:
                log.exception('on_close() exception for backend handle {}'.format(self.handle_id))

    def _async_event_handler_routine(self):
        while not self._has_detach:
            event_msg = self._async_event_queue.get()
            if self._has_detach or event_msg == stop_message:
                return
            try:
                if self._handle_listener:
                    self._handle_listener.on_async_event(event_msg)
            except Exception:
                log.exception('Error when handle async event for backend handle {}'.format(self.handle_id))

if __name__ == '__main__':

    pass




