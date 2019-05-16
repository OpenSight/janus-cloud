# -*- coding: utf-8 -*-
import gevent.monkey
gevent.monkey.patch_all()
import random
import time
from januscloud.transport.ws import WSServer, WSClient
from januscloud.common.logger import test_config


def server_factory(conn, **params):
    cid = params['cid']
    return Server(conn, cid)


class Server(object):

    def __init__(self, conn, cid):
        self._conn = conn
        self._cid = cid

    def receive_loop(self):
        while True:
            msg = self._conn.receive()
            if not msg:
                break
            else:
                self._conn.send(msg)
        print('Closed server {0}'.format(self._cid))


class Client(object):

    def __init__(self, url, cid):
        self._conn = WSClient(url)
        self._conn.connect()
        self._cid = cid
        gevent.spawn(self.receive_loop)
        gevent.spawn(self.send_loop)

    def send_loop(self):
        for x in range(random.randint(0, 20)):
            time.sleep(1 + 1/random.randint(1, 10))
            self._conn.send({'msg_id': x})
        time.sleep(2)
        self._conn.close()

    def receive_loop(self):
        while True:
            msg = self._conn.receive()
            if not msg:
                break
        print('Closed client {0}'.format(self._cid))


if __name__ == '__main__':
    test_config(debug=True)
    gevent.spawn(WSServer('127.0.0.1:9999', server_factory).server_forever)
    time.sleep(1)
    print('Starting client')
    c = Client('ws://127.0.0.1:9999?cid={0}'.format(0), 0)
    time.sleep(60)





