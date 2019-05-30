# -*- coding: utf-8 -*-

from januscloud.common.schema import Schema, StrVal, Default, AutoDel, Optional, BoolVal, IntVal, \
    StrRe, EnumVal
from januscloud.common.confparser import parse as parse_config


config_schema = Schema({
    Optional("general"): Default({
        Optional("daemonize"): Default(BoolVal(), default=False),
        Optional("configs_folder"): Default(StrVal(), default='/etc/janus-cloud'),
        Optional("server_name"): Default(StrVal(min_len=1, max_len=64), default='MyJanusProxy'),
        Optional("session_timeout"): Default(IntVal(min=0, max=86400), default=60),
        AutoDel(str): object  # for all other key we don't care
    }, default={}),
    Optional("log"): Default({
        Optional('log_to_stdout'): Default(BoolVal(), default=True),
        Optional('log_to_file'): Default(StrVal(), default=''),
        Optional('debug_level'): Default(IntVal(), default=4),
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
        Optional("http_listen"): Default(StrRe('^\S+:\d+$'), default='0.0.0.0:8200'),
        Optional("api_base_path"): Default(StrVal(), default='/janus-proxy'),
        AutoDel(str): object  # for all other key we don't care
    }, default={}),
})


def load_conf(path):
    config = parse_config(path, config_schema)

    # set up the default cert pathname
    # TODO

    # check other configure option is valid or not
    # TODO

    return config


if __name__ == '__main__':
    conf = config_schema.validate({})
    import pprint
    pprint.pprint(conf)
