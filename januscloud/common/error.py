# -*- coding: utf-8 -*-

# Success (no error) 
JANUS_OK = 0

# Unauthorized (can only happen when using apisecret/auth token)
JANUS_ERROR_UNAUTHORIZED = 403
# Unauthorized access to a plugin (can only happen when using auth token) 
JANUS_ERROR_UNAUTHORIZED_PLUGIN = 405
# Unknown/undocumented error 
JANUS_ERROR_UNKNOWN = 490
# Transport related error
JANUS_ERROR_TRANSPORT_SPECIFIC = 450
# The request is missing in the message
JANUS_ERROR_MISSING_REQUEST = 452
# The Janus core does not suppurt this request 
JANUS_ERROR_UNKNOWN_REQUEST	= 453
# The payload is not a valid JSON message 
JANUS_ERROR_INVALID_JSON = 454
# The object is not a valid JSON object as expected 
JANUS_ERROR_INVALID_JSON_OBJECT	= 455
# A mandatory element is missing in the message 
JANUS_ERROR_MISSING_MANDATORY_ELEMENT	 = 456
# The request cannot be handled for this webserver path  
JANUS_ERROR_INVALID_REQUEST_PATH	=	457
# The session the request refers to doesn't exist 
JANUS_ERROR_SESSION_NOT_FOUND		=	458
# The handle the request refers to doesn't exist 
JANUS_ERROR_HANDLE_NOT_FOUND		=	459
# The plugin the request wants to talk to doesn't exist 
JANUS_ERROR_PLUGIN_NOT_FOUND		=	460
# An error occurring when trying to attach to a plugin and create a handle  
JANUS_ERROR_PLUGIN_ATTACH			=	461
# An error occurring when trying to send a message/request to the plugin 
JANUS_ERROR_PLUGIN_MESSAGE			=	462
# An error occurring when trying to detach from a plugin and destroy the related handle  
JANUS_ERROR_PLUGIN_DETACH			=	463
# The Janus core doesn't support this SDP type 
JANUS_ERROR_JSEP_UNKNOWN_TYPE		=	464
# The Session Description provided by the peer is invalid 
JANUS_ERROR_JSEP_INVALID_SDP		=	465
# The stream a trickle candidate for does not exist or is invalid 
JANUS_ERROR_TRICKE_INVALID_STREAM	=	466
# A JSON element is of the wrong type (e.g., an integer instead of a string) 
JANUS_ERROR_INVALID_ELEMENT_TYPE	=	467
# The ID provided to create a new session is already in use 
JANUS_ERROR_SESSION_CONFLICT		=	468
# We got an ANSWER to an OFFER we never made 
JANUS_ERROR_UNEXPECTED_ANSWER		=	469
# The auth token the request refers to doesn't exist 
JANUS_ERROR_TOKEN_NOT_FOUND			=	470
# The current request cannot be handled because of not compatible WebRTC state 
JANUS_ERROR_WEBRTC_STATE			=	471
# The server is currently configured not to accept new sessions 
JANUS_ERROR_NOT_ACCEPTING_SESSIONS	=	472


JANUS_ERROR_NOT_FOUND = 404

JANUS_ERROR_CONFLICT = 409

JANUS_ERROR_GATEWAY_TIMEOUT = 504
JANUS_ERROR_INTERNAL_ERROR = 500
JANUS_ERROR_BAD_GATEWAY = 502
JANUS_ERROR_SERVICE_UNAVAILABLE = 503
JANUS_ERROR_NOT_IMPLEMENTED= 501


class JanusCloudError(Exception):
    MSG = 'General Janus Cloud Error'

    def __init__(self, msg=None, code=None, **msg_kwargs):
        if not msg and msg_kwargs:
            msg = self.MSG.format(**msg_kwargs)
        elif not msg:
            msg = self.MSG
        elif msg_kwargs:
            msg = msg.format(**msg_kwargs)
        super(JanusCloudError, self).__init__(msg)
        self.code = code or JANUS_ERROR_UNKNOWN


