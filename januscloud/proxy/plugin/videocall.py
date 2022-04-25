# -*- coding: utf-8 -*-
import base64
import copy

import logging
import socket

from januscloud.common.utils import error_to_janus_msg, create_janus_msg, get_host_ip
from januscloud.common.error import JanusCloudError, JANUS_ERROR_UNKNOWN_REQUEST, JANUS_ERROR_INVALID_REQUEST_PATH, \
    JANUS_ERROR_BAD_GATEWAY, JANUS_ERROR_CONFLICT, JANUS_ERROR_NOT_IMPLEMENTED, JANUS_ERROR_INTERNAL_ERROR
from januscloud.common.schema import Schema, Optional, DoNotCare, \
    Use, IntVal, Default, SchemaError, BoolVal, StrRe, ListVal, Or, STRING, \
    FloatVal, AutoDel, StrVal
from januscloud.core.backend_session import get_backend_session
from januscloud.core.plugin_base import PluginBase
from januscloud.core.frontend_handle_base import FrontendHandleBase, JANUS_PLUGIN_OK_WAIT
import os.path
from januscloud.common.confparser import parse as parse_config
import time
import gevent
from januscloud.proxy.rest.common import post_view, get_params_from_request
from pyramid.response import Response
import requests

log = logging.getLogger(__name__)

BACKEND_SESSION_AUTO_DESTROY_TIME = 10 # auto destroy the backend session after 10s if no handle for it


JANUS_VIDEOCALL_ERROR_USERNAME_TAKEN = 476
JANUS_VIDEOCALL_ERROR_REGISTER_FIRST = 473
JANUS_VIDEOCALL_ERROR_ALREADY_IN_CALL = 480
JANUS_VIDEOCALL_ERROR_INVALID_ELEMENT = 474
JANUS_VIDEOCALL_ERROR_INVALID_REQUEST = 472
JANUS_VIDEOCALL_ERROR_ALREADY_REGISTERED = 477
JANUS_VIDEOCALL_ERROR_NO_SUCH_USERNAME = 478
JANUS_VIDEOCALL_ERROR_USE_ECHO_TEST = 479
JANUS_VIDEOCALL_ERROR_NO_CALL = 481
JANUS_VIDEOCALL_ERROR_MISSING_SDP = 482

JANUS_VIDEOCALL_API_SYNC_VERSION = 'v0.11.4(2021-09-7)'

JANUS_VIDEOCALL_VERSION = 6
JANUS_VIDEOCALL_VERSION_STRING = '0.0.6'
JANUS_VIDEOCALL_DESCRIPTION = 'This is a a simple video call plugin for Janus-cloud, ' \
                                'which allow two WebRTC peer communicate with each other through Janus server. ' \
                              'Its API is kept sync with videocall of Janus-gateway until ' + \
                              JANUS_VIDEOCALL_API_SYNC_VERSION
JANUS_VIDEOCALL_NAME = 'JANUS VideoCall plugin'
JANUS_VIDEOCALL_AUTHOR = 'opensight.cn'
JANUS_VIDEOCALL_PACKAGE = 'janus.plugin.videocall'


JANUS_VIDEOCALL_API_BASE_PATH = '/plugins/videocall'


INCOMMING_CALL_TIMEOUT = 10

username_schema = Schema({
    'username': StrRe('^\w{1,128}$'),
    DoNotCare(str): object  # for all other key we don't care
})

exists_schema = Schema({
    'username': StrRe('^\w{1,128}$'),
    DoNotCare(str): object  # for all other key we don't care
})

set_schema = Schema({
    'request': StrVal(),
    Optional('audio'): BoolVal(),
    Optional('video'): BoolVal(),
    Optional('bitrate'): IntVal(min=1),
    Optional('record'): BoolVal(),
    Optional('restart'): BoolVal(),
    Optional('filename'): StrVal(),
    Optional('substream'): IntVal(min=0, max=2),
    Optional('temporal'): IntVal(min=0, max=2),
    Optional('fallback'): IntVal(min=0),
    DoNotCare(str): object  # for all other key we we don't care
})

post_videocall_user_schema = Schema({
    'caller_username': StrRe('^\w{1,128}$'),
    'backend_server_url': StrVal(max_len=1024),
    AutoDel(str): object  # for all other key we must delete
})


class VideoCallUser(object):

    def __init__(self, username, handle=None, incall=False, peer_name='', api_url=''):
        self.username = username
        self.incall = incall
        self.peer_name = peer_name
        self.handle = handle
        self.api_url = api_url
        self.utime = time.time()
        self.ctime = time.time()

    def __str__(self):
        return 'Video Call User"{0}"({1})'.format(self.username, self.api_url)


