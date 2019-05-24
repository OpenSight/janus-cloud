# -*- coding: utf-8 -*-

import urllib.parse
import json
import random
import time
import logging
from januscloud.transport.ws import WSServer, WSClient
from januscloud.common.logger import test_config
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
        "transport": StrRe(r"^\S+$"),
        Optional("session_id"): IntVal(),
        Optional("handle_id"): IntVal(),
        DoNotCare(Use(STRING)): object  # for all other key we don't care
    })
    def __init__(self, transport_session=None, message={}):
        self.transport_session = transport_session
        self.message = message
        # TODO validdate message with basic schmema

        self.janus = message['janus']
        self.transaction = message['transaction']
        self.session_id = message.get('session_id', 0)
        self.handle_id = message.get('handle_id', 0)





class RequestHandler(object):

    def __init__(self, fontend_session_mgr=None):
        pass

    def incoming_request(self, request):
        """ handle the request from the transport module

        Args:
            request: the request to handle

        Returns:
            a dict to response to the initial client

        """




        return {}

    def transport_gone(self, transport_session=None):
        """ notify transport session is closed by the transport module """

        pass

