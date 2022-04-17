# -*- coding: utf-8 -*-

import logging
from januscloud.common.utils import error_to_janus_msg, create_janus_msg
from januscloud.common.error import JanusCloudError, JANUS_ERROR_UNKNOWN_REQUEST, JANUS_ERROR_INVALID_REQUEST_PATH
from januscloud.common.schema import Schema, Optional, DoNotCare, \
    Use, IntVal, Default, SchemaError, BoolVal, StrRe, ListVal, Or, STRING, \
    FloatVal, AutoDel
from januscloud.core.frontend_handle_base import FrontendHandleBase

log = logging.getLogger(__name__)

_plugins = {}


class PluginBase(object):
    """ This base class for plugin """

    def __init__(self, proxy_config, backend_server_mgr, pyramid_config):
        pass

    def get_version(self):
        pass

    def get_version_string(self):
        pass

    def get_description(self):
        pass

    def get_name(self):
        pass

    def get_author(self):
        pass

    def get_package(self):
        pass

    def create_handle(self, handle_id, session, opaque_id=None):
        pass


def get_plugin(plugin_package_name, default=None):
    return _plugins.get(plugin_package_name, default)


def get_plugin_list():
    return _plugins.values()


def register_plugin(plugin_package_name, plugin):
    _plugins[plugin_package_name] = plugin



