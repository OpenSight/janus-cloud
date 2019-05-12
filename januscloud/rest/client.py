# -*- coding: utf-8 -*-
from januscloud.rest.common import get_view


def includeme(config):
    config.add_route('client_list', '/clients')


@get_view(route_name='client_list')
def get_das_list(request):
    # TODO
    return
