# -*- coding: utf-8 -*-

import logging
import time
import importlib
import gevent
import uuid


from januscloud.common.error import JanusCloudError, JANUS_ERROR_SERVICE_UNAVAILABLE, JANUS_ERROR_BAD_GATEWAY
from januscloud.common.utils import random_uint64, create_janus_msg, get_host_ip, get_monotonic_time
from januscloud.core.backend_server import JANUS_SERVER_STATUS_ABNORMAL, JANUS_SERVER_STATUS_NORMAL, \
    JANUS_SERVER_STATUS_MAINTENANCE, JANUS_SERVER_STATUS_HWM
from januscloud.core.backend_session import BackendTransaction, get_cur_sessions
from januscloud.sentinel.process_mngr import PROC_RUNNING, PROC_STATUS_TEXT
from januscloud.transport.ws import WSClient

log = logging.getLogger(__name__)


class JanusServer(object):

    def __init__(self, server_name, server_ip='127.0.0.1',
                 public_ip='', ws_port=8188, admin_ws_port=0,
                 pingpong_interval=5, statistic_interval=10, request_timeout=10,
                 hwm_threshold=0, admin_secret='',
                 location='', isp=''):
        self.server_name = server_name
        if self.server_name is None or self.server_name == '':
            self.server_name = str(uuid.uuid1())   # for empty, use uuid as server name
        self.server_local_ip = server_ip
        if server_ip == '':
            self.server_local_ip = '127.0.0.1'
        self.server_public_ip = public_ip
        if public_ip == '127.0.0.1' or public_ip == '':
            self.server_public_ip = get_host_ip()
        self.ws_port = ws_port
        self.session_num = -1   # unknown initially
        self.handle_num = -1    # unknown initially
        self.start_time = 0
        self.status = JANUS_SERVER_STATUS_ABNORMAL
        self.location = location
        self.isp = isp
        self._in_maintenance = False
        self._admin_ws_port = admin_ws_port
        self._hwm_threshold = hwm_threshold
        self._admin_secret = admin_secret

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
        self._listeners = []

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

        self._poll_greenlet = None
        self._statistic_greenlet = None
        self.session_num = -1
        self.handle_num = -1
        self.status = JANUS_SERVER_STATUS_ABNORMAL
        self._listeners.clear()

    @property
    def url(self):
        return 'ws://{}:{}'.format(self.server_local_ip, self.ws_port)

    @property
    def admin_url(self):
        return 'ws://{}:{}'.format(self.server_local_ip, self._admin_ws_port)

    @property
    def public_url(self):
        return 'ws://{}:{}'.format(self.server_public_ip, self.ws_port)

    def set_status(self, new_status):
        if self._has_destroy:
            return

        if self._in_maintenance:
            return  # ignore state change when maintaining
        old_status = self.status
        self.status = new_status

        if old_status != new_status:
            log.info('janus server({}) status changed to {}'.format(self.url, new_status))
            for listener in self._listeners:
                if hasattr(listener, 'on_status_changed') and callable(listener.on_status_changed):
                    listener.on_status_changed(new_status)

    def set_stat(self, session_num, handle_num):
        if self._has_destroy:
            return
        stat_updated = False
        if self.session_num != session_num:
            stat_updated = True
            self.session_num = session_num
        if self.handle_num != handle_num:
            stat_updated = True
            self.handle_num = handle_num
        if stat_updated:
            log.info('janus server({}) stat updated: session_num {}, handle_num {}'.format(
                self.url, self.session_num, self.handle_num))
            if session_num >= 0 or handle_num >= 0:
                for listener in self._listeners:
                    if hasattr(listener, 'on_stat_updated') and callable(listener.on_stat_updated):
                        listener.on_stat_updated()

    def register_listener(self, listener):
        self._listeners.append(listener)

    def start_maintenance(self):
        if self._in_maintenance:
            return    # already under maintenance
        self.set_status(JANUS_SERVER_STATUS_MAINTENANCE)
        self._in_maintenance = True

    def stop_maintenance(self):
        if not self._in_maintenance:
            return
        self._in_maintenance = False
        self.set_status(JANUS_SERVER_STATUS_ABNORMAL)

    def pingpong(self):
        try:
            if self._ws_client is None:
                self._ws_client = WSClient(self.url,
                                           self._recv_msg_cbk, self._close_cbk, protocols=['janus-protocol'])
            ping_start_ts = get_monotonic_time()
            self.send_request(self._ws_client, create_janus_msg('ping'))
            ping_end_ts = get_monotonic_time()
            ping_latency = ping_end_ts - ping_start_ts
            if self._hwm_threshold and ping_latency > self._hwm_threshold:
                self.set_status(JANUS_SERVER_STATUS_HWM)
            else:
                self.set_status(JANUS_SERVER_STATUS_NORMAL)

        except Exception as e:
            if self._has_destroy:
                return
            log.warning('Poll janus server({}) failed: {}'.format(self.url, e))
            self.set_status(JANUS_SERVER_STATUS_ABNORMAL)
            if self._ws_client:
                try:
                    self._ws_client.close()
                except Exception:
                    pass
                self._ws_client = None

    def _get_self_session_ids(self):
        self_session_ids = set()
        self_sessions = get_cur_sessions()
        for session in self_sessions:
            if session.session_id:
                self_session_ids.add(session.session_id)
        return self_session_ids

    def query_stat(self):
        try:
            if self._admin_ws_client is None:
                self._admin_ws_client = WSClient(self.admin_url, self._recv_msg_cbk, None, protocols=['janus-admin-protocol'])

            common_args = {}
            if self._admin_secret:
                common_args['admin_secret'] = self._admin_secret

            self_session_ids = self._get_self_session_ids()

            response = self.send_request(self._admin_ws_client, create_janus_msg('list_sessions', **common_args))
            sessions = response.get('sessions', [])
            handles = []
            for session_id in sessions:
                if session_id in self_session_ids:
                    # for self session, not add to stat
                    continue
                response = self.send_request(self._admin_ws_client,
                                             create_janus_msg('list_handles', session_id=session_id, **common_args))
                handles.extend(response.get('handles', []))
            self.set_stat(session_num=len(sessions), handle_num=len(handles))
        except Exception as e:
            if self._has_destroy:
                return
            log.warning('Calculate stat of janus server({}) failed: {}'.format(self.admin_url, e))
            self.set_stat(session_num=-1, handle_num=-1)   # stop post statistic
            if self._admin_ws_client:
                try:
                    self._admin_ws_client.close()
                except Exception:
                    pass
                self._admin_ws_client = None

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
            if response['janus'] == 'error':
                raise JanusCloudError(response['error']['reason'], response['error']['code'])
            return response
        finally:
            self._transactions.pop(transaction_id, None)

    def _close_cbk(self):
        if self._has_destroy:
            return
        log.info('WebSocket closed for Janus server {} '.format(self.url))
        self.set_status(JANUS_SERVER_STATUS_ABNORMAL)

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
            self.query_stat()

    def on_process_status_change(self, watcher):
        log.debug('on_process_status_change is called, new status: {}({})'.format(
            watcher.process_status, PROC_STATUS_TEXT[watcher.process_status]))
        if watcher.process_status == PROC_RUNNING:
            self.start_time = time.time()
        else:
            self.start_time = 0


if __name__ == '__main__':
    pass






