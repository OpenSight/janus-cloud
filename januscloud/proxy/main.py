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


config_schema = Schema({
    'general': {
        Optional('configs_folder'): StrVal(),
        Optional('server_name'): Default(StrVal(min_len=1, max_len=64), default='JanusProxy'),
        Optional('session_timeout'): Default(IntVal(), default=60)
    },
    Optional('log'): {
        Optional('log_to_stdout'): Default(BoolVal(), default=False),
        Optional('log_to_file'): StrVal(),
        Optional('debug_level'): Default(IntVal(), default=4)
    },
    Optional('certificates'): {
        Optional('cert_pem'): StrVal(),
        Optional('cert_key'): StrVal(),
        Optional('cert_pwd'): StrVal()
    },
    'plugins': ListVal(StrVal()),
    'ws_transport': {
        'ws': BoolVal(),
        Optional('ws_listen'): StrVal(),
        Optional('wss'): Default(BoolVal(), default=False),
        Optional('ws_listen'): StrVal(),
        Optional('max_greenlet_num'): Default(IntVal(), default=1024),

        Optional('json'): Default(EnumVal(('indented', 'plain', 'compact'))),
        Optional('pingpong_trigger'): Default(IntVal(), default=30),
        Optional('pingpong_timeout'): Default(IntVal(), default=10),
    },
    'admin_api': {
        'http_listen': StrVal(),
        Optional('api_base_path'): Default(StrVal(), default='/janus-proxy'),
        Optional('json'): Default(EnumVal(('indented', 'plain', 'compact'))),
    }
})


def main():

    default_log_config(debug=False)
    import logging
    log = logging.getLogger(__name__)

    try:
        if len(sys.argv) == 2:
            config = parse_config(sys.argv[1])
        else:
            config = parse_config('/etc/janus-proxy.yml')
        if config['enable_https'] or config['enable_wss']:
            if not config['ssl_keyfile'] or not config['ssl_certfile']:
                raise Exception('No SSL keyfile or certfile given')
            if not os.path.isfile(config['ssl_keyfile']) or not os.path.isfile(config['ssl_certfile']):
                raise Exception('SSL keyfile or cerfile not found')

        # load the core
        from januscloud.proxy.core.request import RequestHandler
        request_handler = RequestHandler()


        pyramid_config = Configurator()
        pyramid_config.add_renderer(None, JSON(indent=4, check_circular=True, cls=CustomJSONEncoder))
        pyramid_config.include('januscloud.proxy.rest', route_prefix='janus-proxy')
        # TODO register service to pyramid registry
        # pyramid_config.registry.das_mngr = das_mngr

        if config['enable_https']:
            rest_server = WSGIServer(
                config['rest_listen'],
                pyramid_config.make_wsgi_app(),
                log=logging.getLogger('rest server'),
                keyfile=config['ssl_keyfile'],
                certfile=config['ssl_certfile']
            )
        else:
            rest_server = WSGIServer(
                config['rest_listen'],
                pyramid_config.make_wsgi_app(),
                log=logging.getLogger('rest server')
            )
        if config['enable_wss']:
            # TODO replace lambda with incoming connection handling function
            ws_server = WSServer(
                config['ws_listen'],
                lambda conn, **args: None,
                keyfile=config['ssl_keyfile'],
                certfile=config['ssl_certfile']
            )
        else:
            # TODO replace lambda with incoming connection handling function
            ws_server = WSServer(config['ws_listen'], lambda conn, **args: None)
        log.info('Started Janus Proxy')

        def stop_server():
            rest_server.stop(timeout=5)
            ws_server.stop()

        gevent.signal(signal.SIGTERM, stop_server)
        gevent.signal(signal.SIGQUIT, stop_server)
        gevent.signal(signal.SIGINT, stop_server)

        gevent.joinall(list(map(gevent.spawn, (ws_server.server_forever, rest_server.serve_forever))))
        log.info("Quit")

    except Exception:
        log.exception('Failed to start Janus Proxy')


if __name__ == '__main__':
    main()
