# -*- coding: utf-8 -*-

import logging
import time
import importlib
import gevent


from januscloud.common.error import JanusCloudError, JANUS_ERROR_SERVICE_UNAVAILABLE
from januscloud.common.utils import random_uint64, create_janus_msg, get_host_ip
from januscloud.proxy.core.backend_server import JANUS_SERVER_STATUS_ABNORMAL, JANUS_SERVER_STATUS_NORMAL, \
    JANUS_SERVER_STATUS_MAINTENANCE
from januscloud.proxy.core.backend_session import BackendTransaction
from januscloud.sentinel.process_mngr import PROC_RUNNING
from januscloud.transport.ws import WSClient

log = logging.getLogger(__name__)


class JanusServer(object):

    def __init__(self, server_name, server_ip, ws_port, admin_ws_port=0,
                 pingpong_interval=5, statistic_interval=10, request_timeout=10):
        self.server_name = server_name
        self.server_local_ip = server_ip
        self.server_public_ip = server_ip
        if server_ip == '127.0.0.1':
            self.server_public_ip = get_host_ip()
        self.ws_port = ws_port
        self.session_num = 0
        self.handle_num = 0
        self.start_time = 0
        self.state = JANUS_SERVER_STATUS_ABNORMAL
        self._in_maintenance = False
        self._admin_ws_port = admin_ws_port
        self._ws_client = None
        self._admin_ws_client = None
        self._transactions = {}
        self._has_destroy = False
        self._poll_greenlet = gevent.spawn(self._poll_routine)
        self._poll_interval = pingpong_interval
        self._statistic_greenlet = None
        if self._admin_ws_port:
            self._statistic_greenlet = gevent.spawn(self._statistic_routine)
        self._statistic_interval = statistic_interval
        self._request_timeout = request_timeout
        self._state_change_cbs = []

    def destroy(self):
        if self._has_destroy:
            return
        self._has_destroy = True

        if self._ws_client:
            try:
                self._ws_client.close()
            except Exception:
                pass
            self._ws_client = None

        if self._admin_ws_client:
            try:
                self._admin_ws_client.close()
            except Exception:
                pass
            self._admin_ws_client = None

        self._proc_watcher = None
        self._poll_greenlet = None
        self._statistic_greenlet = None
        self.session_num = 0
        self.handle_num = 0
        self.state = JANUS_SERVER_STATUS_ABNORMAL

    @property
    def url(self):
        return 'ws://{}:{}'.format(self.server_local_ip, self.ws_port)

    @property
    def admin_url(self):
        return 'ws://{}:{}'.format(self.server_local_ip, self._admin_ws_port)

    @property
    def public_url(self):
        return 'ws://{}:{}'.format(self.server_public_ip, self.ws_port)

    def update_state(self, new_state):
        if self._in_maintenance:
            return  # ignore state change when maintaining
        old_state = self.state
        self.state = new_state
        if old_state != new_state:
            for cb in self._state_change_cbs:
                cb(new_state)

    def register_state_change_callback(self, cb):
        self._state_change_cbs.append(cb)

    def start_maintenance(self):
        if self._in_maintenance:
            return    # already under maintenance
        self.update_state(JANUS_SERVER_STATUS_MAINTENANCE)
        self._in_maintenance = True

    def stop_maintenance(self):
        if not self._in_maintenance:
            return
        self._in_maintenance = False
        self.update_state(JANUS_SERVER_STATUS_ABNORMAL)

    def pingpong(self):
        try:
            if self._ws_client is None:
                self._ws_client = WSClient(self.url,
                                           self._recv_msg_cbk, self._close_cbk(), protocols=['janus-protocol'])

            self.send_request(self._ws_client, create_janus_msg('ping'), )

            self.update_state(JANUS_SERVER_STATUS_NORMAL)

        except Exception as e:
            log.warning('Poll janus server({}) failed: {}'.format(self.url, e))
            self.update_state(JANUS_SERVER_STATUS_ABNORMAL)
            if self._ws_client:
                try:
                    self._ws_client.close()
                except Exception:
                    pass
                self._ws_client = None

    def update_statics(self):
        try:
            if self._admin_ws_client is None:
                self._admin_ws_client = WSClient(self.admin_url, self._recv_msg_cbk, None, protocols=['janus-admin-protocol'])

        except Exception as e:
            log.warning('Poll janus server({}) failed: {}'.format(self.url, e))
            if self._admin_ws_client:
                try:
                    self._admin_ws_client.close()
                except Exception:
                    pass
                self._ws_client = None

    def send_request(self, client, msg, ignore_ack=True):

        if self._has_destroy:
            raise JanusCloudError('Janus server already destory',
                                  JANUS_ERROR_SERVICE_UNAVAILABLE)
        if client is None:
            raise JanusCloudError('websocket client not ready',
                                  JANUS_ERROR_SERVICE_UNAVAILABLE)

        transaction_id = self._generate_new_tid()
        send_msg = dict.copy(msg)
        send_msg['transaction'] = transaction_id
        transaction = BackendTransaction(transaction_id, send_msg, url=client.url, ignore_ack=ignore_ack)

        try:
            self._transactions[transaction_id] = transaction
            log.debug('Send Request {} to Janus server: {}'.format(send_msg, client.url))
            client.send_message(send_msg)
            response = transaction.wait_response(timeout=self._request_timeout)
            log.debug('Receive Response {} from Janus server: {}'.format(response, client.url))
            return response
        finally:
            self._transactions.pop(transaction_id, None)

    def _close_cbk(self):
        if self._has_destroy:
            return
        log.info('WebSocket closed for Janus server {} '.format(self.url))
        self.update_state(JANUS_SERVER_STATUS_ABNORMAL)

    def _recv_msg_cbk(self, msg):
        try:
            if 'transaction' in msg:
                transaction = self._transactions.get(msg['transaction'], None)
                if transaction:
                    transaction.response = msg
            else:
                log.warning('Receive a invalid message {} for server {}'.format(msg, self.url))
        except Exception:
            log.exception('Received a malformat msg {}'.format(msg))

    def _generate_new_tid(self):
        tid = str(random_uint64())
        while tid in self._transactions:
            tid = str(random_uint64())
        return tid

    def _poll_routine(self):
        while not self._has_destroy:
            gevent.sleep(self._poll_interval)
            if self._has_destroy:
                break
            self.pingpong()

    def _statistic_routine(self):
        while not self._has_destroy:
            gevent.sleep(self._statistic_interval)
            if self._has_destroy:
                break
            self.update_statics()

    def on_process_status_change(self, watcher):
        if watcher.process_status == PROC_RUNNING:
            self.start_time = time.time()

if __name__ == '__main__':
    pass






