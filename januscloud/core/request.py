# -*- coding: utf-8 -*-

import logging
from januscloud.common.utils import error_to_janus_msg, create_janus_msg
from januscloud.common.error import JanusCloudError, JANUS_ERROR_UNKNOWN_REQUEST, JANUS_ERROR_INVALID_REQUEST_PATH, \
    JANUS_ERROR_PLUGIN_MESSAGE, JANUS_ERROR_HANDLE_NOT_FOUND, JANUS_ERROR_SESSION_NOT_FOUND, \
    JANUS_ERROR_MISSING_MANDATORY_ELEMENT, JANUS_ERROR_INVALID_JSON, JANUS_ERROR_UNAUTHORIZED
from januscloud.common.schema import Schema, Optional, DoNotCare, \
    Use, IntVal, Default, SchemaError, BoolVal, StrRe, ListVal, Or, StrVal, \
    FloatVal, AutoDel
from januscloud.core.frontend_handle_base import JANUS_PLUGIN_OK, JANUS_PLUGIN_OK_WAIT
from januscloud.core.plugin_base import get_plugin_list

log = logging.getLogger(__name__)


class TransportSession(object):
    """ This class should be sub-class by the transport """

    def send_message(self, message):
        """ Method to send a message to a client over a transport session,

        Args:
            message: the dict content to send

        Returns:
            no returns
        Raise:
            TransportError: An transport error occurred when sending the message
        """
        pass

    def session_created(self, session_id):
        """ Method to notify the transport that a new janus session has been created from this transport

        Args:
            session_id: The janus session ID that was created (if the transport cares)

        Returns:
            no returns

        Raise:
            no exception
        Note:
            no IO, no block
        """
        pass

    def session_over(self, session_id, timeout=False, claimed=False):
        """ Method to notify the transport plugin that a session it originated timed out


        Args:
            session_id: The session ID that was closed (if the transport cares)
            timeout: Whether the cause for the session closure is a timeout (this may interest transport plugins more)
            claimed: Whether the cause for the session closure is due to someone claiming the session

        Returns:
            no returns

        Raise:
            no exception

        Note:
            no IO, no block

        """
        pass

    def session_claimed(self, session_id):
        """ Method to notify the transport plugin that a session it owned was claimed by another transport

        Args:
            session_id: The session ID that was claimed (if the transport cares)

        Returns:
            no returns

        Raise:
            no exception
        Note:
            no IO, no block

        """
        pass


class Request(object):
    request_schema = Schema({
        "janus": StrRe(r"^\S+$"),
        "transaction": StrRe(r"^\S+$"),
        Optional("session_id"): IntVal(),
        Optional("handle_id"): IntVal(),
        Optional("apisecret"): StrVal(),
        DoNotCare(str): object  # for all other key we don't care
    })

    def __init__(self, transport_session, message):
        message = self.request_schema.validate(message)
        self.transport = transport_session
        self.message = message
        self.janus = message['janus']
        self.transaction = message['transaction']
        self.session_id = message.get('session_id', 0)
        self.handle_id = message.get('handle_id', 0)
        self.apisecret = message.get('apisecret', '')


