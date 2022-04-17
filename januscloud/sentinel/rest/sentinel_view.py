# -*- coding: utf-8 -*-
import datetime

from januscloud.common.error import JANUS_ERROR_NOT_IMPLEMENTED, JanusCloudError
from januscloud.core.backend_server import JANUS_SERVER_STATUS_ABNORMAL, JANUS_SERVER_STATUS_NORMAL
from januscloud.proxy.rest.common import get_view, post_view, delete_view, get_params_from_request
from januscloud.common.schema import Schema, Optional, DoNotCare, \
    Use, IntVal, Default, SchemaError, BoolVal, StrRe, ListVal, Or, STRING, \
    FloatVal, AutoDel, StrVal, EnumVal
from pyramid.response import Response

from januscloud.sentinel.poster_manager import list_posters


def includeme(config):
    config.add_route('sentinel_info', '/sentinel/info')
    config.add_route('sentinel_op', '/sentinel/op')
    config.add_route('posters', '/sentinel/posters')


@get_view(route_name='sentinel_info')
def get_sentinel_info(request):
    janus_server = request.registry.janus_server
    janus_watcher = request.registry.janus_watcher
    info = {
        'janus_server': {
            'server_name': janus_server.server_name,
            'server_url': janus_server.url,
            'server_public_url': janus_server.public_url,
            'server_admin_url': janus_server.admin_url,
            'status': janus_server.status,
            'session_num': janus_server.session_num,
            'handle_num': janus_server.handle_num,
            'start_time': str(datetime.datetime.fromtimestamp(janus_server.start_time)),

        }
    }
    if janus_watcher:
        info['janus_watcher'] = {
            'args': janus_watcher.args,
            'process_status': janus_watcher.process_status,
            'pid': janus_watcher.pid,
            'process_running_time': janus_watcher.process_running_time,
            'auto_restart_count': janus_watcher.auto_restart_count,
            'last_exit_time': str(datetime.datetime.fromtimestamp(janus_watcher.process_exit_time)),
            'last_return_code': janus_watcher.process_return_code,
        }

    return info


op_schema = Schema({
    'op': EnumVal(['start_maintenance', 'stop_maintenance', 'restart_process']),
    AutoDel(str): object  # for all other key we must delete
})


@post_view(route_name='sentinel_op')
def post_sentinel_op(request):
    params = get_params_from_request(request, op_schema)
    janus_server = request.registry.janus_server
    janus_watcher = request.registry.janus_watcher
    if params['op'] == 'start_maintenance':
        janus_server.start_maintenance()
    elif params['op'] == 'stop_maintenance':
        janus_server.stop_maintenance()
    elif params['op'] == 'restart_process':
        if janus_watcher is None:
            raise JanusCloudError('janus_watcher not enable',
                                  JANUS_ERROR_NOT_IMPLEMENTED)
        janus_watcher.stop()
        janus_watcher.start()
    else:
        raise JanusCloudError('Not implement for op {}'.format(params['op']),
                               JANUS_ERROR_NOT_IMPLEMENTED)
    return Response(status=200)


@get_view(route_name='posters')
def get_posters(request):
    poster_list = list_posters()
    return poster_list