# -*- coding: utf-8 -*-
import urllib.parse
import json
import logging
import gevent
from ws4py.websocket import WebSocket
from ws4py.server.geventserver import WSGIServer
from ws4py.server.wsgiutils import WebSocketWSGIApplication
from ws4py.client.geventclient import WebSocketClient
from gevent import Greenlet
from gevent.lock import RLock
from gevent.pool import Pool
from januscloud.core.request import Request
from januscloud.common.utils import get_monotonic_time

log = logging.getLogger(__name__)


class WSServerConn(WebSocket):

    DEFAULT_ENCODER = json.JSONEncoder()
    DEFAULT_DECODER = json.JSONDecoder()
    DEFAULT_MSG_HANDLE_THREAD_POOL_SIZE = 8

    def __init__(self, *args, **kwargs):

        super(WSServerConn, self).__init__(*args, **kwargs)

        json_indent = self.environ.get('json_indent')
        if json_indent == 'indented':
            self._msg_encoder = json.JSONEncoder(indent=3)
        elif json_indent == 'plain':
            self._msg_encoder = json.JSONEncoder(indent=None)
        elif json_indent == 'compact':
            self._msg_encoder = json.JSONEncoder(indent=None, separators=(',', ':'))
        else:
            self._msg_encoder = self.DEFAULT_ENCODER

        self._msg_decoder = self.DEFAULT_DECODER

        self._recv_msg_cbk = None
        self._closed_cbk = None

        # pingpong check mechanism
        self._ping_ts = 0
        self._last_active_ts = 0
        self._pingpong_check_greenlet = None
        self._pingpong_trigger = self.environ.get('pingpong_trigger', 0)
        self._pingpong_timeout = self.environ.get('pingpong_timeout', 0)
        if self._pingpong_trigger:
            if self._pingpong_timeout < 1:
                log.warning('pingpong_timeout cannot be less than 1 sec, adjust it to 1 secs')
                self._pingpong_timeout = 1

    def __str__(self):
        return 'websocket server connection with {0}'.format(self.peer_address)

    def opened(self):
        # patch socket.sendall to protect it with lock,
        # in order to prevent sending data from multiple greenlets concurrently
        lock = RLock()
        _sendall = self.sock.sendall

        def sendall(data):
            lock.acquire()
            try:
                _sendall(data)
            except Exception:
                raise
            finally:
                lock.release()
        self.sock.sendall = sendall

        # start check idle
        if self._pingpong_trigger:
            self._last_active_ts = get_monotonic_time()
            self._pingpong_check_greenlet = gevent.spawn(self._pingpong_check_routine)

        # create app
        try:
            if not self.environ.get('QUERY_STRING'):
                query = {}
            else:
                query = urllib.parse.parse_qs(self.environ['QUERY_STRING'], keep_blank_values=True)
            for key, value in query.items():
                query[key] = value[0]
            self._recv_msg_cbk = self.environ['app.recv_msg_cbk']
            self._closed_cbk = self.environ['app.closed_cbk']
            log.info('Created {0}'.format(self))
        except Exception:
            log.exception('Failed to create app for {0}'.format(self))
            raise

    def closed(self, code, reason=None):
        log.info('Closed {0}: {1}'.format(self, reason))
        self._pingpong_check_greenlet = None
        if self._closed_cbk:
            self._closed_cbk(self)

    def ponged(self, pong):
        self._ping_ts = 0
        self._last_active_ts = get_monotonic_time()

    def received_message(self, message):
        if self._pingpong_trigger:
            self._last_active_ts = get_monotonic_time()
        if message.is_text:
            # log.debug('Received message from {0}: {1}'.format(self, message))
            if self._recv_msg_cbk:
                try:
                    self._recv_msg_cbk(
                        self,
                        self._msg_decoder.decode(str(message)),
                        self._on_recv_msg_cbk_greenlet_exception
                    )
                except Exception:
                    log.exception('Failed to handle received msg on {0}'.format(self))
                    raise

    def send_message(self, message, timeout=30):
        """
        send message
        :param message: object which can be encoded by msg_encoder (by default json encoder)
        :param timeout: send timeout in second, if timeout, gevent.Timeout exception will be raised
        :return:
        """
        if self.server_terminated:
            raise Exception('Already closed: {0}'.format(self))
        if self._pingpong_trigger:
            self._last_active_ts = get_monotonic_time()
        with gevent.Timeout(seconds=timeout):
            self.send(self._msg_encoder.encode(message), binary=False)
        #log.debug("Sent message to {0}: {1}".format(self, self._msg_encoder.encode(message)))

    # transport session interface methods
    def session_created(self, session_id=""):
        pass

    def session_over(self, session_id="", timeout=False, claimed=False):
        pass

    def session_claimed(self, session_id=""):
        pass

    def _on_recv_msg_cbk_greenlet_exception(self, g):
        log.error('Failed to handle received msg on {0}: {1}'.format(self, g.exception))
        self.close()

    def _pingpong_check_routine(self):
        while not self.server_terminated:
            gevent.sleep(1)
            if self.server_terminated:
                break
            now = get_monotonic_time()
            # check pingpong timeout
            if self._ping_ts:
                if now - self._ping_ts > self._pingpong_timeout:
                    log.debug('Close ws connection ({}) because of no pong'. format(self))
                    self.close()
                    break
            # send ping if idle
            if self._last_active_ts and now - self._last_active_ts >= self._pingpong_trigger:
                try:
                    self.ping('')
                    self._last_active_ts = self._ping_ts = get_monotonic_time()
                except Exception as e:
                    log.error('Fail to send ping on {}: {}'.format(self, str(e)))
                    self.close()
                    break


