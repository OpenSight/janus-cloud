# -*- coding: utf-8 -*-
import urllib.parse
import json
import logging
from ws4py.websocket import WebSocket
from ws4py.server.geventserver import WSGIServer
from ws4py.server.wsgiutils import WebSocketWSGIApplication
from ws4py.client.geventclient import WebSocketClient
from gevent.lock import RLock
from gevent.queue import Queue

log = logging.getLogger(__name__)


class WSServerTransport(WebSocket):

    CONNECTED_CBK = None
    DEFAULT_ENCODER = json.JSONEncoder()
    DEFAULT_DECODER = json.JSONDecoder()

    def __init__(self, *args, **kwargs):
        super(WSServerTransport, self).__init__(*args, **kwargs)
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
        log.debug("Received message from {0}: {1}".format(self, message))
        if message.is_text:
            try:
                self.messages.put_nowait(self.DEFAULT_DECODER.decode(str(message)))
            except Exception:
                log.exception('Failed to put message on {0}, closing'.format(self))
                self.messages.queue.clear()
                self.messages.put(StopIteration)

    def receive(self, block=True):
        # If the websocket was terminated and there are no messages
        # left in the queue, return None immediately otherwise the client
        # will block forever
        if self.terminated and self.messages.empty():
            return None
        message = self.messages.get(block=block)
        if message is StopIteration:
            return None
        return message

    def send(self, obj, binary=False):
        log.debug("Sending message on {0}: {1}".format(self, obj))
        super(WSServerTransport, self).send(self.DEFAULT_ENCODER.encode(obj), binary=binary)

    def close(self, code=1000, reason=''):
        log.info("Shutting down {0}".format(self))
        self.messages.put(StopIteration)
        super(WSServerTransport, self).close()


class WSServer(object):

    def __init__(self, listen, connected_cbk):
        WSServerTransport.CONNECTED_CBK = connected_cbk
        self._listen = listen

    def server_forever(self):
        server = WSGIServer(self._listen, WebSocketWSGIApplication(handler_cls=WSServerTransport), log=logging.getLogger('websocket server'))
        log.info("Starting websocket server on {0}".format(self._listen))
        server.serve_forever()


class WSClient(WebSocketClient):

    APP_FACTORY = None
    DEFAULT_ENCODER = json.JSONEncoder()
    DEFAULT_DECODER = json.JSONDecoder()

    def __init__(self, url):
        # patch socket.sendall to protect it with lock,
        # in order to prevent sending data from multiple greenlets concurrently
        WebSocketClient.__init__(self, url)
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

    def received_message(self, message):
        if message.is_text:
            try:
                self.messages.put_nowait(self.DEFAULT_DECODER.decode(str(message)))
            except Exception:
                log.exception('Failed to put message on {0}, closing'.format(self))
                self.messages.queue.clear()
                self.messages.put(StopIteration)

    def send(self, obj, binary=False):
        log.debug("Sending message on {0}: {1}".format(self, obj))
        super(WSClient, self).send(self.DEFAULT_ENCODER.encode(obj), binary=binary)

    def close(self, code=1000, reason=''):
        log.info("Shutting down {0}".format(self))
        self.messages.put(StopIteration)
        super(WSClient, self).close()
