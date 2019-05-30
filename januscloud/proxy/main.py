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
from januscloud.transport.ws import WSServer
from januscloud.proxy.core.request import RequestHandler
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

        cert_pem_file = None
        cert_key_file = None
        if config['ws_transport'].get('wss'):
            if 'certificates' not in config:
                raise Exception('No SSL keyfile or certfile given')
            cert_pem_file = config['certificates'].get('cert_pem')
            cert_key_file = config['certificates'].get('cert_key')
            if not os.path.isfile(cert_pem_file) or not os.path.isfile(cert_key_file):
                raise Exception('SSL keyfile or cerfile not found')

        # load the core
        request_handler = RequestHandler()

        # start admin rest api server
        pyramid_config = Configurator()
        pyramid_config.add_renderer(None, JSON(indent=4, check_circular=True, cls=CustomJSONEncoder))
        pyramid_config.include('januscloud.proxy.rest', route_prefix=config['admin_api']['api_base_path'])
        # TODO register service to pyramid registry
        # pyramid_config.registry.das_mngr = das_mngr

        # set up all server
        server_list = []
        rest_server = WSGIServer(
            config['admin_api']['http_listen'],
            pyramid_config.make_wsgi_app(),
            log=logging.getLogger('rest server')
        )
        server_list.append(rest_server)
        if config['ws_transport']['wss']:
            wss_server = WSServer(
                config['ws_transport']['wss_listen'],
                request_handler,
                keyfile=cert_key_file,
                certfile=cert_pem_file
            )
            server_list.append(wss_server)
            log.info('Transport wss server listens at wss://{0}'.format(config['ws_transport']['wss_listen']))
        if config['ws_transport']['ws']:
            ws_server = WSServer(config['ws_transport']['ws_listen'], request_handler)
            server_list.append(ws_server)
        log.info('Started Janus Proxy')

        def stop_server():
            log.info('Janus Proxy receives signals to quit...')
            for server in server_list:
                server.stop()

        gevent.signal(signal.SIGTERM, stop_server)
        gevent.signal(signal.SIGQUIT, stop_server)
        gevent.signal(signal.SIGINT, stop_server)

        serve_forever(server_list)  # serve all server

        log.info("Quit")

    except Exception:
        log.exception('Failed to start Janus Proxy')


def serve_forever(server_list):
    server_greenlets = []
    for server in server_list:
        server_greenlets.append(gevent.spawn(server.serve_forever))
    gevent.joinall(server_greenlets)


if __name__ == '__main__':
    main()
