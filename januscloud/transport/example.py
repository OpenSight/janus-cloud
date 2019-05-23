# -*- coding: utf-8 -*-
import gevent.monkey
gevent.monkey.patch_all()
import random
import time
import logging
from januscloud.transport.ws import WSServer, WSClient
from januscloud.common.logger import test_config

log = logging.getLogger(__name__)


def server_factory(conn, **params):
    """
    :param conn:
    :param params: URL queries
    :return:
    """
    return EchoServer(conn)


class EchoServer(object):

    def __init__(self, conn):
        self._conn = conn
        self._conn.register_recv_msg_cbk(self._on_recv_msg)
        self._conn.register_close_cbk(self._on_closed)

    def _on_recv_msg(self, msg):
        log.info('Msg received: {0}'.format(msg))
        self._conn.send_msg(msg, timeout=10)

    def _on_closed(self):
        pass


class Client(object):

    def __init__(self, url):
        self._conn = WSClient(url, self._on_recv_msg, self._on_closed, protocols=('janus-protocol',))
        gevent.spawn(self.send_loop)

    def _on_recv_msg(self, msg):
        pass

    def _on_closed(self):
        log.info('Client closed')

    def send_loop(self):
        for x in range(5):
            time.sleep(1 + 1/random.randint(1, 10))
            self._conn.send_msg({'msg_id': x}, 10)
            log.info('Msg sent')
        time.sleep(2)
        self._conn.close()


if __name__ == '__main__':
    test_config(debug=True)
    gevent.spawn(WSServer('127.0.0.1:9999', server_factory).server_forever)
    time.sleep(1)
    c = Client('ws://127.0.0.1:9999')
    time.sleep(60)
    """
    # example to connect to Janus
    client = WSClient("ws://192.168.0.221/ws/media", protocols=('janus-protocol',))
    client.connect()
    client.send_msg({'janus': 'info', 'transaction': 'abcdef'})
    print(client.receive_msg())
    client.close()
    """
