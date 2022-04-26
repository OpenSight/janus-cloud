# -*- coding: utf-8 -*-

from januscloud.common.schema import Schema, StrVal, Default, AutoDel, Optional, BoolVal, IntVal, \
    StrRe, EnumVal, Or
from januscloud.common.confparser import parse as parse_config
from pkg_resources import Requirement, resource_filename
import os
import logging

log = logging.getLogger(__name__)

config_schema = Schema({
    Optional("general"): Default({
        Optional("daemonize"): Default(BoolVal(), default=False),
        Optional("configs_folder"): Default(StrVal(), default=''),
        Optional("server_name"): Default(StrRe(r'^\S*$'), default=''),
        Optional("session_timeout"): Default(IntVal(min=0, max=86400), default=60),
        Optional("server_db"): Default(StrVal(), default='memory'),
        Optional("server_select"): Default(StrVal(), default='rr'),
        Optional('api_secret'): Default(StrVal(), default=''),
        AutoDel(str): object  # for all other key we don't care
    }, default={}),
    Optional("log"): Default({
        Optional('log_to_stdout'): Default(BoolVal(), default=True),
        Optional('log_to_file'): Default(StrVal(), default=''),
        Optional('debug_level'): Default(EnumVal(['DEBUG', 'INFO', 'WARN', 'ERROR', 'FATAL']), default='DEBUG'),
        Optional('log_file_size'): Default(IntVal(), default=104857600),
        Optional('log_file_rotate'): Default(IntVal(), default=10),
        AutoDel(str): object  # for all other key we don't care
    }, default={}),
    Optional("certificates"): Default({
        Optional("cert_pem"): StrVal(),
        Optional("cert_key"): StrVal(),
        Optional("cert_pwd"): StrVal(),
        AutoDel(str): object  # for all other key we don't care
    }, default={}),
    Optional("plugins"): Default([StrRe('^\S+:\S+$')], default=[]),
    Optional("ws_transport"): Default({
        Optional("json"): Default(EnumVal(['indented', 'plain', 'compact']), default='indented'),
        Optional("pingpong_trigger"): Default(IntVal(min=0, max=3600), default=0),
        Optional("pingpong_timeout"): Default(IntVal(min=1, max=3600), default=30),
        Optional("ws"): Default(BoolVal(), default=False),
        Optional("ws_listen"): Default(StrRe('^\S+:\d+$'), default='0.0.0.0:8288'),
        Optional("wss"): Default(BoolVal(), default=False),
        Optional("wss_listen"): Default(StrRe('^\S+:\d+$'), default='0.0.0.0:8289'),
        Optional("max_greenlet_num"): Default(IntVal(min=0, max=10000), default=1000),
        AutoDel(str): object  # for all other key we don't care
    }, default={}),
    Optional("admin_api"): Default({
        Optional("json"): Default(EnumVal(['indented', 'plain', 'compact']), default='indented'),
        Optional("http_listen"): Default(StrRe('^\S+:\d+$'), default='0.0.0.0:8100'),
        AutoDel(str): object  # for all other key we don't care
    }, default={}),
    Optional("janus_server"): Default([{
        "name": StrRe('^[\w-]{1,64}$'),
        "url": StrRe('^(ws|wss)://\S+$'),
        Optional("status"): Default(IntVal(values=(0, 1)), default=0),
        Optional("session_timeout"): Default(IntVal(min=0, max=86400), default=60),
        Optional("session_num"): Default(IntVal(min=0, max=10000), default=0),
        Optional("handle_num"): Default(IntVal(min=0, max=100000), default=0),
        Optional("location"): Default(StrVal(min_len=0, max_len=64), default=''),
        Optional("isp"): Default(StrVal(min_len=0, max_len=64), default=''),
        AutoDel(str): object  # for all other key we don't care
    }], default=[]),
})


def load_conf(path):
    if path is None or path == '':
        config = config_schema.validate({})
    else:
        print('Janus-proxy loads the config file: {}'.format(os.path.abspath(path)))
        config = parse_config(path, config_schema)

    # set up the default cert pathname
    if config['certificates'].get('cert_key') is None:
        config['certificates']['cert_key'] = resource_filename("januscloud", "certs/mycert.key")
    if config['certificates'].get('cert_pem') is None:
        config['certificates']['cert_pem'] = resource_filename("januscloud", "certs/mycert.pem")

    # check other configure option is valid or not
    # TODO

    if config['general']['configs_folder'] == '':
        if path is None or path == '':
            config['general']['configs_folder'] = '/etc/janus-cloud'
        else:
            config['general']['configs_folder'] = os.path.dirname(os.path.abspath(path))

    # print('configs_folders: {}'.format(config['general']['configs_folder']))

    return config


if __name__ == '__main__':
    conf = config_schema.validate({})
    import pprint
    pprint.pprint(conf)
