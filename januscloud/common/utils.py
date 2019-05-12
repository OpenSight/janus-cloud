# -*- coding: utf-8 -*-
import json
import base64
import datetime


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
