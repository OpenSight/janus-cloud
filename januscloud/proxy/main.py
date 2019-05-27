# -*- coding: utf-8 -*-
import gevent.monkey
gevent.monkey.patch_all()
import sys
import os.path
import signal
from gevent.pywsgi import WSGIServer
from pyramid.config import Configurator
from pyramid.renderers import JSON
from januscloud.common.utils import CustomJSONEncoder
from januscloud.common.logger import default_config as default_log_config
from januscloud.common.confparser import parse as parse_config
from januscloud.common.schema import Schema, StrVal, BoolVal, Optional, Default, IntVal, ListVal, EnumVal
from januscloud.transport.ws import WSServer
from januscloud.proxy.config import load_conf




def main():

    default_log_config(debug=False)
    import logging
    log = logging.getLogger(__name__)

    try:
        if len(sys.argv) == 2:
            config = load_conf(sys.argv[1])
        else:
            config = load_conf('/etc/janus-proxy.yml')
        '''
        jiankai: use a default keyfile and certfile in the distribution
        if config['enable_https'] or config['enable_wss']:
            if not config['ssl_keyfile'] or not config['ssl_certfile']:
                raise Exception('No SSL keyfile or certfile given')
            if not os.path.isfile(config['ssl_keyfile']) or not os.path.isfile(config['ssl_certfile']):
                raise Exception('SSL keyfile or cerfile not found')
        '''

        # load the plugins


        # load the core
        from januscloud.proxy.core.request import RequestHandler
        request_handler = RequestHandler()




        # set up all server

        server_list = []

        # start admin rest api server
        pyramid_config = Configurator()
        pyramid_config.add_renderer(None, JSON(indent=4, check_circular=True, cls=CustomJSONEncoder))
        pyramid_config.include('januscloud.proxy.rest', route_prefix=config['admin_api']['api_base_path'])
        # TODO register service to pyramid registry
        # pyramid_config.registry.das_mngr = das_mngr
        rest_server = WSGIServer(
            config['admin_api']['http_listen'],
            pyramid_config.make_wsgi_app(),
            log=logging.getLogger('rest server')
        )
        server_list.append(rest_server)
        log.info('Admin RESTAPI server startup at http://{0}'.format(config['admin_api']['http_listen']))

        # start ws transport server
        if config['ws_transport']['wss']:
            # TODO replace lambda with incoming connection handling function
            wss_server = WSServer(
                config['ws_transport']['wss_listen'],
                lambda conn, **args: None,
                keyfile=config['certificates']['cert_key'],
                certfile=config['ssl_certfile']
            )
            server_list.append(wss_server)
            log.info('Transport wss server startup at wss://{0}'.format(config['ws_transport']['wss_listen']))

        if config['ws_transport']['ws']:
            # TODO replace lambda with incoming connection handling function
            ws_server = WSServer(
                config['ws_transport']['ws_listen'],
                lambda conn, **args: None)

            server_list.append(ws_server)
            log.info('Transport ws server startup successful at ws://{0}'.format(config['ws_transport']['wss_listen']))

        log.info('Janus Proxy is launched successfully')

        def stop_server():
            for server in server_list:
                server.stop()

        gevent.signal(signal.SIGTERM, stop_server)
        gevent.signal(signal.SIGQUIT, stop_server)
        gevent.signal(signal.SIGINT, stop_server)

        serve_forever(server_list) # serve all server

        log.info("Quit")

    except Exception:
        log.exception('Failed to start Janus Proxy')


def serve_forever(server_list):
    server_greenlets = []
    for server in server_list:
        server_list.append(gevent.spawn(server.serve_forever))
    gevent.joinall(server_greenlets)


if __name__ == '__main__':
    main()
