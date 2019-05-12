# -*- coding: utf-8 -*-
import gevent.monkey
gevent.monkey.patch_all()
import sys
from gevent.pywsgi import WSGIServer
from pyramid.config import Configurator
from pyramid.renderers import JSON
from januscloud.common.utils import CustomJSONEncoder
from januscloud.common.logger import default_config as default_log_config
from januscloud.common.confparser import parse as parse_config
from januscloud.common.schema import Schema, StrVal


config_schema = Schema({
    'listen': StrVal(min_len=1, max_len=128),
})


def main():

    default_log_config(debug=False)
    import logging
    log = logging.getLogger(__name__)

    try:
        if len(sys.argv) == 2:
            config = parse_config(sys.argv[1])
        else:
            config = parse_config('/etc/januscloud.yml')

        pyramid_config = Configurator()
        pyramid_config.add_renderer(None, JSON(indent=4, check_circular=True, cls=CustomJSONEncoder))
        pyramid_config.include('januscloud.rest', route_prefix='api/januscloud/v1')
        # pyramid_config.registry.das_mngr = das_mngr

        rest_server = WSGIServer(
            config['rest_listen'],
            pyramid_config.make_wsgi_app(),
            log=logging.getLogger('rest server')
        )
        #ws_server = WSServer(config['ws_listen'], das_mngr.ivt_online)
        log.info('Started Janus Cloud')

        gevent.joinall(map(gevent.spawn, (ws_server.server_forever, rest_server.serve_forever)))
        log.info("Quit")

    except Exception:
        log.exception('Failed to start Janus Cloud')


if __name__ == '__main__':
    main()
