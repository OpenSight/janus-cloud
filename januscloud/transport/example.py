# -*- coding: utf-8 -*-
import gevent.monkey
gevent.monkey.patch_all()
import random
import time
import logging
from januscloud.transport.ws import WSServer, WSClient
from januscloud.common.logger import test_config
from januscloud.core.request import RequestHandler

log = logging.getLogger(__name__)


class EchoServer(RequestHandler):

    def incoming_request(self, request):
        """ handle the request from the transport module

        Args:
            request: the request to handle

        Returns:
            a dict to response to the initial client

        """
        log.info('Msg received: {0}'.format(request.message))
        return request.message

    def transport_gone(self, transport_session):
        """ notify transport session is closed by the transport module """
        pass


class Client(object):

    def __init__(self, url):
        self._conn = WSClient(url, self._on_recv_msg, self._on_closed, protocols=('janus-protocol',))
        gevent.spawn(self.send_loop)

    def _on_recv_msg(self, msg):
        log.info('response received: {0}'.format(msg))

    def _on_closed(self):
        log.info('Client closed')

    def send_loop(self):
        for x in range(5):
            time.sleep(1 + 1/random.randint(1, 10))
            self._conn.send_message({'janus': 'abc', 'transaction': str(x)})
            log.info('Msg sent')
        time.sleep(2)
        self._conn.close()


if __name__ == '__main__':

    test_config(debug=True)
    """
    gevent.spawn(WSServer('127.0.0.1:9999', EchoServer(), keyfile='../../certs/mycert.key', certfile='../../certs/mycert.pem').server_forever)
    time.sleep(1)
    c = Client('wss://127.0.0.1:9999')
    time.sleep(60)
    """
    # example to connect to Janus
    def incoming_msg(msg):
        print('received message:')
        print(msg)

    def on_closed():
        print('Client closed')
    client = WSClient("ws://127.0.0.1:8288", incoming_msg, on_closed, protocols=('janus-protocol',))
    client.send_message({'janus': 'info', 'transaction': 'abcdef'})
    print('after send_message')
    time.sleep(5)
    print('before close')
    client.close()
    print('after close, client terminated:{}'.format(client.terminated))
    time.sleep(1)

    print('after sleep, client terminated:{}'.format(client.terminated))

    time.sleep(5)


