# -*- coding: utf-8 -*-
import base64
import copy

import json
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

JANUS_P2PCALL_ERROR_USERNAME_TAKEN = 476
JANUS_P2PCALL_ERROR_REGISTER_FIRST = 473
JANUS_P2PCALL_ERROR_ALREADY_IN_CALL = 480
JANUS_P2PCALL_ERROR_INVALID_ELEMENT = 474
JANUS_P2PCALL_ERROR_INVALID_REQUEST = 472
JANUS_P2PCALL_ERROR_ALREADY_REGISTERED = 477
JANUS_P2PCALL_ERROR_NO_SUCH_USERNAME = 478
JANUS_P2PCALL_ERROR_USE_ECHO_TEST = 479
JANUS_P2PCALL_ERROR_NO_CALL = 481
JANUS_P2PCALL_ERROR_MISSING_SDP = 482

JANUS_P2PCALL_API_SYNC_VERSION = 'v0.11.4(2021-09-7)'
JANUS_P2PCALL_VERSION = 6
JANUS_P2PCALL_VERSION_STRING = '0.0.6'
JANUS_P2PCALL_DESCRIPTION = 'This is a simple P2P video call plugin for Janus-cloud, ' \
                                'allow two WebRTC peer communicate with each other in P2P mode. ' \
                                'Its API is kept sync with videocall of Janus-gateway until ' + \
                                JANUS_P2PCALL_API_SYNC_VERSION
JANUS_P2PCALL_NAME = 'JANUS P2PCall plugin'
JANUS_P2PCALL_AUTHOR = 'opensight.cn'
JANUS_P2PCALL_PACKAGE = 'janus.plugin.p2pcall'

JANUS_P2PCALL_API_BASE_PATH = '/plugins/p2pcall'


username_schema = Schema({
    'username': StrRe('^\w{1,128}$'),
    DoNotCare(str): object  # for all other key we don't care
})


class P2PCallUser(object):

    def __init__(self, username, handle=None, incall=False, peer_name='', api_url=''):
        self.username = username
        self.incall = incall
        self.peer_name = peer_name
        self.handle = handle
        self.api_url = api_url
        self.utime = time.time()
        self.ctime = time.time()

    def __str__(self):
        return 'P2P Call User"{0}"(url:{1}, handle:{2})'.format(self.username, self.api_url, self.handle)


