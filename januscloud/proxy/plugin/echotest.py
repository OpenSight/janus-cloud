# -*- coding: utf-8 -*-

import logging
from januscloud.common.utils import error_to_janus_msg, create_janus_msg
from januscloud.common.error import JanusCloudError, JANUS_ERROR_UNKNOWN_REQUEST, JANUS_ERROR_INVALID_REQUEST_PATH
from januscloud.common.schema import Schema, Optional, DoNotCare, \
    Use, IntVal, Default, SchemaError, BoolVal, StrRe, ListVal, Or, STRING, \
    FloatVal, AutoDel
from januscloud.proxy.core.plugin_base import PluginBase


log = logging.getLogger(__name__)


JANUS_ECHOTEST_VERSION = 7
JANUS_ECHOTEST_VERSION_STRING = '0.0.7'
JANUS_ECHOTEST_DESCRIPTION = 'This is a trivial EchoTest plugin for Janus-cloud, ' \
                                'just used to showcase the plugin interface.'
JANUS_ECHOTEST_NAME = 'JANUS EchoTest plugin'
JANUS_ECHOTEST_AUTHOR = 'opensight.cn'
JANUS_ECHOTEST_PACKAGE = 'janus.plugin.echotest'


class EchoTestPlugin(PluginBase):
    """ This base class for plugin """

    def init(self, config_path):
        pass

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

    def create_handle(self, handle_id, session):
        return super(EchoTestPlugin, self).create_handle(handle_id, session)


def create():
    return EchoTestPlugin()

if __name__ == '__main__':
    pass





