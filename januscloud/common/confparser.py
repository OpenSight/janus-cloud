# -*- coding: utf-8 -*-
import yaml
import os

import logging
log = logging.getLogger(__name__)


def parse(file_path, schema=None):
    if not os.path.isabs(file_path):
        file_path = os.path.join(os.getcwd(), file_path)
    if os.path.isfile(file_path):
        with open(file_path) as f:
            conf = yaml.safe_load(f)
    else:
        raise Exception("Configure file {0} does not exit".format(file_path))
    # import pprint
    # pprint.pprint(conf)
    if schema is not None:
        conf = schema.validate(conf)
    return conf