class VideoCallHandle(FrontendHandleBase):

    def __init__(self, handle_id, session, plugin, opaque_id=None, *args, **kwargs):
        super().__init__(handle_id, session, plugin, opaque_id, *args, **kwargs)

        self.videocall_user = None
        self.backend_handle = None
        self._backend_server_url = None
        self._backend_keepalive_interval = 0
        self._pending_candidates = list()
        self._pending_set_body = None
        self._auto_disconnect_greenlet = None

    def detach(self):
        if self._has_destroy:
            return
        super().detach()
        if self._auto_disconnect_greenlet:
            gevent.kill(self._auto_disconnect_greenlet)
            self._auto_disconnect_greenlet = None

        if self.videocall_user:
            # print('user_dao remove')
            self._plugin.user_dao.remove(self.videocall_user)
            self.videocall_user.handle = None
            self.videocall_user = None

        if self.backend_handle:
            backend_handle = self.backend_handle
            self.backend_handle = None
            backend_handle.detach()

    def handle_hangup(self):
        log.debug('handle_hangup for videocall Handle {}'.format(self.handle_id))
        if self.backend_handle:
            self.backend_handle.send_hangup()

    def handle_message(self, transaction, body, jsep=None):
        log.debug('handle_message for videocall handle {}. transaction:{} body:{} jsep:{}'.
                 format(self.handle_id, transaction, body, jsep))

        self._enqueue_async_message(transaction, body, jsep)
        return JANUS_PLUGIN_OK_WAIT, None

    def handle_trickle(self, candidate=None, candidates=None):
        log.debug('handle_trickle for videocall handle {}.candidate:{} candidates:{}'.
                  format(self.handle_id, candidate, candidates))

        if self.videocall_user is None or self.videocall_user.incall == False:
            if candidates:
                self._pending_candidates.extend(candidates)
            if candidate:
                self._pending_candidates.append(candidate)
        else:
            while self.backend_handle is None and self.videocall_user.incall == True:
                # backend handle is building
                gevent.sleep(0.1)
            if self.backend_handle:
                self.backend_handle.send_trickle(candidate=candidate, candidates=candidates)
            else:
                if candidates:
                    self._pending_candidates.extend(candidates)
                if candidate:
                    self._pending_candidates.append(candidate)

    def handle_incoming_call(self, caller_username, backend_server_url):
        if self.videocall_user is None:
            raise JanusCloudError('Register a username first', JANUS_VIDEOCALL_ERROR_REGISTER_FIRST)
        if self.videocall_user.incall:
            raise JanusCloudError('User {} busy'.format(self.videocall_user.username), JANUS_VIDEOCALL_ERROR_ALREADY_IN_CALL)
        self.videocall_user.incall = True
        self.videocall_user.peer_name = caller_username
        self.videocall_user.utime = time.time()
        self._plugin.user_dao.update(self.videocall_user)
        try:
            self._connect_backend(backend_server_url)
        except Exception:
            self._disconnect_backend()
            self.videocall_user.peer_name = ''
            self.videocall_user.incall = False
            raise

        # if incoming_call event cannot be received in INCOMMING_CALL_TIMEOUT(10) seconds,
        # auto disconnect the backend server
        if self._auto_disconnect_greenlet is None:
            self._auto_disconnect_greenlet = gevent.spawn_later(INCOMMING_CALL_TIMEOUT, self._auto_disconnect_routine)

    def _handle_async_message(self, transaction, body, jsep):
        try:
            result = None
            request = body.get('request')
            if request is None:
                raise JanusCloudError('Request {}  format invalid'.format(body), JANUS_VIDEOCALL_ERROR_INVALID_ELEMENT)
            if request == 'exists':
                body = exists_schema.validate(body)
                username = body['username']
                exists = self._plugin.user_dao.get_by_username(username) is not None
                result = {
                    'username': username,
                    'registered': exists
                }
            elif request == 'list':
                username_list = self._plugin.user_dao.get_username_list()
                result = {
                    'list': username_list
                }
            elif request == 'register':
                if self.videocall_user:
                    raise JanusCloudError('Already registered ({})'.format(self.videocall_user.username),
                                          JANUS_VIDEOCALL_ERROR_ALREADY_REGISTERED)
                body = username_schema.validate(body)
                username = body['username']
                # exist_user = self._plugin.user_dao.get_by_username(username)
                # if exist_user:
                #     log.error('Username \'{}\' already taken'.format(username))
                #     raise JanusCloudError('Username \'{}\' already taken'.format(username),
                #                           JANUS_VIDEOCALL_ERROR_USERNAME_TAKEN)
                # valid, register this new user
                api_url = self._plugin.api_base_url + '/' + username
                new_videocall_user = VideoCallUser(username, handle=self, api_url=api_url)
                self._plugin.user_dao.add(new_videocall_user)
                self.videocall_user = new_videocall_user
                result = {
                    'event': 'registered',
                    'username': username
                }
            elif request == 'call':
                if self.videocall_user is None:
                    raise JanusCloudError('Register a username first', JANUS_VIDEOCALL_ERROR_REGISTER_FIRST)
                if self.videocall_user.incall:
                    raise JanusCloudError('Already in a call', JANUS_VIDEOCALL_ERROR_ALREADY_IN_CALL)

                body = username_schema.validate(body)
                username = body['username']
                if username == self.videocall_user.username:
                    raise JanusCloudError('You can\'t call yourself... use the EchoTest for that',
                                          JANUS_VIDEOCALL_ERROR_USE_ECHO_TEST)
                peer = self._plugin.user_dao.get_by_username(username)
                if peer is None:
                    raise JanusCloudError('Username \'{}\' doesn\'t exist'.format(username),
                                          JANUS_VIDEOCALL_ERROR_NO_SUCH_USERNAME)
                if jsep is None:
                    raise JanusCloudError('Missing SDP', JANUS_VIDEOCALL_ERROR_MISSING_SDP)

                server = self._plugin.backend_server_mgr.choose_server(self._session.ts)
                if server is None:
                    raise JanusCloudError('No backend server', JANUS_ERROR_BAD_GATEWAY)

                if peer.incall:
                    log.debug('{} is busy'.format(username))
                    result = {
                        'event': 'hangup',
                        'username': self.videocall_user.username,
                        'reason': 'User busy'
                    }
                else:
                    # start the calling process
                    self.videocall_user.peer_name = username
                    self.videocall_user.incall = True
                    self.videocall_user.utime = time.time()
                    self._plugin.user_dao.update(self.videocall_user)
                    try:
                        self._connect_backend(server.url)
                        self._plugin.call_peer(username, self.videocall_user.username,
                                               server.url)
                        # send call request to backend server
                        result, reply_jsep = self._send_backend_meseage(self.backend_handle,body, jsep)
                    except Exception:
                        backend_handle = self.backend_handle
                        self.backend_handle = None
                        if backend_handle:
                            backend_handle.detach()

                        self.videocall_user.peer_name = ''
                        self.videocall_user.incall = False
                        self.videocall_user.utime = time.time()
                        self._plugin.user_dao.update(self.videocall_user)
                        raise

            elif request == 'accept':
                if self.videocall_user is None or self.videocall_user.incall is False \
                        or self.videocall_user.peer_name == '' or self.backend_handle is None:
                    raise JanusCloudError('No incoming call to accept', JANUS_VIDEOCALL_ERROR_NO_CALL)
                if jsep is None:
                    raise JanusCloudError('Missing SDP', JANUS_VIDEOCALL_ERROR_MISSING_SDP)

                # send accept request to backend server
                result, reply_jsep = self._send_backend_meseage(self.backend_handle, body, jsep)
            elif request == 'set':
                if self.backend_handle:  # has set up the backend handle
                    # send set request to backend server
                    result, reply_jsep = self._send_backend_meseage(self.backend_handle, body, jsep)
                else:
                    body = set_schema.validate(body)
                    if self._pending_set_body is None:
                        self._pending_set_body = body
                    else:
                        self._pending_set_body.update(body)
                    result = {
                        'event': 'set'
                    }
            elif request == 'hangup':
                reason = str(body.get('reason', 'We did the hangup'))

                if self.videocall_user and self.videocall_user.incall:
                    # stop auto disconnect greenlet
                    if self._auto_disconnect_greenlet:
                        gevent.kill(self._auto_disconnect_greenlet)
                        self._auto_disconnect_greenlet = None
                    backend_handle = self.backend_handle
                    self.backend_handle = None
                    peer_name = self.videocall_user.peer_name
                    self.videocall_user.peer_name = ''
                    self.videocall_user.incall = False
                    self.videocall_user.utime = time.time()
                    self._plugin.user_dao.update(self.videocall_user)

                    if backend_handle:
                        try:
                            self._send_backend_meseage(backend_handle,
                                                       {'request' : 'hangup', 'reason': reason})
                        except Exception:
                            log.exception('hangup backend handle failed')
                        finally:
                            backend_handle.detach()
                    else:
                        log.warn('backend_handle absent for user {}'.format(self.videocall_user.username))

                    log.debug("{} is hanging up the call with {}".format(
                        self.videocall_user.username, peer_name)
                    )
                else:
                    log.warn('No call to hangup')

                if self.videocall_user:
                    username = self.videocall_user.username
                else:
                    username = 'unkown'
                result = {
                    'event': 'hangup',
                    'username': username,
                    'reason': 'Explicit hangup'
                }

            else:
                log.error('unknown request {}'.format(request))
                raise JanusCloudError('Unknown request {{}}'.format(request), JANUS_VIDEOCALL_ERROR_INVALID_REQUEST)

            # Process successfully
            data = {
                'videocall': 'event',
            }
            if result:
                data['result'] = result
            self._push_plugin_event(data, transaction=transaction)

        except JanusCloudError as e:
            log.exception('Fail to handle async message ({}) for handle {}'.format(body, self.handle_id))
            self._push_plugin_event({'videocall':'event',
                              'error_code': e.code,
                              'error':str(e),
                              }, transaction=transaction)
        except SchemaError as e:
            log.exception('invalid message format ({}) for handle {}'.format(body, self.handle_id))
            self._push_plugin_event({'videocall':'event',
                              'error_code': JANUS_VIDEOCALL_ERROR_INVALID_ELEMENT,
                              'error':str(e),
                              }, transaction=transaction)
        except Exception as e:
            log.exception('Fail to handle async message ({}) for handle {}'.format(body, self.handle_id))
            self._push_plugin_event({'videocall':'event',
                              'error_code': JANUS_ERROR_BAD_GATEWAY,
                              'error':str(e),
                              }, transaction=transaction)

    def on_async_event(self, handle, event_msg):
        if self._has_destroy:
            return
        if event_msg['janus'] == 'event':
            data = event_msg['plugindata']['data']
            result = event_msg['plugindata']['data']['result']
            jsep = event_msg.get('jsep')
            event = result.get('event', '')
            if event == 'hangup':
                if self.videocall_user and self.videocall_user.incall:
                    # stop auto disconnect greenlet
                    if self._auto_disconnect_greenlet:
                        gevent.kill(self._auto_disconnect_greenlet)
                        self._auto_disconnect_greenlet = None
                    backend_handle = self.backend_handle
                    self.backend_handle = None
                    self.videocall_user.peer_name = ''
                    self.videocall_user.incall = False
                    self.videocall_user.utime = time.time()
                    self._plugin.user_dao.update(self.videocall_user)
                    if backend_handle:
                        backend_handle.detach()
            elif event == 'update':
                if self.videocall_user is None or self.videocall_user.incall == False:
                    log.warn('async event {} invalid for handle {}, ignored'.format(event_msg, self.handle_id))
                    return
            elif event == 'accepted':
                if self.videocall_user is None or self.videocall_user.incall == False or \
                   self.videocall_user.peer_name != result.get('username',''):
                    # incomingcall event invalid, ignore it
                    log.warn('async event {} invalid for handle {}, ignored'.format(event_msg, self.handle_id))
                    return
            elif event == 'incomingcall':
                # if receive incomingcll, means caller and callee has already connected to the same backend server,
                # and caller send 'call' request successfully.
                # double check state
                if self.videocall_user is None or self.videocall_user.incall == False or \
                   self.videocall_user.peer_name != result.get('username',''):
                    # incomingcall event invalid, ignore it
                    log.warn('async event {} invalid for handle {}, ignored'.format(event_msg, self.handle_id))
                    return
                # disable the auto disconnect greenlet
                if self._auto_disconnect_greenlet:
                    gevent.kill(self._auto_disconnect_greenlet)
                    self._auto_disconnect_greenlet = None
            elif event == 'slow_link':
                if self.videocall_user is None or self.videocall_user.incall == False:
                    log.warn('async event {} invalid for handle {}, ignored'.format(event_msg, self.handle_id))
                    return
            elif event == 'simulcast':
                if self.videocall_user is None or self.videocall_user.incall is False:
                    log.warning('async event {} invalid for handle {}, ignored'.format(event_msg, self.handle_id))
                    return
            else:
                if self.videocall_user is None or self.videocall_user.incall == False:
                    log.warn('async event {} invalid for handle {}, ignored'.format(event_msg, self.handle_id))
                    return
                pass

            self._push_plugin_event(data, jsep)
        else:
            params = dict()
            for key, value in event_msg.items():
                if key not in ['janus', 'session_id', 'sender', 'opaque_id', 'transaction']:
                    params[key] = value
            self._push_event(event_msg['janus'], None, **params)

    def on_close(self, handle):
        self.backend_handle = None #detach with backend handle

        if self._auto_disconnect_greenlet:
            gevent.kill(self._auto_disconnect_greenlet)
            self._auto_disconnect_greenlet = None

        if self.videocall_user and self.videocall_user.incall:
            self.videocall_user.peer_name = ''
            self.videocall_user.incall = False
            self.videocall_user.utime = time.time()
            self._plugin.user_dao.update(self.videocall_user.peer_name)

            hangup_event_data = {
                'videocall': 'event',
                'result' : {
                    "event" : "hangup",
                    "username" : self.videocall_user.username,
                    "reason" : "backend handle closed"
                }
            }
            self._push_plugin_event(hangup_event_data, None, None)

    def _connect_backend(self, server_url):

        if self.backend_handle is not None:
            raise JanusCloudError('Already connected', JANUS_ERROR_INTERNAL_ERROR)
        if self.videocall_user is None:
            raise JanusCloudError('Register a username first', JANUS_VIDEOCALL_ERROR_REGISTER_FIRST)

        backend_session = get_backend_session(server_url,
                                              auto_destroy=BACKEND_SESSION_AUTO_DESTROY_TIME)
        backend_handle = backend_session.attach_handle(JANUS_VIDEOCALL_PACKAGE, handle_listener=self)

        # register
        try:
            body = {
                   'request':  'register',
                   'username': self.videocall_user.username
            }
            self._send_backend_meseage(backend_handle, body)
            if self._pending_set_body:
                self._send_backend_meseage(backend_handle, self._pending_set_body)
            if len(self._pending_candidates) > 0:
                backend_handle.send_trickle(candidates=self._pending_candidates)

        except Exception:
            backend_handle.detach()
            raise

        # connect & setup successfully
        if self._pending_set_body:
            self._pending_set_body = None
        if len(self._pending_candidates) > 0:
            self._pending_candidates.clear()
        self.backend_handle = backend_handle

    def _disconnect_backend(self):
        if self.backend_handle is not None:
            self.backend_handle.detach()
            self.backend_handle = None

    def _auto_disconnect_routine(self):
        self._auto_disconnect_greenlet = None
        backend_handle = self.backend_handle
        self.backend_handle = None
        self.videocall_user.peer_name = ''
        self.videocall_user.incall = False
        self.videocall_user.utime = time.time()
        self._plugin.user_dao.update(self.videocall_user)
        if backend_handle:
            backend_handle.detach()


    @staticmethod
    def _send_backend_meseage(backend_handle, body, jsep=None):
        if backend_handle is None:
            raise JanusCloudError('Not connected', JANUS_ERROR_INTERNAL_ERROR)
        data, reply_jsep = backend_handle.send_message(body=body, jsep=jsep)
        if 'error_code' in data:
            raise JanusCloudError(data.get('error','unknown'), data['error_code'])
        elif 'result' not in data:
            raise JanusCloudError('Invalid Response payload: {}'.format(data), JANUS_ERROR_BAD_GATEWAY)
        return data['result'], reply_jsep


