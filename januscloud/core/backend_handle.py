# -*- coding: utf-8 -*-

import logging
from januscloud.common.utils import error_to_janus_msg, create_janus_msg, get_monotonic_time, random_uint64
from januscloud.common.error import JanusCloudError, JANUS_ERROR_INVALID_ELEMENT_TYPE, \
    JANUS_ERROR_PLUGIN_DETACH, JANUS_ERROR_BAD_GATEWAY, JANUS_ERROR_MISSING_MANDATORY_ELEMENT, JANUS_ERROR_INVALID_JSON
from gevent.queue import Queue
import gevent

log = logging.getLogger(__name__)

stop_message = object()

class HandleListener(object):
    def on_async_event(self, handle, event_msg):
        """ call when receive an async event from Janus server
        :param event_msg:
        :return:
        """
        pass

    def on_close(self, handle):
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
            session = self._session
            self._session = None
            session.on_handle_detached(self.handle_id)
            try:
                detach_message = create_janus_msg('detach', handle_id=self.handle_id)
                session.async_send_request(detach_message)
            except Exception:
                log.exception('Detach backend handle {} error'.format(self.handle_id))


    def async_send_message(self, body, jsep=None):
        if self._has_detach:
            raise JanusCloudError('backend handle {} has been destroyed'.format(self.handle_id),
                                  JANUS_ERROR_PLUGIN_DETACH)

        params = dict()
        params['body'] = body
        if jsep:
            params['jsep'] = jsep

        message = create_janus_msg('message', handle_id=self.handle_id, **params)
        self._session.async_send_request(message)


    def send_message(self, body, jsep=None):
        if self._has_detach:
            raise JanusCloudError('backend handle {} has been destroyed'.format(self.handle_id),
                                  JANUS_ERROR_PLUGIN_DETACH)

        params = dict()
        params['body'] = body
        if jsep:
            params['jsep'] = jsep

        message = create_janus_msg('message', handle_id=self.handle_id, **params)
        response = self._session.send_request(message)
        if response['janus'] == 'event' or response['janus'] == 'success':
            data = response['plugindata']['data']
            reply_jsep = response.get('jsep')
            return data, reply_jsep
        elif response['janus'] == 'error':
            raise JanusCloudError(response['error']['reason'], response['error']['code'])
        else:
            raise JanusCloudError(
                'unknown backend response {}'.format(response),
                JANUS_ERROR_BAD_GATEWAY)

    def send_trickle(self, candidate=None, candidates=None):
        if self._has_detach:
            raise JanusCloudError('backend handle {} has been destroyed'.format(self.handle_id),
                                  JANUS_ERROR_PLUGIN_DETACH)
        if candidate is None and candidates is None:
            raise JanusCloudError('Missing mandatory element (candidate|candidates)',
                                  JANUS_ERROR_MISSING_MANDATORY_ELEMENT)
        if candidate and candidates:
            raise JanusCloudError('Can\'t have both candidate and candidates',
                                  JANUS_ERROR_INVALID_JSON)
        params = {}
        if candidate:
            params['candidate'] = candidate
        if candidates:
            params['candidates'] = candidates
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
                self._handle_listener.on_close(self)
            except Exception:
                log.exception('on_close() exception for backend handle {}'.format(self.handle_id))

    def _async_event_handler_routine(self):
        while not self._has_detach:
            event_msg = self._async_event_queue.get()
            if self._has_detach or event_msg == stop_message:
                return
            try:
                if self._handle_listener:
                    self._handle_listener.on_async_event(self, event_msg)
            except Exception:
                log.exception('Error when handle async event for backend handle {}'.format(self.handle_id))

if __name__ == '__main__':

    pass




