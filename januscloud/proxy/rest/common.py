# -*- coding: utf-8 -*-
from __future__ import unicode_literals, division
import sys
import traceback
import inspect
import copy
import pyramid.exceptions
from pyramid.view import view_config
from pyramid.events import subscriber, NewResponse
from januscloud.common.schema import SchemaError as ValidationFailure
from januscloud.common.error import JanusCloudError

import logging
log = logging.getLogger(__name__)


class PrefligthHandlerFactory(object):
    def __init__(self, route_name, request_method):
        self.route_name = route_name
        self.allow_methods = {request_method}
        self._allow_methods_header = request_method

    def add_method(self, request_method):
        self.allow_methods.add(request_method)
        self._allow_methods_header = ', '.join(self.allow_methods)

    def __call__(self, request):
        response = request.response
        if request.headers.get('Access-Control-Request-Method') not in self.allow_methods:
            response.status_int = 401
        else:
            response.headers[str('Access-Control-Allow-Methods')] = str(self._allow_methods_header)
            response.headers[str('Access-Control-Allow-Headers')] = str('Origin, X-Requested-With, Content-Type, Accept, Authorization')
            response.headers[str('Access-Control-Max-Age')] = str(86400)


class _rest_view(view_config):
    cors_route = {}

    def __init__(self, **settings):
        method = self.__class__.__name__.split('_')[0].upper()
        super(_rest_view, self).__init__(request_method=method,
                                         **settings)
        # add CORS OPTIONS method support for registered REST view
        route_name = settings['route_name']
        if route_name in self.cors_route:
            self.cors_route[route_name].add_method(method)
            return
        handler = PrefligthHandlerFactory(route_name, method)
        self.cors_route[route_name] = handler
        view_config(request_method='OPTIONS', route_name=route_name, _depth=1)(handler)
        # dirty hack
        # to get caller's module, in order to inject preflight_handler to that module
        # so when scan configuration, pyramid will pick OPTIONS for that route
        module = inspect.getmodule(inspect.getouterframes(inspect.currentframe())[0][0].f_back)
        setattr(module, 'preflight_'+route_name, handler)


class get_view(_rest_view):
    pass


class post_view(_rest_view):
    pass


class put_view(_rest_view):
    pass


class delete_view(_rest_view):
    pass


@view_config(context=ValidationFailure)
def failed_validation(exc, request):
    response = request.response
    response.status_int = 400
    typ, dummy, tb = sys.exc_info()
    tb_list = traceback.format_list(traceback.extract_tb(tb)[-10:])
    log.error('Error handling request\n{0}\n{1}'.format(str(exc), ' '.join(tb_list)))
    return {'info': str(exc), 'exception': str(typ), 'traceback': tb_list}


@view_config(context=JanusCloudError)
def ivr_error_view(exc, request):
    response = request.response
    response.status_int = exc.code
    typ, dummy, tb = sys.exc_info()
    tb_list = traceback.format_list(traceback.extract_tb(tb)[-10:])
    log.error('Error handling request\n{0}\n{1}'.format(str(exc), ' '.join(tb_list)))
    return {'info': str(exc), 'exception': str(typ), 'traceback': tb_list}


@view_config(context=Exception)
def error_view(exc, request):
    response = request.response
    response.status_int = 500
    typ, dummy, tb = sys.exc_info()
    tb_list = traceback.format_list(traceback.extract_tb(tb)[-10:])
    log.error('Error handling request\n{0}\n{1}'.format(str(exc), ' '.join(tb_list)))
    return {'info': str(exc), 'exception': str(typ), 'traceback': tb_list}


@view_config(context=pyramid.exceptions.NotFound)
def not_found_view(exc, request):
    response = request.response
    response.status_int = exc.status_code
    type, dummy, tb = sys.exc_info()
    return {'info': 'Resource {0} not found or method {1} not supported'.format(request.path, request.method),
            'exception': str(type),
            'traceback': []}


@view_config(context=pyramid.exceptions.Forbidden)
def forbidden_view(exc, request):
    response = request.response
    response.status_int = exc.status_code
    type, dummy, tb = sys.exc_info()
    return {'info': 'Resource {0} for method {1} is Forbidden'.format(request.path, request.method),
            'exception': str(type),
            'traceback': []}


@subscriber(NewResponse)
def add_response_header(event):
    """
    add all custom header here
    """
    response = event.response
    response.headers[str('X-Powered-By')] = str('Pyramid Framework')
    response.headers[str('Access-Control-Allow-Origin')] = str('*')


def get_params_from_request(request, schema=None):
    """Get input parameter dict from request

    If the request content type is json, get the params dict from json body,
    otherwise, from GET/POST params.
    If shema is not None, check the input params dict against the schema.

    return the params dict.


    :param request: request object
    :param schema:  the schema for the input params

    """
    params = dict(request.params)
    if "json" in request.content_type and request.content_length > 0:
        if isinstance(request.json_body, dict):
            params.update(request.json_body)
        else:
            params = copy.copy(request.json_body)

    if schema is not None:
        params = schema.validate(params)

    return params