class VideoCallPlugin(PluginBase):

    def __init__(self, proxy_config, backend_server_mgr, pyramid_config):
        super().__init__(proxy_config, backend_server_mgr, pyramid_config)
        self.config = self.read_config(
            os.path.join(proxy_config['general']['configs_folder'], 'janus-proxy.plugin.videocall.yml')
        )
        self.backend_server_mgr = backend_server_mgr

        self.api_base_url = self.get_api_base_url(proxy_config)
        #print('api_base_url:', self.api_base_url)

        # set up DAO
        self.user_dao = None

        if self.config['general']['user_db'].startswith('memory'):
            from januscloud.proxy.dao.mem_videocall_user_dao import MemVideoCallUserDao
            self.user_dao = MemVideoCallUserDao()

        elif self.config['general']['user_db'].startswith('redis://'):
            import redis
            from januscloud.proxy.dao.rd_videocall_user_dao import RDVideoCallUserDao
            connection_pool = redis.BlockingConnectionPool.from_url(
                url=self.config['general']['user_db'],
                decode_responses=True,
                health_check_interval=30,
                timeout=10)
            redis_client = redis.Redis(connection_pool=connection_pool)
            self.user_dao = RDVideoCallUserDao(redis_client=redis_client,
                                          api_base_url=self.api_base_url)
        else:
            raise JanusCloudError('user_db url {} not support by videocall plugin'.format(self.config['general']['user_db']),
                                  JANUS_ERROR_NOT_IMPLEMENTED)

        includeme(pyramid_config)
        pyramid_config.registry.videocall_plugin = self

        log.info('{} initialized!'.format(JANUS_VIDEOCALL_NAME))

    def get_version(self):
        return JANUS_VIDEOCALL_VERSION

    def get_version_string(self):
        return JANUS_VIDEOCALL_VERSION_STRING

    def get_description(self):
        return JANUS_VIDEOCALL_DESCRIPTION

    def get_name(self):
        return JANUS_VIDEOCALL_NAME

    def get_author(self):
        return JANUS_VIDEOCALL_AUTHOR

    def get_package(self):
        return JANUS_VIDEOCALL_PACKAGE

    def create_handle(self, handle_id, session, opaque_id=None, *args, **kwargs):
        return VideoCallHandle(handle_id, session, self, opaque_id, *args, **kwargs)

    def call_peer(self, peer_username, caller_username, backend_server_url):
        peer = self.user_dao.get_by_username(peer_username)
        if peer is None:
            raise JanusCloudError('Username \'{}\' doesn\'t exist'.format(peer_username),
                                    JANUS_VIDEOCALL_ERROR_NO_SUCH_USERNAME)
        if peer.handle:
            # the peer is handled by self
            peer.handle.handle_incoming_call(caller_username, backend_server_url)
        elif peer.api_url:
            # the peer is handled by the other janus-proxy
            caller = self.user_dao.get_by_username(caller_username)
            if caller is None or caller.handle is None:
                raise JanusCloudError('Not support relay http request',
                                        JANUS_VIDEOCALL_ERROR_INVALID_REQUEST)
            r = requests.post(peer.api_url,
                              data={'caller_username': caller_username,
                                    'backend_server_url': backend_server_url
                                    }
                              )
            if r.status_code != requests.codes.ok:
                try:
                    text = r.json()['info']
                except Exception:
                    text = r.text
                raise JanusCloudError(text, r.status_code)

    @staticmethod
    def read_config(config_file):

        videocall_config_schema = Schema({
            Optional("general"): Default({
                Optional("user_db"): Default(StrVal(), default='memory'),
                AutoDel(str): object  # for all other key we don't care
            }, default={}),
            DoNotCare(str): object  # for all other key we don't care

        })
        #print('config file:', config_file)
        if config_file is None or config_file == '':
            config = videocall_config_schema.validate({})
        else:
            log.info('Videocall plugin loads the config file: {}'.format(os.path.abspath(config_file)))
            config = parse_config(config_file, videocall_config_schema)

        # check other configure option is valid or not

        return config

    @staticmethod
    def get_api_base_url(proxy_config):
        server_addr = None
        server_name = proxy_config['general']['server_name'].strip()
        if len(server_name) > 0 and server_name != '127.0.0.1' and 'localhost' not in server_name:
            server_addr = server_name
#            try:
#                ip = socket.gethostbyname(server_name)
#                if ip and ip not in {'127.0.0.1', '0.0.0.0'}:
#                    server_addr = server_name
#            except socket.error as e:
#                # server_name is not a valid host domain name
#                pass
        listen_addr, sep, port = proxy_config['admin_api']['http_listen'].strip().partition(':')
        if server_addr is None and listen_addr.strip() != '0.0.0.0':
            server_addr = listen_addr.strip()
        if server_addr is None:
            server_addr = get_host_ip()
        return 'http://' + server_addr + ':' + str(port) + JANUS_VIDEOCALL_API_BASE_PATH



def includeme(config):
    config.add_route('videocall_user', JANUS_VIDEOCALL_API_BASE_PATH + '/{username}')
    config.scan('januscloud.proxy.plugin.videocall')


@post_view(route_name='videocall_user')
def post_video_user(request):
    params = get_params_from_request(request)
    print('params: {}'.format(params))
    params = get_params_from_request(request, post_videocall_user_schema)
    username = request.matchdict['username']
    #print('username:', username)
    request.registry.videocall_plugin.call_peer(username, **params)
    return Response(status=200)


