# -*- coding: utf-8 -*-
from __future__ import unicode_literals, division
import sys
import os
import logging
import logging.handlers
import logging.config


def default_config(debug=False):
    root = logging.getLogger()
    if debug:
        root.setLevel(logging.DEBUG)
    else:
        root.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s [%(process)d] [%(name)s] [%(levelname)s] - %(message)s')
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    root.addHandler(sh)
    program_name = os.path.basename(sys.argv[0])
    log_path = os.path.join(os.getcwd(), 'logs')
    if not os.path.exists(log_path):
        os.makedirs(log_path)
    rfh = logging.handlers.RotatingFileHandler(os.path.join(log_path, program_name+'.log'),
                                               maxBytes=50*1024*1024,
                                               backupCount=30)
    rfh.setFormatter(formatter)
    root.addHandler(rfh)


def test_config(debug=False):
    import sys
    log = logging.getLogger()
    formatter = logging.Formatter('%(asctime)s [%(process)d] [%(name)s] [%(levelname)s] - %(message)s')
    hdlr = logging.StreamHandler(sys.stdout)
    hdlr.setFormatter(formatter)
    if debug:
        log.setLevel(logging.DEBUG)
    else:
        log.setLevel(logging.INFO)
    log.addHandler(hdlr)
    return log