class RequestHandler(object):

    def __init__(self, frontend_session_mgr=None, proxy_conf={}):
        self._frontend_session_mgr = frontend_session_mgr
        self._proxy_conf = proxy_conf
        self._api_secret = proxy_conf.get('general', {}).get('api_secret', '')

    def _get_session(self, request):
        if request.session_id == 0:
            raise JanusCloudError("Unhandled request '{}' at this path".format(request.janus),
                                  JANUS_ERROR_INVALID_REQUEST_PATH)
        session = self._frontend_session_mgr.find_session(request.session_id)
        if session is None:
            raise JanusCloudError('No such session {}'.format(request.session_id), JANUS_ERROR_SESSION_NOT_FOUND)
        session.activate()
        return session

    def _get_plugin_handle(self, request):
        session = self._get_session(request)
        if request.handle_id == 0:
            raise JanusCloudError("Unhandled request '{}' at this path".format(request.janus),
                                  JANUS_ERROR_INVALID_REQUEST_PATH)
        handle = session.get_handle(request.handle_id)
        if handle is None:
            raise JanusCloudError("No such handle {} in session {}".format(request.handle_id, request.session_id),
                                  JANUS_ERROR_HANDLE_NOT_FOUND)
        return handle

    def _handle_info(self, request):
        reply = create_janus_msg('server_info', 0, request.transaction)
        reply['name'] = 'Janus-Cloud Proxy'
        reply['author'] = 'OpenSight'
        reply['email'] = 'public@opensight.cn'
        reply['website'] = 'https://github.com/OpenSight/janus-cloud'
        reply['server_name'] = self._proxy_conf.get('general', {}).get('server_name', '')
        reply['session-timeout'] = self._proxy_conf.get('general', {}).get('session_timeout', 60)

        plugin_info_list = {}
        for plugin in get_plugin_list():
            plugin_info = {
                'version_string': plugin.get_version_string(),
                'description': plugin.get_description(),
                'author': plugin.get_author(),
                'name': plugin.get_name(),
                'version': plugin.get_version(),
            }
            plugin_info_list[plugin.get_package()] = plugin_info
        reply['plugins'] = plugin_info_list

        return reply

    def _handle_ping(self, request):
        return create_janus_msg('pong', 0, request.transaction)

    def _handle_create(self, request):
        create_params_schema = Schema({
                Optional('id'): IntVal(min=1, max=9007199254740992),
                AutoDel(str): object  # for all other key we don't care
        })

        params = create_params_schema.validate(request.message)
        session_id = params.get('id', 0)
        session = self._frontend_session_mgr.create_new_session(session_id, request.transport)
        return create_janus_msg('success', 0, request.transaction, data={'id': session.session_id})

    def _handle_destroy(self, request):
        if request.session_id == 0:
            raise JanusCloudError("Unhandled request '{}' at this path".format(request.janus),
                                  JANUS_ERROR_INVALID_REQUEST_PATH)
        self._frontend_session_mgr.destroy_session(request.session_id)
        return create_janus_msg('success', request.session_id, request.transaction)

    def _handle_keepalive(self, request):
        log.debug('Got a keep-alive on session {0}'.format(request.session_id))
        session = self._get_session(request)
        return create_janus_msg('ack', request.session_id, request.transaction)

    def _handle_claim(self, request):
        session = self._get_session(request)
        session.transport_claim(request.transport)
        return create_janus_msg('success', request.session_id, request.transaction)

    def _handle_attach(self, request):
        session = self._get_session(request)
        attach_params_schema = Schema({
                'plugin': StrVal(max_len=64),
                Optional('opaque_id'): StrVal(max_len=64),
                AutoDel(str): object  # for all other key we don't care
        })
        params = attach_params_schema.validate(request.message)
        handle = session.attach_handle(**params)
        return create_janus_msg('success', request.session_id, request.transaction, data={'id': handle.handle_id})

    def _handle_detach(self, request):
        self._get_plugin_handle(request) # check handle exist
        session = self._get_session(request)
        session.detach_handle(request.handle_id)
        return create_janus_msg('success', request.session_id, request.transaction)

    def _handle_hangup(self, request):
        handle = self._get_plugin_handle(request)
        handle.handle_hangup()
        return create_janus_msg('success', request.session_id, request.transaction)

    def _handle_message(self, request):

        message_params_schema = Schema({
                'body': dict,
                Optional('jsep'): dict,
                AutoDel(str): object  # for all other key we don't care
        })
        params = message_params_schema.validate(request.message)

        # dispatch to plugin handle
        handle = self._get_plugin_handle(request)
        result, content = handle.handle_message(request.transaction, **params)
        if result == JANUS_PLUGIN_OK:
            if content is None or not isinstance(content, dict):
                raise JanusCloudError(
                    "Plugin didn't provide any content for this synchronous response" if content is None
                    else "Plugin returned an invalid JSON response",
                    JANUS_ERROR_PLUGIN_MESSAGE)
            response = create_janus_msg('success', request.session_id, request.transaction, sender=request.handle_id)
            if handle.opaque_id:
                response['opaque_id'] = handle.opaque_id
            response['plugindata'] = {
                'plugin': handle.plugin_package_name,
                'data': content
            }
        elif result == JANUS_PLUGIN_OK_WAIT:
            response = create_janus_msg('ack', request.session_id, request.transaction)
            if content:
                response['hint'] = content
        else:
            raise JanusCloudError('Plugin returned a severe (unknown) error', JANUS_ERROR_PLUGIN_MESSAGE)

        return response

    def _handle_trickle(self, request):

        trickle_params_schema = Schema({
                Optional('candidate'): dict,
                Optional('candidates'): [dict],
                AutoDel(str): object  # for all other key we don't care
        })
        params = trickle_params_schema.validate(request.message)
        candidate = params.get('candidate')
        candidates = params.get('candidates')

        if candidate is None and candidates is None:
            raise JanusCloudError('Missing mandatory element (candidate|candidates)',
                                  JANUS_ERROR_MISSING_MANDATORY_ELEMENT)

        if candidate and candidates:
            raise JanusCloudError('Can\'t have both candidate and candidates',
                                  JANUS_ERROR_INVALID_JSON)

        # dispatch to plugin handle
        handle = self._get_plugin_handle(request)
        handle.handle_trickle(candidate=candidate, candidates=candidates)

        return create_janus_msg('ack', request.session_id, request.transaction)

    def incoming_request(self, request):
        """ handle the request from the transport module

        Args:
            request: the request to handle

        Returns:
            a dict to response to the initial client

        """

        try:
            log.debug('Request ({}) is incoming to handle'.format(request.message))
            handler = getattr(self, '_handle_' + request.janus)
            if handler is None or self._frontend_session_mgr is None:
                raise JanusCloudError('Unknown request \'{0}\''.format(request.janus), JANUS_ERROR_UNKNOWN_REQUEST)

            # check secret valid
            if self._api_secret and request.janus not in {'ping', 'info'}:
                if self._api_secret != request.apisecret:
                    raise JanusCloudError("Unauthorized request (wrong or missing secret/token)",
                                          JANUS_ERROR_UNAUTHORIZED)

            response = handler(request)
            log.debug('Response ({}) is to return'.format(response))
            return response
        except Exception as e:
            log.warn('Request ({}) processing failed'.format(request.message), exc_info=True)
            return error_to_janus_msg(request.session_id, request.transaction, e)

    def transport_gone(self, transport):
        """ notify transport session is closed by the transport module """
        if self._frontend_session_mgr:
            self._frontend_session_mgr.transport_gone(transport)


if __name__ == '__main__':
    handler = RequestHandler()
    income_message = {
        'janus': 'keepalive',
        'transaction': 'test_1234',
        'session_id': 12345,
        'handle_id': 6789
    }
    my_request = Request(message=income_message)

    reply = handler.incoming_request(my_request)

    print(reply)