class P2PCallHandle(FrontendHandleBase):

    def __init__(self, handle_id, session, plugin, opaque_id=None, *args, **kwargs):
        super().__init__(handle_id, session, plugin, opaque_id, *args, **kwargs)

        self.p2pcall_user = None
        self._pending_candidates = list()
        self._trickle_holding = False

    def detach(self):
        if self._has_destroy:
            return
        super().detach()

        if self.p2pcall_user:
            self._plugin.user_dao.del_by_username(self.p2pcall_user.username)
            self.p2pcall_user.handle = None
            self.p2pcall_user = None

    def handle_hangup(self):
        log.debug('handle_hangup for p2pcall Handle {}'.format(self.handle_id))
        if self.p2pcall_user and self.p2pcall_user.incall:
            hangup_msg = create_janus_msg('hangup', reason='Peer Hangup')
            self._send_aync_event(self.p2pcall_user.peer_name, hangup_msg)

    def handle_message(self, transaction, body, jsep=None):
        log.debug('handle_message for p2pcall handle {}. transaction:{} body:{} jsep:{}'.
                 format(self.handle_id, transaction, body, jsep))
        self._enqueue_async_message(transaction, body, jsep)
        return JANUS_PLUGIN_OK_WAIT, None

    def handle_trickle(self, candidate=None, candidates=None):
        log.debug('handle_trickle for p2pcall handle {}.candidate:{} candidates:{}'.
                 format(self.handle_id, candidate, candidates))

        if candidate:
            if candidates:
                candidates.append(candidate)
            else:
                candidates = [candidate]

        if self.p2pcall_user is None or self.p2pcall_user.incall is False or self._trickle_holding:
            self._pending_candidates.extend(candidates)
        else:
            trickle_msg = create_janus_msg('trickle', candidates=candidates)
            self._send_aync_event(self.p2pcall_user.peer_name, trickle_msg)

    def _handle_async_message(self, transaction, body, jsep):
        try:
            result = None
            request = body.get('request')
            if request is None:
                raise JanusCloudError('Request {}  format invalid'.format(body), JANUS_P2PCALL_ERROR_INVALID_ELEMENT)
            if request == 'list':
                username_list = self._plugin.user_dao.get_username_list()
                result = {
                    'list': username_list
                }
            elif request == 'register':
                if self.p2pcall_user:
                    raise JanusCloudError('Already registered ({})'.format(self.p2pcall_user.username),
                                          JANUS_P2PCALL_ERROR_ALREADY_REGISTERED)
                body = username_schema.validate(body)
                username = body['username']
                # valid, register this new user
                api_url = self._plugin.api_base_url + '/' + username
                new_p2pcall_user = P2PCallUser(username, handle=self, api_url=api_url)
                self._plugin.user_dao.add(new_p2pcall_user)
                self.p2pcall_user = new_p2pcall_user
                result = {
                    'event': 'registered',
                    'username': username
                }
            elif request == 'call':
                if self.p2pcall_user is None:
                    raise JanusCloudError('Register a username first', JANUS_P2PCALL_ERROR_REGISTER_FIRST)
                if self.p2pcall_user.incall:
                    raise JanusCloudError('Already in a call', JANUS_P2PCALL_ERROR_ALREADY_IN_CALL)

                body = username_schema.validate(body)
                username = body['username']
                if username == self.p2pcall_user.username:
                    raise JanusCloudError('You can\'t call yourself... use the EchoTest for that',
                                          JANUS_P2PCALL_ERROR_USE_ECHO_TEST)
                peer = self._plugin.user_dao.get_by_username(username)
                if peer is None:
                    raise JanusCloudError('Username \'{}\' doesn\'t exist'.format(username),
                                          JANUS_P2PCALL_ERROR_NO_SUCH_USERNAME)
                if jsep is None:
                    raise JanusCloudError('Missing SDP', JANUS_P2PCALL_ERROR_MISSING_SDP)

                if peer.incall:
                    log.debug('{} is busy'.format(username))
                    result = {
                        'event': 'hangup',
                        'username': self.p2pcall_user.username,
                        'reason': 'User busy'
                    }
                else:
                    # start the calling process
                    try:
                        self.p2pcall_user.peer_name = username
                        self.p2pcall_user.incall = True
                        self.p2pcall_user.utime = time.time()
                        self._trickle_holding = True    # buffer the trickle candidates util
                                                        # peer receiving incoming call event
                        # update the user dao
                        self._plugin.user_dao.update(self.p2pcall_user)

                        # send the imcomingcall event to the peer
                        call = {
                            'videocall': 'event',
                            'result': {
                                'event': 'incomingcall',
                                'username': self.p2pcall_user.username
                            }
                        }
                        self._send_plugin_event(username, call, jsep)

                        self._trickle_holding = False
                        # send the buffer candidates
                        if len(self._pending_candidates) > 0:
                            candidates = self._pending_candidates
                            self._pending_candidates.clear()
                            trickle_msg = create_janus_msg('trickle', candidates=candidates)
                            self._send_aync_event(self.p2pcall_user.peer_name, trickle_msg)

                        result = {
                            'event': 'calling'
                        }
                    except Exception:
                        self.p2pcall_user.peer_name = ''
                        self.p2pcall_user.incall = False
                        self._trickle_holding = False
                        # update the user dao
                        self._plugin.user_dao.update(self.p2pcall_user)
                        raise

            elif request == 'accept':
                if self.p2pcall_user is None or self.p2pcall_user.incall is False \
                        or self.p2pcall_user.peer_name == '':
                    raise JanusCloudError('No incoming call to accept', JANUS_P2PCALL_ERROR_NO_CALL)
                if jsep is None:
                    raise JanusCloudError('Missing SDP', JANUS_P2PCALL_ERROR_MISSING_SDP)

                # send the accepted event to the peer
                call = {
                    'videocall': 'event',
                    'result': {
                        'event': 'accepted',
                        'username': self.p2pcall_user.username
                    }
                }
                self._send_plugin_event(self.p2pcall_user.peer_name, call, jsep)

                result = {
                    'event': 'accepted'
                }

            elif request == 'set':
                if self.p2pcall_user is None or self.p2pcall_user.incall is False \
                        or self.p2pcall_user.peer_name == '':
                    raise JanusCloudError('Not in call', JANUS_P2PCALL_ERROR_NO_CALL)
                if jsep is None:
                    raise JanusCloudError('Missing SDP', JANUS_P2PCALL_ERROR_MISSING_SDP)

                # send the accepted event to the peer
                call = {
                    'videocall': 'event',
                    'result': {
                        'event': 'update',
                    }
                }
                self._send_plugin_event(self.p2pcall_user.peer_name, call, jsep)

                result = {
                    'event': 'set'
                }
            elif request == 'hangup':
                reason = str(body.get('reason', 'We did the hangup'))

                if self.p2pcall_user and self.p2pcall_user.incall:

                    peer_name = self.p2pcall_user.peer_name
                    self.p2pcall_user.peer_name = ''
                    self.p2pcall_user.incall = False
                    self.p2pcall_user.utime = time.time()
                    self._plugin.user_dao.update(self.p2pcall_user)

                    try:
                        call = {
                            'videocall': 'event',
                            'result': {
                                'event': 'hangup',
                                'username': self.p2pcall_user.username,
                                'reason': reason
                            }
                        }
                        self._send_plugin_event(peer_name, call, jsep)
                    except Exception:
                        log.warning('fail to hangup to \'{}\''.format(peer_name))

                    log.debug("{} is hanging up the call with {}".format(
                        self.p2pcall_user.username, peer_name)
                    )
                else:
                    log.warning('No call to hangup')

                if self.p2pcall_user:
                    username = self.p2pcall_user.username
                else:
                    username = 'unkown'
                result = {
                    'event': 'hangup',
                    'username': username,
                    'reason': 'Explicit hangup'
                }

            else:
                log.error('unknown request {}'.format(request))
                raise JanusCloudError('Unknown request {{}}'.format(request), JANUS_P2PCALL_ERROR_INVALID_REQUEST)

            # Process successfully
            data = {
                'videocall': 'event',
            }
            if result:
                data['result'] = result
            self._push_plugin_event(data, transaction=transaction)

            if result and result.get('event') == 'accepted':
                self._push_event('webrtcup')

        except JanusCloudError as e:
            log.exception('Fail to handle async message ({}) for handle {}'.format(body, self.handle_id))
            self._push_plugin_event({'videocall': 'event',
                              'error_code': e.code,
                              'error':str(e),
                              }, transaction=transaction)
        except SchemaError as e:
            log.exception('invalid message format ({}) for handle {}'.format(body, self.handle_id))
            self._push_plugin_event({'videocall': 'event',
                              'error_code': JANUS_P2PCALL_ERROR_INVALID_ELEMENT,
                              'error':str(e),
                              }, transaction=transaction)
        except Exception as e:
            log.exception('Fail to handle async message ({}) for handle {}'.format(body, self.handle_id))
            self._push_plugin_event({'videocall':'event',
                              'error_code': JANUS_ERROR_BAD_GATEWAY,
                              'error':str(e),
                              }, transaction=transaction)

    def on_async_event(self, from_user, event_msg):
        if self._has_destroy:
            return
        if event_msg['janus'] == 'event':
            data = event_msg['plugindata']['data']
            result = event_msg['plugindata']['data']['result']
            jsep = event_msg.get('jsep')
            event = result.get('event', '')
            if event == 'hangup':
                if self.p2pcall_user.incall and \
                  self.p2pcall_user.peer_name == result.get('username', from_user):
                    self.p2pcall_user.peer_name = ''
                    self.p2pcall_user.incall = False
                    self.p2pcall_user.utime = time.time()
                    self._plugin.user_dao.update(self.p2pcall_user)
                # always send hangup event to user
                else:
                    raise JanusCloudError('No call to hangup', JANUS_P2PCALL_ERROR_NO_CALL)

            elif event == 'update':
                if self.p2pcall_user.incall is False:
                    raise JanusCloudError('Not in call', JANUS_P2PCALL_ERROR_NO_CALL)

            elif event == 'accepted':
                if self.p2pcall_user.incall is False or \
                  self.p2pcall_user.peer_name != result.get('username', from_user):
                    # incomingcall event invalid, ignore it
                    raise JanusCloudError('No incoming call to accept', JANUS_P2PCALL_ERROR_NO_CALL)

            elif event == 'incomingcall':
                if self.p2pcall_user.incall:
                    # incomingcall event invalid, raise a exception
                    raise JanusCloudError('\'{}\' is busy'.format(self.p2pcall_user.username),
                                          JANUS_P2PCALL_ERROR_INVALID_REQUEST)

                self.p2pcall_user.incall = True
                self.p2pcall_user.peer_name = result.get('username', from_user)
                self.p2pcall_user.utime = time.time()
                self._plugin.user_dao.update(self.p2pcall_user)

                if len(self._pending_candidates) > 0:
                    log.warning('Pending candidates {} before incomingcall, would be cleared'.
                                format(self._pending_candidates))
                    self._pending_candidates.clear()
            else:
                if self.p2pcall_user.incall is False:
                    log.warn('async event {} invalid for handle {}, ignored'.format(event_msg, self.handle_id))
                    return
                pass

            self._push_plugin_event(data, jsep)

            if event == 'accepted':
                self._push_event('webrtcup')

        elif event_msg['janus'] == 'trickle':
            candidates = event_msg.get('candidates', None)
            candidate = event_msg.get('candidate', None)
            if candidate and not candidates:
                candidates = [candidate]
            for can in candidates:
                self._push_event('trickle', candidate=can)
        else:
            params = dict()
            for key, value in event_msg.items():
                if key not in ['janus', 'session_id', 'sender', 'opaque_id', 'transaction']:
                    params[key] = value
            self._push_event(event_msg['janus'], **params)

    def _send_plugin_event(self, to_user, data, jsep=None):
        params = dict()
        params['plugindata'] = {
            'plugin': self.plugin_package_name,
            'data': data
        }
        if jsep:
            params['jsep'] = jsep
        event = create_janus_msg('event', **params)
        self._send_aync_event(to_user, event)

    def _send_aync_event(self, to_user, event_msg):
        if self.p2pcall_user is None:
            raise JanusCloudError('Register a username first', JANUS_P2PCALL_ERROR_REGISTER_FIRST)
        from_user = self.p2pcall_user.username
        peer = self._plugin.user_dao.get_by_username(to_user)
        if peer is None:
            raise JanusCloudError('Username \'{}\' doesn\'t exist'.format(to_user),
                                    JANUS_P2PCALL_ERROR_NO_SUCH_USERNAME)

        if peer.handle:
            # if dest user is handled by the same proxy, send to him directly
            log.debug('An async event ({}) is sent from \'{}\' to \'{}\' at local proxy'.format(
                event_msg, from_user, to_user
            ))

            peer.handle.on_async_event(from_user, event_msg)
        elif peer.api_url:
            # if dest user is handled by the other proxy, send to him by RESTful api
            log.debug('An async event ({}) is sent from \'{}\' to \'{}\' by {}'.format(
                event_msg, from_user, to_user, peer.api_url
            ))
            r = requests.post(peer.api_url,
                              json={
                                  'from_user': from_user,
                                  'async_event': event_msg
                              })
            if r.status_code != requests.codes.ok:
                try:
                    text = r.json()['info']
                except Exception:
                    text = r.text
                raise JanusCloudError(text, r.status_code)
        else:
            raise JanusCloudError('Username \'{}\' doesn\'t exist'.format(to_user),
                                    JANUS_P2PCALL_ERROR_NO_SUCH_USERNAME)


