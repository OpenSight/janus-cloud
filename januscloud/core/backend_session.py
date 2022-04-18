# -*- coding: utf-8 -*-
import gevent.monkey
gevent.monkey.patch_all()
import logging
from januscloud.common.utils import error_to_janus_msg, create_janus_msg, get_monotonic_time, random_uint64
from januscloud.common.error import JanusCloudError, JANUS_ERROR_SESSION_CONFLICT, \
    JANUS_ERROR_BAD_GATEWAY, JANUS_ERROR_GATEWAY_TIMEOUT, JANUS_ERROR_SERVICE_UNAVAILABLE
from januscloud.common.schema import Schema, Optional, DoNotCare, \
    Use, IntVal, Default, SchemaError, BoolVal, StrRe, ListVal, Or, STRING, \
    FloatVal, AutoDel
import time
import gevent
from gevent.event import Event
from januscloud.transport.ws import WSClient
from januscloud.core.backend_handle import BackendHandle

log = logging.getLogger(__name__)


BACKEND_SESSION_STATE_CREATING = 1
BACKEND_SESSION_STATE_ACTIVE = 2
BACKEND_SESSION_STATE_DESTROYED = 3


class BackendTransaction(object):
    def __init__(self, transaction_id, request_msg, url, ignore_ack=True):
        self.transaction_id = transaction_id
        self.request_msg = request_msg
        self._response_ready = Event()
        self._response = None
        self._ignore_ack = ignore_ack
        self._url = url

    def wait_response(self, timeout=None):
        ready = self._response_ready.wait(timeout=timeout)
        if not ready:
            raise JanusCloudError('Request {} Timeout for backend Janus server: {}'.format(self.request_msg, self._url),
                                  JANUS_ERROR_GATEWAY_TIMEOUT)
        return self._response

    @property
    def response(self):
        return self._response

    @response.setter
    def response(self, response):
        method = response.get('janus', None)
        if self._ignore_ack and method == 'ack':
            return   # not consider ack is response
        self._response = response
        self._response_ready.set()


