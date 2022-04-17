# -*- coding: utf-8 -*-

import logging
from gevent.pool import Pool
from januscloud.common.utils import error_to_janus_msg, create_janus_msg
from januscloud.common.error import JanusCloudError, JANUS_ERROR_UNKNOWN_REQUEST, JANUS_ERROR_INVALID_REQUEST_PATH
from januscloud.common.schema import Schema, Optional, DoNotCare, \
    Use, IntVal, Default, SchemaError, BoolVal, StrRe, ListVal, Or, STRING, \
    FloatVal, AutoDel
from januscloud.core.plugin_base import PluginBase
from januscloud.core.frontend_handle_base import FrontendHandleBase, JANUS_PLUGIN_OK, JANUS_PLUGIN_OK_WAIT

log = logging.getLogger(__name__)


JANUS_DUMMYTEST_VERSION = 1
JANUS_DUMMYTEST_VERSION_STRING = '0.0.1'
JANUS_DUMMYTEST_DESCRIPTION = 'This is a trivial dummy plugin for Janus-cloud, ' \
                                'just used to test api of Janus-cloud.'
JANUS_DUMMYTEST_NAME = 'JANUS DummyTest plugin'
JANUS_DUMMYTEST_AUTHOR = 'opensight.cn'
JANUS_DUMMYTEST_PACKAGE = 'janus.plugin.dummytest'


class DummyHandle(FrontendHandleBase):
    def __init__(self, handle_id, session, plugin, opaque_id=None, *args, **kwargs):
        super().__init__(handle_id, session, plugin, opaque_id, *args, **kwargs)

    def handle_hangup(self):
        log.info('handle_hangup for dummy Handle {}'.format(self.handle_id))

    def handle_message(self, transaction, body, jsep=None):
        log.info('handle_message for dummy handle {}. transaction:{} body:{} jsep:{}'.
                 format(self.handle_id, transaction, body, jsep))
        if 'async' in body:
            self._enqueue_async_message(transaction, body, jsep)
            return JANUS_PLUGIN_OK_WAIT, None
        else:
            return JANUS_PLUGIN_OK, {'dummytest': 'successful'}

    def handle_trickle(self, candidate=None, candidates=None):
        log.info('handle_trickle for dummy handle {}.candidate:{} candidates:{}'.
                 format(self.handle_id, candidate, candidates))

    def _handle_async_message(self, transaction, body, jsep):
        self._push_plugin_event({'dummytest':'successful'}, jsep, transaction)

class DummyTestPlugin(PluginBase):

    def __init__(self, proxy_config, backend_server_mgr, pyramid_config):
        super().__init__(proxy_config, backend_server_mgr, pyramid_config)
        log.info('{} initialized!'.format(JANUS_DUMMYTEST_NAME))

    def get_version(self):
        return JANUS_DUMMYTEST_VERSION

    def get_version_string(self):
        return JANUS_DUMMYTEST_VERSION_STRING

    def get_description(self):
        return JANUS_DUMMYTEST_DESCRIPTION

    def get_name(self):
        return JANUS_DUMMYTEST_NAME

    def get_author(self):
        return JANUS_DUMMYTEST_AUTHOR

    def get_package(self):
        return JANUS_DUMMYTEST_PACKAGE

    def create_handle(self, handle_id, session, opaque_id=None, *args, **kwargs):
        return DummyHandle(handle_id, session, self, opaque_id, *args, **kwargs)