class P2PCallPlugin(PluginBase):
    """ This base class for plugin """

    def __init__(self, proxy_config, backend_server_mgr, pyramid_config):
        super().__init__(proxy_config, backend_server_mgr, pyramid_config)
        self.config = self.read_config(
            os.path.join(proxy_config['general']['configs_folder'], 'janus-proxy.plugin.p2pcall.yml')
        )

        # set up DAO
        from januscloud.proxy.dao.mem_videocall_user_dao import MemVideoCallUserDao
        if self.config['general']['user_db'] == 'memory':
            self.user_dao = MemVideoCallUserDao()
        else:
            raise JanusCloudError('user_db url {} not support by videocall plugin'.format(self.config['general']['user_db']),
                                  JANUS_ERROR_NOT_IMPLEMENTED)

        self.api_base_url = self.get_api_base_url(proxy_config)
        #print('api_base_url:', self.api_base_url)

        includeme(pyramid_config)
        pyramid_config.registry.p2pcall_plugin = self

        log.info('{} initialized!'.format(JANUS_P2PCALL_NAME))

    def get_version(self):
        return JANUS_P2PCALL_VERSION

    def get_version_string(self):
        return JANUS_P2PCALL_VERSION_STRING

    def get_description(self):
        return JANUS_P2PCALL_DESCRIPTION

    def get_name(self):
        return JANUS_P2PCALL_NAME

    def get_author(self):
        return JANUS_P2PCALL_AUTHOR

    def get_package(self):
        return JANUS_P2PCALL_PACKAGE

    def create_handle(self, handle_id, session, opaque_id=None, *args, **kwargs):
        return P2PCallHandle(handle_id, session, self, opaque_id, *args, **kwargs)

    def handle_async_event(self, to_user, from_user, async_event):
        p2pcall_user = self.user_dao.get_by_username(to_user)
        if p2pcall_user is None:
            raise JanusCloudError('Username \'{}\' doesn\'t exist'.format(to_user),
                                    JANUS_P2PCALL_ERROR_NO_SUCH_USERNAME)
        if p2pcall_user.handle is None:
            raise JanusCloudError('Not support relay http request',
                                        JANUS_P2PCALL_ERROR_INVALID_REQUEST)
        log.debug('an async event ({}) from \'{}\' to \'{}\' is received by http API'.
                  format(async_event, from_user, to_user))
        p2pcall_user.handle.on_async_event(from_user, async_event)

    @staticmethod
    def read_config(config_file):

        p2pcall_config_schema = Schema({
            Optional("general"): Default({
                Optional("user_db"): Default(StrVal(), default='memory'),
                AutoDel(str): object  # for all other key we don't care
            }, default={}),
            DoNotCare(str): object  # for all other key we don't care

        })
        #print('config file:', config_file)
        if config_file is None or config_file == '':
            config = p2pcall_config_schema.validate({})
        else:
            log.info('P2Pcall plugin loads the config file: {}'.format(os.path.abspath(config_file)))
            config = parse_config(config_file, p2pcall_config_schema)

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
        if server_addr is None and listen_addr != '0.0.0.0':
            server_addr = listen_addr.strip()
        if server_addr is None:
            server_addr = get_host_ip()
        return 'http://' + server_addr + ':' + str(port) + JANUS_P2PCALL_API_BASE_PATH


def includeme(config):
    config.add_route('p2pcall_user', JANUS_P2PCALL_API_BASE_PATH + '/{username}')
    config.scan('januscloud.proxy.plugin.p2pcall')


post_p2pcall_user_schema = Schema({
    'from_user': StrRe('^\w{1, 128}$'),
    'async_event': dict,
    AutoDel(str): object  # for all other key we auto delete
})


@post_view(route_name='p2pcall_user')
def post_video_user(request):
    params = get_params_from_request(request, post_p2pcall_user_schema)
    username = request.matchdict['username']
    #print('username:', username)
    request.registry.p2pcall_plugin.handle_async_event(to_user=username, **params)
    return Response(status=200)