class BackendSession(object):
    """ This backend session represents a session of the backend Janus server """

    def __init__(self, url, auto_destroy=False, api_secret=''):
        self.url = url
        self._ws_client = None
        self._transactions = {}
        self.session_id = 0
        self.state = BACKEND_SESSION_STATE_CREATING
        self._handles = {}
        self._auto_destroy = int(auto_destroy)
        self._auto_destroy_greenlet = None
        self._keepalive_interval = 10
        self._keepalive_greenlet = None
        self._api_secret = api_secret
        _sessions[url] = self

    def init(self):
        try:
            self._ws_client = WSClient(self.url, self._recv_msg_cbk, self._close_cbk, protocols=['janus-protocol'])
            session_timeout = self._get_session_timeout()
            if session_timeout:
                self._keepalive_interval = int(session_timeout / 3)
            self.session_id = self._create_janus_session()
            self.state = BACKEND_SESSION_STATE_ACTIVE
            self._keepalive_greenlet = gevent.spawn(self._keepalive_routine)
        except Exception:
            if self._ws_client:
                self._ws_client.close()
                self._ws_client = None
            self.session_id = 0
            self._keepalive_greenlet = None
            self.state = BACKEND_SESSION_STATE_CREATING
            raise

    def attach_handle(self, plugin_package_name, opaque_id=None, handle_listener=None):
        """

        :param plugin_pacakge_name:  str plugin package name
        :param opaque_id:   str opaque id
        :param handle_listener: handle related  callback listener which cannot block
        :return: BackendHandle object
        """
        if self.state == BACKEND_SESSION_STATE_DESTROYED:
            raise JanusCloudError('Session has destroy for Janus server: {}'.format(self.url),
                                  JANUS_ERROR_SERVICE_UNAVAILABLE)

        attach_request_msg = create_janus_msg('attach', plugin=plugin_package_name)
        if opaque_id:
            attach_request_msg['opaque_id'] = opaque_id

        response = self.send_request(attach_request_msg)  # would block for IO
        if response['janus'] == 'success':
             handle_id = response['data']['id']
        elif response['janus'] == 'error':
            raise JanusCloudError(
                'attach error for Janus server {} with reason {}'.format(self.url, response['error']['reason']),
                response['error']['code'])
        else:
            raise JanusCloudError(
                'attach error for Janus server: {} with invalid response {}'.format(self.url, response),
                JANUS_ERROR_BAD_GATEWAY)

        # check again when wake up from block IO
        if self.state == BACKEND_SESSION_STATE_DESTROYED:
            raise JanusCloudError('Session has destroy for Janus server: {}'.format(self.url),
                                  JANUS_ERROR_SERVICE_UNAVAILABLE)

        handle = BackendHandle(handle_id, plugin_package_name, self,
                               opaque_id=opaque_id, handle_listener=handle_listener)
        self._handles[handle_id] = handle
        if self._auto_destroy_greenlet:
            gevent.kill(self._auto_destroy_greenlet)
            self._auto_destroy_greenlet = None
        return handle

    def get_handle(self, handle_id, default=None):
        return self._handles.get(handle_id, default)

    def on_handle_detached(self, handle_id):
        self._handles.pop(handle_id, None)

    def async_send_request(self, msg):
        if self.state == BACKEND_SESSION_STATE_DESTROYED:
            raise JanusCloudError('Session has destroy for Janus server: {}'.format(self.url),
                                  JANUS_ERROR_SERVICE_UNAVAILABLE)
        transaction_id = self._genrate_new_tid()
        send_msg = dict.copy(msg)
        send_msg['session_id'] = self.session_id
        send_msg['transaction'] = transaction_id
        if self._api_secret:
            send_msg['apisecret'] = self._api_secret  
        log.debug('Send Async Request {} to Janus server: {}'.format(send_msg, self.url))
        self._ws_client.send_message(send_msg)              

    def send_request(self, msg, ignore_ack=True, timeout=30):

        if self.state == BACKEND_SESSION_STATE_DESTROYED:
            raise JanusCloudError('Session has destroy for Janus server: {}'.format(self.url),
                                  JANUS_ERROR_SERVICE_UNAVAILABLE)
        transaction_id = self._genrate_new_tid()

        send_msg = dict.copy(msg)
        send_msg['session_id'] = self.session_id
        send_msg['transaction'] = transaction_id
        if self._api_secret:
            send_msg['apisecret'] = self._api_secret
        transaction = BackendTransaction(transaction_id, send_msg, url=self.url, ignore_ack=ignore_ack)
        try:
            self._transactions[transaction_id] = transaction
            log.debug('Send Request {} to Janus server: {}'.format(send_msg, self.url))
            self._ws_client.send_message(send_msg)
            response = transaction.wait_response(timeout=timeout)
            log.debug('Receive Response {} from Janus server: {}'.format(response, self.url))
            return response
        finally:
            self._transactions.pop(transaction_id, None)

    def destroy(self):
        if self.state == BACKEND_SESSION_STATE_DESTROYED:
            return
        self.state = BACKEND_SESSION_STATE_DESTROYED
        if _sessions.get(self.url) == self:
            _sessions.pop(self.url)

        if self._auto_destroy_greenlet:
            gevent.kill(self._auto_destroy_greenlet)
            self._auto_destroy_greenlet = None

        if self._keepalive_greenlet is not None:
            self._keepalive_greenlet = None

        for handle in self._handles.values():
            handle.on_close()
        self._handles.clear()

        if self._ws_client:
            try:
                self._ws_client.close()
            except Exception:
                pass
            self._ws_client = None

    def _close_cbk(self):
        if self.state == BACKEND_SESSION_STATE_DESTROYED:
            return
        log.info('Backend session {} is closed by under network'.format(self.session_id))
        self._ws_client = None
        self.destroy()


    def _auto_destroy_routine(self):
        log.info('Backend session {} is auto destroyed'.format(self.session_id))
        self._auto_destroy_greenlet = None
        self.destroy()

    def _recv_msg_cbk(self, msg):
        try:
            if 'transaction' in msg:
                transaction = self._transactions.get(msg['transaction'], None)
                if transaction:
                    transaction.response = msg
                else:
                    log.debug('Receive Async Response {} from Janus server: {}'.format(msg, self.url))
            elif msg['janus'] == 'timeout':
                log.debug('Receive session timeout from Janus server: {}'.format(self.url))
                self.destroy()
            elif msg['janus'] == 'detached':
                log.debug('Receive async event {} from Janus server: {}'.format(msg, self.url))
                handle = self._handles.pop(msg['sender'], None)
                if handle:
                    handle.on_close()
            elif 'sender' in msg:
                log.debug('Receive async event {} from Janus server: {}'.format(msg, self.url))
                handle = self._handles.get(msg['sender'], None)
                if handle:
                    handle.on_async_event(msg)
            else:
                log.warn('Receive a invalid message {} on session {} for server {}'.format(msg, self.session_id, self.url))
        except Exception:
            log.exception('Received a malformat msg {}'.format(msg))

    def _genrate_new_tid(self):
        tid = str(random_uint64())
        while tid in self._transactions:
            tid = str(random_uint64())
        return tid

    def _get_session_timeout(self):
        response = self.send_request(create_janus_msg('info'))
        if response['janus'] == 'server_info':
            return response.get('session-timeout', 30)
        elif response['janus'] == 'error':
            raise JanusCloudError(
                'Create session error for Janus server {} with reason {}'.format(self.url, response['error']['reason']),
                response['error']['code'])
        else:
            raise JanusCloudError(
                'Create session error for Janus server: {} with invalid response {}'.format(self.url, response),
                JANUS_ERROR_BAD_GATEWAY)

    def _create_janus_session(self):
        response = self.send_request(create_janus_msg('create'))
        if response['janus'] == 'success':
            return response['data']['id']
        elif response['janus'] == 'error':
            raise JanusCloudError(
                'Create session error for Janus server {} with reason {}'.format(self.url, response['error']['reason']),
                response['error']['code'])
        else:
            raise JanusCloudError(
                'Create session error for Janus server: {} with invalid response {}'.format(self.url, response),
                JANUS_ERROR_BAD_GATEWAY)

    def _keepalive_routine(self):
        gevent.sleep(self._keepalive_interval)
        keepalive_msg = create_janus_msg('keepalive')
        while self.state == BACKEND_SESSION_STATE_ACTIVE:
            try:
                # if there is no handle existed and auto destroy is enabled, just schedule the destroy route
                if not self._handles:
                    if self._auto_destroy and self._auto_destroy_greenlet is None:
                        self._auto_destroy_greenlet = gevent.spawn_later(self._auto_destroy, self._auto_destroy_routine)

                self.send_request(keepalive_msg, ignore_ack=False)

            except Exception as e:
                log.exception('Keepalive failed for backend session {}'.format(self.url))
                self.destroy()
            else:
                gevent.sleep(self._keepalive_interval)


