# -*- coding: utf-8 -*-

import logging
from januscloud.common.utils import error_to_janus_msg, create_janus_msg
from januscloud.common.error import JanusCloudError, JANUS_ERROR_UNKNOWN_REQUEST, JANUS_ERROR_PLUGIN_MESSAGE, \
    JANUS_ERROR_MISSING_REQUEST
from januscloud.common.schema import Schema, Optional, DoNotCare, \
    Use, IntVal, Default, SchemaError, BoolVal, StrRe, ListVal, Or, STRING, \
    FloatVal, AutoDel

log = logging.getLogger(__name__)



class FrontendHandleBase(object):
    """ This base class for frontend handle """

    def __init__(self, handle_id, session):
        self.handle_id = handle_id
        self._session = session
        self._has_destroy = False

    def detach(self):

        if self._has_destroy:
            return
        self._has_destroy = True

        self._session.on_handle_detach(self.handle_id)

    def has_destroy(self):
        return self._has_destroy

    def handle_hangup(self, request):
        raise JanusCloudError('hangup not support\'{0}\''.format(request.janus), JANUS_ERROR_MISSING_REQUEST)

    def handle_message(self, request):
        raise JanusCloudError('message not support\'{0}\''.format(request.janus), JANUS_ERROR_PLUGIN_MESSAGE)

    def handle_trickle(self, request):
        raise JanusCloudError('hangup not support\'{0}\''.format(request.janus), JANUS_ERROR_MISSING_REQUEST)



if __name__ == '__main__':
    pass




