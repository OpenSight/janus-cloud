# -*- coding: utf-8 -*-
import urllib.parse
import json
import logging
from ws4py.websocket import WebSocket
from ws4py.server.geventserver import WSGIServer
from ws4py.server.wsgiutils import WebSocketWSGIApplication
from ws4py.client.geventclient import WebSocketClient
from gevent.lock import RLock
from gevent.queue import Queue, Full

log = logging.getLogger(__name__)


class WSServerConn(WebSocket):

    CONNECTED_CBK = None
    DEFAULT_ENCODER = json.JSONEncoder()
    DEFAULT_DECODER = json.JSONDecoder()

    def __init__(self, *args, **kwargs):
        self._msg_encoder = kwargs.pop('msg_encoder', None) or self.DEFAULT_ENCODER
        self._msg_decoder = kwargs.pop('msg_decoder', None) or self.DEFAULT_DECODER
        super(WSServerConn, self).__init__(*args, **kwargs)
        self.messages = Queue(maxsize=32)

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
            self.CONNECTED_CBK(**query)
        except Exception:
            log.exception('Failed to create app for {0}'.format(self))
            raise

    def closed(self, code, reason=None):
        self.messages.put(StopIteration)

    def received_message(self, message):
        if message.is_text:
            log.debug('Received message from {0}: {1}'.format(self, message))
            try:
                self.messages.put_nowait(self._msg_decoder.decode(str(message)))
            except Exception:
                log.exception('Failed to put message on {0}, closing'.format(self))
                self.messages.queue.clear()
                self.messages.put(StopIteration)

    def send_msg(self, msg):
        """
        send message
        :param msg: object which can be encoded by msg_encoder (by default json encoder)
        :return:
        """
        if self.terminated:
            raise Exception('Already closed: {0}'.format(self))
        self.send(self._msg_encoder.encode(msg), binary=False)
        log.debug("Sent message to {0}: {1}".format(self, msg))

    def receive_msg(self):
        """
        receive message
        :return: decoded message, if None, indicates connection closed
        """
        if self.terminated and self.messages.empty():
            return None
        message = self.messages.get(block=True)
        if message is StopIteration:
            return None
        return message

    def close(self, code=1000, reason=''):
        """
        close connection
        :param code:
        :param reason:
        :return:
        """
        if not self.terminated:
            log.info("Shutting down {0}".format(self))
            try:
                self.messages.put(StopIteration, timeout=10)
            except Full:
                self.messages.queue.clear()
                self.messages.put(StopIteration)
            super(WSServerConn, self).close()


class WSServer(object):

    def __init__(self, listen, connected_cbk):
        WSServerConn.CONNECTED_CBK = connected_cbk
        self._listen = listen
        self._server = WSGIServer(self._listen, WebSocketWSGIApplication(handler_cls=WSServerConn), log=logging.getLogger('websocket server'))

    def server_forever(self):
        log.info("Starting websocket server on {0}".format(self._listen))
        self._server.serve_forever()

    def stop(self):
        self._server.stop()


class WSClient(WebSocketClient):

    APP_FACTORY = None
    DEFAULT_ENCODER = json.JSONEncoder()
    DEFAULT_DECODER = json.JSONDecoder()

    def __init__(self, url, msg_encoder=None, msg_decoder=None):
        # patch socket.sendall to protect it with lock,
        # in order to prevent sending data from multiple greenlets concurrently
        WebSocketClient.__init__(self, url)
        self._msg_encoder = msg_encoder or self.DEFAULT_ENCODER
        self._msg_decoder = msg_decoder or self.DEFAULT_DECODER
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
        self.messages = Queue(maxsize=32)

    def __str__(self):
        return 'websocket client connection with {0}'.format(self.peer_address)

    def received_message(self, message):
        if message.is_text:
            log.debug('Received message from {0}: {1}'.format(self, message))
            try:
                self.messages.put_nowait(self._msg_decoder.decode(str(message)))
            except Exception:
                log.exception('Failed to put message on {0}, closing'.format(self))
                self.messages.queue.clear()
                self.messages.put(StopIteration)

    def send_msg(self, msg):
        """
        send message
        :param msg: object which can be encoded by msg_encoder (by default json encoder)
        :return:
        """
        if self.client_terminated:
            raise Exception('Already closed: {0}'.format(self))
        self.send(self._msg_encoder.encode(msg), binary=False)
        log.debug("Sent message to {0}: {1}".format(self, msg))

    def receive_msg(self):
        """
        receive message
        :return: decoded message, if None, indicates connection closed
        """
        msg = self.receive(block=True)
        if msg:
            return msg

    def close(self, code=1000, reason=''):
        """
        close connection
        :param code:
        :param reason:
        :return:
        """
        if not self.client_terminated:
            log.info("Shutting down {0}".format(self))
            try:
                self.messages.put(StopIteration, timeout=10)
            except Full:
                self.messages.queue.clear()
                self.messages.put(StopIteration)
            super(WSClient, self).close()
