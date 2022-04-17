# -*- coding: utf-8 -*-
import gevent.monkey
gevent.monkey.patch_all()
import sys
from januscloud.proxy.config import load_conf
from daemon import DaemonContext
import os

def main():
    if len(sys.argv) == 2:
        config = load_conf(sys.argv[1])
    else:
        config = load_conf('/opt/janus-cloud/conf/janus-proxy.yml')

    if config['general']['daemonize']:
        with DaemonContext(stdin=sys.stdin,
                           stdout=sys.stdout,
                           # Change the current working directory to the root
                           # so we won't prevent file systems from being unmounted.
                           # working_directory=os.getcwd(),
                           files_preserve=list(range(3, 100))):
            do_main(config)
    else:
        do_main(config)


def do_main(config):

    import signal
    from gevent.pywsgi import WSGIServer
    from pyramid.config import Configurator
    from pyramid.renderers import JSON
    from januscloud.common.utils import CustomJSONEncoder
    from januscloud.common.logger import set_root_logger
    from januscloud.transport.ws import WSServer
    import importlib
    from januscloud.common.error import JanusCloudError, JANUS_ERROR_NOT_IMPLEMENTED
    import gevent

    set_root_logger(**(config['log']))

    import logging
    log = logging.getLogger(__name__)

    try:
        log.info('Janus Proxy is starting...')
        cert_pem_file = config['certificates'].get('cert_pem')
        cert_key_file = config['certificates'].get('cert_key')

        # load the dao
        from januscloud.proxy.dao.mem_server_dao import MemServerDao
        if config['general']['server_db'].startswith('memory'):
            server_dao = MemServerDao()
        elif config['general']['server_db'].startswith('redis://'):
            import redis
            from januscloud.proxy.dao.rd_server_dao import RDServerDao
            connection_pool = redis.BlockingConnectionPool.from_url(
                url=config['general']['server_db'],
                decode_responses=True,
                health_check_interval=30,
                timeout=10)
            redis_client = redis.Redis(connection_pool=connection_pool)
            server_dao = RDServerDao(redis_client)
        else:
            raise JanusCloudError('server_db url {} not support'.format(config['general']['server_db']),
                                  JANUS_ERROR_NOT_IMPLEMENTED)


        # load the core
        from januscloud.core.frontend_session import FrontendSessionManager
        frontend_session_mgr = FrontendSessionManager(session_timeout=config['general']['session_timeout'])
        from januscloud.core.request import RequestHandler
        request_handler = RequestHandler(frontend_session_mgr=frontend_session_mgr, proxy_conf=config)
        from januscloud.core.backend_server import BackendServerManager
        backend_server_manager = BackendServerManager(config['general']['server_select'],
                                                      config['janus_server'],
                                                      server_dao)
        from januscloud.core.backend_session import set_api_secret
        set_api_secret(config['general']['api_secret'])

        # rest api config
        pyramid_config = Configurator()
        pyramid_config.add_renderer(None, JSON(indent=4, check_circular=True, cls=CustomJSONEncoder))
        pyramid_config.include('januscloud.proxy.rest')
        # TODO register service to pyramid registry
        pyramid_config.registry.backend_server_manager = backend_server_manager
        pyramid_config.registry.proxy_conf = config


        # load the plugins
        from januscloud.core.plugin_base import register_plugin
        for plugin_str in config['plugins']:
            module_name, sep, factory_name = plugin_str.partition(':')
            module = importlib.import_module(module_name)
            plugin_factory = getattr(module, factory_name)
            plugin = plugin_factory(config, backend_server_manager, pyramid_config)
            register_plugin(plugin.get_package(), plugin)


        # start admin rest api server


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
                msg_handler_pool_size=config['ws_transport']['max_greenlet_num'],
                indent=config['ws_transport']['json'],
                keyfile=cert_key_file,
                certfile=cert_pem_file,
                pingpong_trigger=config['ws_transport']['pingpong_trigger'],
                pingpong_timeout=config['ws_transport']['pingpong_timeout'],

            )
            server_list.append(wss_server)

        if config['ws_transport']['ws']:
            ws_server = WSServer(
                config['ws_transport']['ws_listen'],
                request_handler,
                msg_handler_pool_size=config['ws_transport']['max_greenlet_num'],
                indent=config['ws_transport']['json'],
                pingpong_trigger=config['ws_transport']['pingpong_trigger'],
                pingpong_timeout=config['ws_transport']['pingpong_timeout'],
            )
            server_list.append(ws_server)

        log.info('Janus Proxy launched successfully')

        def stop_server():
            log.info('Janus Proxy receives signals to quit...')
            for server in server_list:
                server.stop()

        gevent.signal_handler(signal.SIGTERM, stop_server)
        gevent.signal_handler(signal.SIGQUIT, stop_server)
        gevent.signal_handler(signal.SIGINT, stop_server)

        serve_forever(server_list)  # serve all server

        log.info("Janus-proxy Quit")

    except Exception:
        log.exception('Fail to start Janus Proxy')


def serve_forever(server_list):
    server_greenlets = []
    for server in server_list:
        server_greenlets.append(gevent.spawn(server.serve_forever))
    gevent.joinall(server_greenlets)


if __name__ == '__main__':
    main()
