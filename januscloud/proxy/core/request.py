# -*- coding: utf-8 -*-

import urllib.parse
import json
import random
import time
import logging
from januscloud.transport.ws import WSServer, WSClient
from januscloud.common.logger import test_config
from januscloud.common.utils import error_to_janus_msg, create_janus_msg
from januscloud.common.error import JanusCloudError, JANUS_ERROR_UNKNOWN_REQUEST
from januscloud.common.schema import Schema, Optional, DoNotCare, \
    Use, IntVal, Default, SchemaError, BoolVal, StrRe, ListVal, Or, STRING, \
    FloatVal, AutoDel

log = logging.getLogger(__name__)



class TransportSession(object):
    """ This class should be sub-class by the transport """

    def send_message(self, message={}):
        """ Method to send a message to a client over a transport session,

        Args:
            message: the dict content to send

        Returns:
            no returns
        Raise:
            TransportError: An transport error occurred when sending the message
        """
        pass

    def session_created(self, session_id=""):
        """ Method to notify the transport that a new janus session has been created from this transport

        Args:
            session_id: The janus session ID that was created (if the transport cares)

        Returns:
            no returns

        """
        pass

    def session_over(self, session_id="", timeout=False, claimed=False):
        """ Method to notify the transport plugin that a session it originated timed out


        Args:
            session_id: The session ID that was closed (if the transport cares)
            timeout: Whether the cause for the session closure is a timeout (this may interest transport plugins more)
            claimed: Whether the cause for the session closure is due to someone claiming the session

        Returns:
            no returns

        """
        pass

    def session_claimed(self, session_id=""):
        """ Method to notify the transport plugin that a session it owned was claimed by another transport

        Args:
            session_id: The session ID that was claimed (if the transport cares)

        Returns:
            no returns

        """
        pass


class Request(object):
    request_schema = Schema({
        "janus": StrRe(r"^\S+$"),
        "transaction": StrRe(r"^\S+$"),
        Optional("session_id"): IntVal(),
        Optional("handle_id"): IntVal(),
        DoNotCare(str): object  # for all other key we don't care
    })

    def __init__(self, transport_session=None, message={}):
        message = self.request_schema.validate(message)
        self.transport_session = transport_session
        self.message = message
        self.janus = message['janus']
        self.transaction = message['transaction']
        self.session_id = message.get('session_id', 0)
        self.handle_id = message.get('handle_id', 0)


class RequestHandler(object):

    def __init__(self, fontend_session_mgr=None, plugin_list=[], proxy_conf={}):
        self._fontend_session_mgr = fontend_session_mgr
        self._plugins_list = plugin_list
        self._proxy_conf = proxy_conf

        pass

    def _handle_info(self, request):
        reply = create_janus_msg('server_info', 0, request.transaction)
        reply['name'] = 'Janus-Cloud Proxy'
        reply['author'] = 'OpenSight'
        reply['email'] = 'public@opensight.cn'
        reply['website'] = 'https://github.com/OpenSight/janus-cloud'
        reply['server_name'] = self._proxy_conf.get('general', {}).get('server_name', 'MyJanusCloudProxy')
        reply['session-timeout'] = self._proxy_conf.get('general', {}).get('session_timeout', 60)

        plugin_info_list = {}
        for plugin in self._plugin_list:
            plugin_info = {

            }
        reply['plugins'] = plugin_info_list

        return reply

    def _handle_ping(self, request):
        return create_janus_msg('pong', 0, request.transaction)

    def _handle_create(self, request):
        return {}

    def _handle_destroy(self, request):
        return {}

    def _handle_keepalive(self, request):
        log.debug('Got a keep-alive on session {0}'.format(request.session_id))
        return create_janus_msg('ack', request.session_id, request.transaction)

    def _handle_attach(self, request):
        return {}

    def _handle_detach(self, request):
        return {}

    def _handle_hangup(self, request):
        return {}

    def _handle_claim(self, request):
        return {}

    def _handle_message(self, request):
        # dispatch to plugin handle
        return {}

    def _handle_trickle(self, request):

        # dispatch to plugin handle

        return {}

    def incoming_request(self, request):
        """ handle the request from the transport module

        Args:
            request: the request to handle

        Returns:
            a dict to response to the initial client

        """

        try:
            handler = getattr(self, '_handle_' + request.janus)
            if handler is None:
                raise JanusCloudError('Unknown request \'{0}\''.format(request.janus), JANUS_ERROR_UNKNOWN_REQUEST)
            return handler(request)
        except Exception as e:
            return error_to_janus_msg(request.session_id, request.transport_session, e)

    def transport_gone(self, transport_session=None):
        """ notify transport session is closed by the transport module """

        pass


if __name__ == '__main__':
    handler = RequestHandler()
    income_message = {
        'janus': 'keepalive',
        'transaction': 'test_1234',
        'session_id': 12345,
        'handle_id': 6789
    }
    request = Request(message=income_message)

    reply = handler.incoming_request(request)

    print(reply)




