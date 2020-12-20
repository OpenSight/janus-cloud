# -*- coding: utf-8 -*-
import json
import base64
import datetime
from .error import JanusCloudError, JANUS_ERROR_INVALID_ELEMENT_TYPE
import sys
import traceback
import random
import time
from .schema import SchemaError
import socket

class CustomJSONEncoder(json.JSONEncoder):

    def __init__(self, *args, **kwargs):
        # dirty hack to keep 'default' method intact
        kwargs.pop('default', None)
        super(CustomJSONEncoder, self).__init__(*args, **kwargs)

    def default(self, o):
        if isinstance(o, bytes):
            o = base64.b64encode(o).decode()
            return o
        elif isinstance(o, datetime.datetime):
            return o.strftime('%Y-%m-%dT%H:%M:%S')
        elif isinstance(o, datetime.time):
            return o.strftime('%H:%M:%S')
        elif isinstance(o, datetime.date):
            return o.strftime('%Y-%m-%d')
        elif isinstance(o, set):
            return list(o)
        elif hasattr(o, '__json__'):
            return o.__json__()
        elif hasattr(o, '__dict__'):
            obj_dict = {}
            for k, v in o.__dict__.items():
                if not k.startswith('_'):
                    obj_dict[k] = v
            return obj_dict
        else:
            return json.JSONEncoder.default(self, o)


def create_janus_msg(method, session_id=0, transaction=None, **kwargs):
    """ create a basic janus message"""
    msg = {"janus": str(method)}
    if session_id > 0:
        msg["session_id"] = session_id
    if transaction:
        msg["transaction"] = str(transaction)
    msg.update(kwargs)
    return msg


def error_to_janus_msg(session_id=0, transaction=None, exception=None):
    """ convert a Error exception to a message in dict form """
    error = {}
    if isinstance(exception, JanusCloudError):
        error["code"] = exception.code
        error["reason"] = str(exception)
    elif isinstance(exception, SchemaError):
        error["code"] = JANUS_ERROR_INVALID_ELEMENT_TYPE
        error["reason"] = str(exception)
    else:
        error["code"] = 500
        error["reason"] = str(exception)

    type, dummy, tb = sys.exc_info()
    tb_list = traceback.format_list(traceback.extract_tb(tb)[-10:])
    error["traceback"] = tb_list
    return create_janus_msg("error", session_id, transaction, error=error)


def get_monotonic_time():
    return time.monotonic()


def random_uint64():
    return random.randint(1, 9007199254740991)


def random_uint32():
    return random.randint(1, 2147483647)


def get_host_ip():
    ip = None
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    finally:
        if s:
            s.close()

    return ip

def to_redis_hash(o):
    if hasattr(o, "__redis__"):
        return o.__redis__()
    elif hasattr(o, "__dict__"):
        obj_dict = {}
        for k, v in o.__dict__.items():
            if not k.startswith("_"):
                if v is None:
                    v = ""
                elif v is False:
                    v = ''
                elif v is True:
                    v = '1'
                elif isinstance(v, list):
                    v = ','.join(v)
                elif isinstance(v, set):
                    v = ','.join(v)
                obj_dict[k] = v
        return obj_dict