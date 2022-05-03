# -*- coding: utf-8 -*-
from januscloud.core.backend_server import JANUS_SERVER_STATUS_ABNORMAL, JANUS_SERVER_STATUS_NORMAL, \
    JANUS_SERVER_STATUS_MAINTENANCE, JANUS_SERVER_STATUS_HWM
from januscloud.proxy.rest.common import get_view, post_view, delete_view, get_params_from_request
from januscloud.common.schema import Schema, Optional, DoNotCare, \
    Use, IntVal, Default, SchemaError, BoolVal, StrRe, ListVal, Or, STRING, \
    FloatVal, AutoDel, StrVal, EnumVal
from pyramid.response import Response


def includeme(config):
    config.add_route('sentinel_callback', '/sentinel_callback')
    config.add_route('backend_server_list', '/backend_servers')
    config.add_route('backend_server', '/backend_servers/{server_name}')


@get_view(route_name='backend_server_list')
def get_backend_server_list(request):
    backend_server_manager = request.registry.backend_server_manager
    server_list = backend_server_manager.get_all_server_list()
    return server_list


server_update_schema = Schema({
    'name': StrRe('^[\w-]{1,64}$'),
    'url': StrRe('^(ws|wss)://\S+$'),
    'status': IntVal(values=(JANUS_SERVER_STATUS_NORMAL, 
                             JANUS_SERVER_STATUS_ABNORMAL, 
                            JANUS_SERVER_STATUS_MAINTENANCE,
                            JANUS_SERVER_STATUS_HWM)),
    Optional("session_timeout"): IntVal(min=0, max=86400),
    Optional("session_num"): IntVal(min=0, max=10000),
    Optional("handle_num"): IntVal(min=0, max=100000),
    Optional("location"): StrVal(min_len=0, max_len=64),
    Optional("isp"): StrVal(min_len=0, max_len=64),
    Optional("expire"): IntVal(min=0, max=86400),
    Optional("start_time"): FloatVal(),
    AutoDel(str): object  # for all other key we must delete
})


@post_view(route_name='sentinel_callback')
def post_sentinel_callback(request):
    params = get_params_from_request(request, server_update_schema)
    backend_server_manager = request.registry.backend_server_manager
    backend_server_manager.update_server(**params)
    return Response(status=200)


@post_view(route_name='backend_server_list')
def post_backend_server_list(request):
    params = get_params_from_request(request, server_update_schema)
    backend_server_manager = request.registry.backend_server_manager
    backend_server_manager.update_server(**params)
    return Response(status=200)


@delete_view(route_name='backend_server')
def delete_backend_server(request):
    server_name = request.matchdict['server_name']
    backend_server_manager = request.registry.backend_server_manager
    backend_server_manager.del_server(server_name)
    return Response(status=200)
