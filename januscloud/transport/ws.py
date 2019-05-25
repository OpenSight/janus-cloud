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
from januscloud.proxy.core.request import Request

log = logging.getLogger(__name__)


class WSServerConn(WebSocket):

    DEFAULT_ENCODER = json.JSONEncoder()
    DEFAULT_DECODER = json.JSONDecoder()
    DEFAULT_MSG_HANDLE_THREAD_POOL_SIZE = 8

    def __init__(self, *args, **kwargs):
        self._msg_encoder = kwargs.pop('msg_encoder', None) or self.DEFAULT_ENCODER
        self._msg_decoder = kwargs.pop('msg_decoder', None) or self.DEFAULT_DECODER
        self._recv_msg_cbk = None
        self._closed_cbk = None
        super(WSServerConn, self).__init__(*args, **kwargs)

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
        if self._closed_cbk:
            self._closed_cbk(self)

    def received_message(self, message):
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
        with gevent.Timeout(seconds=timeout):
            self.send(self._msg_encoder.encode(message), binary=False)
        # log.debug("Sent message to {0}: {1}".format(self, msg))

    def session_created(self, session_id=""):
        pass

    def session_over(self, session_id="", timeout=False, claimed=False):
        pass

    def session_claimed(self, session_id=""):
        pass

    def _on_recv_msg_cbk_greenlet_exception(self, g):
        log.error('Failed to handle received msg on {0}: {1}'.format(self, g.exception))
        self.close()


class WSServer(object):

    def __init__(self, listen, request_handler, msg_handler_pool_size=1024, keyfile=None, certfile=None):
        self._msg_handler_pool = Pool(size=msg_handler_pool_size)
        self._request_handler = request_handler
        self._listen = listen
        if keyfile or certfile:
            self._server = WSGIServer(
                self._listen,
                WebSocketWSGIApplication(handler_cls=WSServerConn),
                log=logging.getLogger('websocket server'),
                keyfile=keyfile,
                certfile=certfile
            )
        else:
            self._server = WSGIServer(
                self._listen,
                WebSocketWSGIApplication(handler_cls=WSServerConn),
                log=logging.getLogger('websocket server'),
            )
        self._server.set_environ(
            {
                'app.recv_msg_cbk': self._async_incoming_msg_handler,
                'app.closed_cbk': self._request_handler.transport_gone,
            }
        )

    def server_forever(self):
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