class WSServer(object):

    def __init__(self, listen, request_handler, msg_handler_pool_size=1024, indent='indented',
                 pingpong_trigger=0, pingpong_timeout=0,
                 keyfile=None, certfile=None):
        """
        :param listen: string ip:port
        :param request_handler: instance of januscloud.proxy.core.request:RequestHandler
        :param msg_handler_pool_size:
        :param keyfile:
        :param certfile:
        """
        if msg_handler_pool_size == 0:
            msg_handler_pool_size = None

        self._msg_handler_pool = Pool(size=msg_handler_pool_size)
        self._request_handler = request_handler
        self._listen = listen
        if keyfile or certfile:
            self._server = WSGIServer(
                self._listen,
                WebSocketWSGIApplication(protocols=['janus-protocol'], handler_cls=WSServerConn),
                log=logging.getLogger('websocket server'),
                keyfile=keyfile,
                certfile=certfile
            )
        else:
            self._server = WSGIServer(
                self._listen,
                WebSocketWSGIApplication(protocols=['janus-protocol'], handler_cls=WSServerConn),
                log=logging.getLogger('websocket server'),
            )
        self._server.set_environ(
            {
                'app.recv_msg_cbk': self._async_incoming_msg_handler,
                'app.closed_cbk': self._request_handler.transport_gone,
                'json_indent': indent,
                'pingpong_trigger': pingpong_trigger,
                'pingpong_timeout': pingpong_timeout
            }
        )

    def serve_forever(self):
        log.info("Starting websocket server on {0}".format(self._listen))
        self._server.serve_forever()

    def stop(self):
        self._server.stop()

    def _async_incoming_msg_handler(self, transport_session, message, exception_handler):
        greenlet = Greenlet(
            self._incoming_msg_handler,
            transport_session,
            message,
        )
        greenlet.link_exception(exception_handler)
        self._msg_handler_pool.start(
            greenlet,
            blocking=True
        )

    def _incoming_msg_handler(self, transport_session, message):
        if self._request_handler:
            response = self._request_handler.incoming_request(
                Request(transport_session, message)
            )
            if response:
                transport_session.send_message(response)


class WSClient(WebSocketClient):

    APP_FACTORY = None
    DEFAULT_ENCODER = json.JSONEncoder()
    DEFAULT_DECODER = json.JSONDecoder()
    DEFAULT_MSG_HANDLE_THREAD_POOL_SIZE = 8

    def __init__(self, url, recv_msg_cbk=None, close_cbk=None, protocols=None, msg_encoder=None, msg_decoder=None):
        # patch socket.sendall to protect it with lock,
        # in order to prevent sending data from multiple greenlets concurrently
        WebSocketClient.__init__(self, url, protocols=protocols)
        self._msg_encoder = msg_encoder or self.DEFAULT_ENCODER
        self._msg_decoder = msg_decoder or self.DEFAULT_DECODER
        lock = RLock()
        _sendall = self.sock.sendall
        self._recv_msg_cbk = recv_msg_cbk
        self._close_cbk = close_cbk

        def sendall(data):
            lock.acquire()
            try:
                _sendall(data)
            except Exception:
                raise
            finally:
                lock.release()
        self.sock.sendall = sendall

        self.connect()
        log.info('Created {0}'.format(self))

    def __str__(self):
        return 'websocket client connection with {0}'.format(self.peer_address)

    def received_message(self, message):
        if message.is_text:
            # log.debug('Received message from {0}: {1}'.format(self, message))
            if self._recv_msg_cbk:
                try:
                    self._recv_msg_cbk(self._msg_decoder.decode(str(message)))
                except Exception:
                    log.exception('Failed to handle received msg on {0}'.format(self))
                    raise

    def send_message(self, message, timeout=30):
        """
        send message
        :param message: object which can be encoded by msg_encoder (by default json encoder)
        :param timeout: send timeout in second, if timeout, gevent.Timeout exception will be raised
        :return:
        """
        if self.client_terminated:
            raise Exception('Already closed: {0}'.format(self))
        with gevent.Timeout(seconds=timeout):
            self.send(self._msg_encoder.encode(message), binary=False)
        # log.debug("Sent message to {0}: {1}".format(self, msg))

    def closed(self, code, reason=None):
        log.info('Closed {0}: {1}'.format(self, reason))
        if self._close_cbk:
            self._close_cbk()
