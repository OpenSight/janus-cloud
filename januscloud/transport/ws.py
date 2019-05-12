# -*- coding: utf-8 -*-
import urllib.parse
import logging
from gevent.event import Event
from ws4py.websocket import WebSocket
from ws4py.client.geventclient import WebSocketClient
from gevent.lock import RLock

log = logging.getLogger(__name__)


class WSServerTransport(WebSocket):

    APP_FACTORY = None

    def __init__(self, *args, **kwargs):
        super(WSServerTransport, self).__init__(*args, **kwargs)
        self._app = None

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
                query = urllib.parse.parse_qs(self.environ['QUERY_STRING'])
            for key, value in query.iteritems():
                query[key] = value[0]
            self._app = self.APP_FACTORY(self, query)
        except Exception:
            log.exception('Failed to create app for {0}'.format(self))
            raise

    def closed(self, code, reason=None):
        app, self._app = self._app, None
        if app:
            app.on_close()

    def received_message(self, message):
        log.debug("Received message from {0}: {1}".format(self, message))
        if self._app:
            self._app.on_received_packet(message)
        else:
            log.warning('Websocket server app already closed')

    def send_packet(self, data):
        log.debug("Sending message on {0}: {1}".format(self, data))
        self.send(data)

    def force_shutdown(self):
        # called by the upper layer, and no callback will be possible when closed
        log.info("Shutting down {0}".format(self))
        self._app = None
        self.close()
        log.info('Closed {0}'.format(self))


class WSClientTransport(WebSocketClient):

    APP_FACTORY = None

    def __init__(self, url):
        self._close_event = Event()
        # patch socket.sendall to protect it with lock,
        # in order to prevent sending data from multiple greenlets concurrently
        WebSocketClient.__init__(self, url)
        self._app = None
        lock = RLock()
        _sendall = self.sock.sendall

        def sendall(data):
            lock.acquire()
            try:
                _sendall(data)
            except:
                raise
            finally:
                lock.release()
        self.sock.sendall = sendall

    def connect(self):
        super(WSClientTransport, self).connect()
        self._app = self.APP_FACTORY(self)
        log.info("Connected to websocket server {0}".format(self.url))

    def closed(self, code, reason=None):
        app, self._app = self._app, None
        if app:
            app.on_close()
        self._close_event.set()

    def received_message(self, message):
        log.debug("Received message {0}".format(message))
        if self._app:
            self._app.on_received_packet(message)
        else:
            log.warning('Websocket client app already closed')

    def send_packet(self, data):
        log.debug("Sending message {0}".format(data))
        self.send(data)

    def force_shutdown(self):
        # called by the upper layer, and no callback will be possible when closed
        self._app = None
        self.close()
        self._close_event.set()
        log.info('Websocket client closed')

    def wait_close(self):
        self._close_event.wait()

    def app(self):
        return self._app
