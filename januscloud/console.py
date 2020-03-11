# -*- coding: utf-8 -*-

import sys

import os
import sys
from distutils.dir_util import copy_tree

from pkg_resources import Requirement, resource_filename


def install_conf():
    if len(sys.argv) != 2:
        print('Usage: janus-install-conf [INSTALL_PATH]\n'
              '[INSTALL_PATH] is the filesystem directory to install the related conf file\n')
        sys.exit(-1)

    dst_root_dir = sys.argv[1]

    dst_conf_dir = os.path.join(dst_root_dir, 'conf')
    dst_html_dir = os.path.join(dst_root_dir, 'html')
    dst_certs_dir = os.path.join(dst_root_dir, 'certs')

    src_certs_dir = resource_filename(Requirement.parse("janus-cloud"), "/certs")
    src_html_dir = resource_filename(Requirement.parse("janus-cloud"), "/html")
    src_conf_dir = resource_filename(Requirement.parse("janus-cloud"), "/conf")

    copy_tree(src_conf_dir, dst_conf_dir)
    print('/conf  installed')

    copy_tree(src_html_dir, dst_html_dir)
    print('/html  installed')

    copy_tree(src_certs_dir, dst_certs_dir)
    print('/certs  installed')

    print('Installation completed')

if __name__ == '__main__':
    pass
