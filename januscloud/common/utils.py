# -*- coding: utf-8 -*-
import json
import base64
import datetime
from .error import JanusCloudError
import sys
import traceback


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


def create_janus_msg(status, session_id=0, transaction=None, **kwargs):
    """ create a basic janus message"""
    msg = {"janus": str(status)}
    if session_id > 0:
        msg["session_id"] = session_id
    if transaction:
        msg["transaction"] = str(transaction)
    msg.update(kwargs)
    return msg


def janus_cloud_error_to_janus_msg(session_id=0, transaction=None, exception=None):
    """ convert a Error exception to a message in dict form """
    error = {}
    if isinstance(exception, JanusCloudError):
        error["code"] = exception.code
        error["reason"] = str(exception)
    else:
        error["code"] = 500
        error["reason"] = str(exception)

    type, dummy, tb = sys.exc_info()
    tb_list = traceback.format_list(traceback.extract_tb(tb)[-10:])
    error["traceback"] = tb_list
    return create_janus_msg("error", session_id, transaction, error=error)
