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
                                               maxBytes=100*1024*1024,
                                               backupCount=30)
    rfh.setFormatter(formatter)
    root.addHandler(rfh)


def set_root_logger(log_to_stdout, log_to_file, debug_level='DEBUG', log_file_size=100*1024*1024, log_file_rotate=10):
    root = logging.getLogger()
    root.setLevel(debug_level)
    formatter = logging.Formatter('%(asctime)s [%(process)d] [%(name)s] [%(levelname)s] - %(message)s')
    if log_to_stdout:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(formatter)
        root.addHandler(sh)
    if log_to_file:
        log_path = os.path.dirname(log_to_file)
        if not os.path.exists(log_path):
            os.makedirs(log_path)
        rfh = logging.handlers.RotatingFileHandler(log_to_file,
                                                   maxBytes=log_file_size,
                                                   backupCount=log_file_rotate)
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