_sessions = {}

_api_secret = ''

def get_cur_sessions():
    return list(_sessions.values())

def get_backend_session(server_url, auto_destroy=False):
    session = _sessions.get(server_url)
    if session is None:
        # create new session
        session = \
            BackendSession(server_url, auto_destroy=auto_destroy, api_secret=_api_secret)
        try:
            session.init()
        except Exception as e:
            session.destroy()
            raise JanusCloudError('Failed to create backend session for Janus server: {} for reason:{}'
                                  .format(server_url, str(e)), JANUS_ERROR_BAD_GATEWAY)

    # wait for session init complete
    while session.state != BACKEND_SESSION_STATE_ACTIVE:
        if session.state == BACKEND_SESSION_STATE_CREATING:
            gevent.sleep(0.01)
        elif session.state == BACKEND_SESSION_STATE_DESTROYED:
            raise JanusCloudError('Failed to create backend session for Janus server: {}'.format(server_url),
                                    JANUS_ERROR_BAD_GATEWAY)
    return session


def set_api_secret(api_secret):
    global _api_secret
    _api_secret = api_secret

if __name__ == '__main__':
    from januscloud.common.logger import test_config
    test_config(debug=True)
    session = get_backend_session('ws://127.0.0.1:8188', auto_destroy=5)
    print('create session successful')
    handle = session.attach_handle('janus.plugin.echotest1')
    gevent.sleep(5)
    handle.detach()
    gevent.sleep(5)
    print('destroy session')
    session.destroy()
    gevent.sleep(20)






