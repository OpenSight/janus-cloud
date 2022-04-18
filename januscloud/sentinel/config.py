# -*- coding: utf-8 -*-
from januscloud.common.error import JanusCloudError, JANUS_ERROR_NOT_IMPLEMENTED
from januscloud.common.schema import Schema, StrVal, Default, AutoDel, Optional, BoolVal, IntVal, \
    StrRe, EnumVal, Or, DoNotCare
from januscloud.common.confparser import parse as parse_config
import os
# import logging

# log = logging.getLogger(__name__)

config_schema = Schema({
    Optional("general"): Default({
        Optional("daemonize"): Default(BoolVal(), default=False),
        AutoDel(str): object  # for all other key remove
    }, default={}),
    Optional("log"): Default({
        Optional('log_to_stdout'): Default(BoolVal(), default=True),
        Optional('log_to_file'): Default(StrVal(), default=''),
        Optional('debug_level'): Default(EnumVal(['DEBUG', 'INFO', 'WARN', 'ERROR', 'FATAL']), default='DEBUG'),
        Optional('log_file_size'): Default(IntVal(), default=104857600),
        Optional('log_file_rotate'): Default(IntVal(), default=10),
        AutoDel(str): object  # for all other key remove
    }, default={}),
    Optional("janus"): Default({
        Optional("server_name"): Default(StrVal(min_len=0, max_len=64), default=''),
        Optional("server_ip"): Default(StrVal(), default='127.0.0.1'),
        Optional("public_ip"): Default(StrVal(), default=''),
        Optional("ws_port"): Default(IntVal(min=0, max=65536), default=8188),
        Optional("admin_ws_port"): Default(IntVal(min=0, max=65536), default=0),
        Optional("pingpong_interval"): Default(IntVal(min=1, max=3600), default=5),
        Optional("statistic_interval"): Default(IntVal(min=1, max=3600), default=10),
        Optional("request_timeout"): Default(IntVal(min=1, max=3600), default=10),
        Optional("hwm_threshold"): Default(IntVal(min=0, max=300), default=0),
        Optional('admin_secret'): Default(StrVal(), default=''),
        Optional("location"): Default(StrVal(min_len=0, max_len=64), default=''),
        Optional("isp"): Default(StrVal(min_len=0, max_len=64), default=''),
        AutoDel(str): object  # for all other key remove
    }, default={}),
    Optional("videoroom_sweeper"): Default({
        Optional("enable"): Default(BoolVal(), default=True),
        Optional("check_interval"): Default(IntVal(min=1, max=86400), default=30),
        Optional("room_auto_destroy_timeout"): Default(IntVal(min=1, max=86400), default=600),
        Optional("des_filter"): Default(StrVal(min_len=0, max_len=64), default='januscloud-'),
        AutoDel(str): object  # for all other key remove
    }, default={}),
    Optional("proc_watcher"): Default({
        Optional("cmdline"): Default(StrVal(), default=''),
        Optional("error_restart_interval"): Default(IntVal(min=0, max=86400), default=10),
        Optional("poll_interval"): Default(IntVal(min=1, max=3600), default=1),
        AutoDel(str): object  # for all other key remove
    }, default={}),
    Optional("admin_api"): Default({
        Optional("json"): Default(EnumVal(['indented', 'plain', 'compact']), default='indented'),
        Optional("http_listen"): Default(StrRe('^\S+:\d+$'), default='0.0.0.0:8200'),
        AutoDel(str): object  # for all other key we don't care
    }, default={}),
    Optional("posters"): Default([{
        "post_type": StrVal(min_len=1, max_len=64),
        "name": StrVal(min_len=0, max_len=64),
        DoNotCare(str): object  # for all other key we don't care
    }], default=[]),
})
http_poster_schema = Schema({
    "post_type": StrVal(min_len=1, max_len=64),
    "name": StrVal(min_len=0, max_len=64),
    "post_urls": [StrRe(r'(http|https)://')],
    Optional("expire"): Default(IntVal(min=1, max=3600), default=60),
    Optional("http_timeout"): Default(IntVal(min=1, max=3600), default=10),
    AutoDel(str): object  # for all other key, remove
})


def load_conf(path):
    if path is None or path == '':
        config = config_schema.validate({})
    else:
        print('Janus-sentinel loads the config file: {}'.format(os.path.abspath(path)))
        config = parse_config(path, config_schema)

    for i in range(len(config['posters'])):
        if config['posters'][i]['post_type'] == 'http':
            config['posters'][i] = http_poster_schema.validate(config['posters'][i])
        else:
            raise JanusCloudError('poster_type {} not support'.format(config['posters'][i]['post_type']),
                                  JANUS_ERROR_NOT_IMPLEMENTED)

    # check other configure option is valid or not
    # TODO

    return config


if __name__ == '__main__':
    conf = config_schema.validate({})
    import pprint
    pprint.pprint(conf)
