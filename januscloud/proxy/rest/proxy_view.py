# -*- coding: utf-8 -*-
from januscloud.core.backend_server import JANUS_SERVER_STATUS_ABNORMAL, JANUS_SERVER_STATUS_NORMAL, \
    JANUS_SERVER_STATUS_MAINTENANCE
from januscloud.proxy.rest.common import get_view, post_view, delete_view, get_params_from_request
from januscloud.common.schema import Schema, Optional, DoNotCare, \
    Use, IntVal, Default, SchemaError, BoolVal, StrRe, ListVal, Or, STRING, \
    FloatVal, AutoDel, StrVal, EnumVal
from pyramid.response import Response
from januscloud.core.plugin_base import get_plugin_list

def includeme(config):
    config.add_route('info', '/info')
    config.add_route('ping', '/ping')



@get_view(route_name='info')
def get_info(request):
    proxy_conf = request.registry.proxy_conf
    reply = {'janus': 'server_info',
             'name': 'Janus-Cloud Proxy',
             'author': 'OpenSight',
             'email': 'public@opensight.cn',
             'website': 'https://github.com/OpenSight/janus-cloud',
             'server_name': proxy_conf.get('general', {}).get('server_name', ''),
             'session-timeout': proxy_conf.get('general', {}).get('session_timeout', 60)}

    plugin_info_list = {}
    for plugin in get_plugin_list():
        plugin_info = {
            'version_string': plugin.get_version_string(),
            'description': plugin.get_description(),
            'author': plugin.get_author(),
            'name': plugin.get_name(),
            'version': plugin.get_version(),
        }
        plugin_info_list[plugin.get_package()] = plugin_info
    reply['plugins'] = plugin_info_list
    return reply



@get_view(route_name='ping')
def get_ping(request):
    return 'pong'
