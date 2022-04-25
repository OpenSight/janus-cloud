# -*- coding: utf-8 -*-
import copy

import logging
from januscloud.common.utils import error_to_janus_msg, create_janus_msg
from januscloud.common.error import JanusCloudError, JANUS_ERROR_UNKNOWN_REQUEST, JANUS_ERROR_INVALID_REQUEST_PATH, \
    JANUS_ERROR_BAD_GATEWAY
from januscloud.common.schema import Schema, Optional, DoNotCare, \
    Use, IntVal, Default, SchemaError, BoolVal, StrRe, ListVal, Or, STRING, \
    FloatVal, AutoDel
from januscloud.core.backend_session import get_backend_session
from januscloud.core.plugin_base import PluginBase
from januscloud.core.frontend_handle_base import FrontendHandleBase, JANUS_PLUGIN_OK_WAIT


log = logging.getLogger(__name__)

BACKEND_SESSION_AUTO_DESTROY_TIME = 300 # auto destroy the backend session after 300s if no handle for it

JANUS_ECHOTEST_VERSION = 7
JANUS_ECHOTEST_VERSION_STRING = '0.0.7'
JANUS_ECHOTEST_DESCRIPTION = 'This is a trivial EchoTest plugin for Janus-cloud, ' \
                                'just used to showcase the plugin interface.'
JANUS_ECHOTEST_NAME = 'JANUS EchoTest plugin'
JANUS_ECHOTEST_AUTHOR = 'opensight.cn'
JANUS_ECHOTEST_PACKAGE = 'janus.plugin.echotest'


class EchoTestHandle(FrontendHandleBase):
    def __init__(self, handle_id, session, plugin, opaque_id=None, *args, **kwargs):
        super().__init__(handle_id, session, plugin, opaque_id, *args, **kwargs)

        server = plugin.backend_server_mgr.choose_server(session.ts)
        if server is None:
            raise JanusCloudError('No backend server', JANUS_ERROR_BAD_GATEWAY)

        backend_session = get_backend_session(server.url,
                                              auto_destroy=BACKEND_SESSION_AUTO_DESTROY_TIME)
        self.backend_handle = backend_session.attach_handle(JANUS_ECHOTEST_PACKAGE, handle_listener=self)

    def detach(self):
        super().detach()
        backend_handle = self.backend_handle
        self.backend_handle = None
        backend_handle.detach()

    def handle_hangup(self):
        if self.backend_handle is None:
            raise JanusCloudError('backend handle invalid', JANUS_ERROR_BAD_GATEWAY)

        log.info('handle_hangup for echotest Handle {}'.format(self.handle_id))
        self.backend_handle.send_hangup()

    def handle_message(self, transaction, body, jsep=None):
        if self.backend_handle is None:
            raise JanusCloudError('backend handle invalid', JANUS_ERROR_BAD_GATEWAY)

        log.debug('handle_message for echotest handle {}. transaction:{} body:{} jsep:{}'.
                  format(self.handle_id, transaction, body, jsep))

        self._enqueue_async_message(transaction, body, jsep)
        return JANUS_PLUGIN_OK_WAIT, None

    def handle_trickle(self, candidate=None, candidates=None):
        if self.backend_handle is None:
            raise JanusCloudError('backend handle invalid', JANUS_ERROR_BAD_GATEWAY)
        log.debug('handle_trickle for echotest handle {}.candidate:{} candidates:{}'.
                 format(self.handle_id, candidate, candidates))
        self.backend_handle.send_trickle(candidate=candidate, candidates=candidates)

    def _handle_async_message(self, transaction, body, jsep):
        try:
            if self.backend_handle is None:
                raise JanusCloudError('backend handle invalid', JANUS_ERROR_BAD_GATEWAY)

            data, reply_jsep = self.backend_handle.send_message(body=body, jsep=jsep)
            self._push_plugin_event(data, reply_jsep, transaction)

        except JanusCloudError as e:
            log.exception('Fail to send message to backend handle {}'.format(self.backend_handle.handle_id))
            self._push_plugin_event({'echotest':'event',
                              'error_code': e.code,
                              'error':str(e),
                              }, transaction=transaction)
        except Exception as e:
            log.exception('Fail to send message to backend handle {}'.format(self.backend_handle.handle_id))
            self._push_plugin_event({'echotest':'event',
                              'error_code': JANUS_ERROR_BAD_GATEWAY,
                              'error':str(e),
                              }, transaction=transaction)

    def on_async_event(self, handle, event_msg):
        if self._has_destroy:
            return
        if event_msg['janus'] == 'event':
            data = event_msg['plugindata']['data']
            jsep = event_msg.get('jsep')
            self._push_plugin_event(data, jsep)
        else:
            params = dict()
            for key, value in event_msg.items():
                if key not in ['janus', 'session_id', 'sender', 'opaque_id', 'transaction']:
                    params[key] = value
            self._push_event(event_msg['janus'], None, **params)

    def on_close(self, handle):
        self.backend_handle = None #detach with backend handle


class EchoTestPlugin(PluginBase):

    def __init__(self, proxy_config, backend_server_mgr, pyramid_config):
        super().__init__(proxy_config, backend_server_mgr, pyramid_config)
        self.backend_server_mgr = backend_server_mgr
        log.info('{} initialized!'.format(JANUS_ECHOTEST_NAME))

    def get_version(self):
        return JANUS_ECHOTEST_VERSION

    def get_version_string(self):
        return JANUS_ECHOTEST_VERSION_STRING

    def get_description(self):
        return JANUS_ECHOTEST_DESCRIPTION

    def get_name(self):
        return JANUS_ECHOTEST_NAME

    def get_author(self):
        return JANUS_ECHOTEST_AUTHOR

    def get_package(self):
        return JANUS_ECHOTEST_PACKAGE

    def create_handle(self, handle_id, session, opaque_id=None, *args, **kwargs):
        return EchoTestHandle(handle_id, session, self, opaque_id, *args, **kwargs)


if __name__ == '__main__':
    pass





