# -*- coding: utf-8 -*-
from januscloud.proxy.rest.common import get_view


def includeme(config):
    config.add_route('client_list', '/clients')


@get_view(route_name='client_list')
def get_client_list(request):
    # TODO
    return {'list': [1, 2, 3]}
