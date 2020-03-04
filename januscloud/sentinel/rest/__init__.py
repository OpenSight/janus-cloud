# -*- coding: utf-8 -*-


def includeme(config):
    # look into following modules' includeme function
    # in order to register routes
    config.include(__name__ + '.sentinel_view')
    config.scan('januscloud.proxy.rest.common')
    config.scan()
