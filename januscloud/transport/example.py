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
        gevent.spawn(self.receive_loop)

    def receive_loop(self):
        while True:
            msg = self._conn.receive_msg()
            if not msg:
                log.info('Server connection closed')
                break
            else:
                self._conn.send_msg(msg)


class Client(object):

    def __init__(self, url):
        self._conn = WSClient(url)
        self._conn.connect()
        gevent.spawn(self.receive_loop)
        gevent.spawn(self.send_loop)

    def send_loop(self):
        for x in range(5):
            time.sleep(1 + 1/random.randint(1, 10))
            self._conn.send_msg({'msg_id': x})
        time.sleep(2)
        self._conn.close()

    def receive_loop(self):
        while True:
            msg = self._conn.receive_msg()
            if not msg:
                log.info('Client closed')
                break


if __name__ == '__main__':
    test_config(debug=True)
    gevent.spawn(WSServer('127.0.0.1:9999', server_factory).server_forever)
    time.sleep(1)
    c = Client('ws://127.0.0.1:9999')
    time.sleep(60)





