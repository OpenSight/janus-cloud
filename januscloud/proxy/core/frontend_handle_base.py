# -*- coding: utf-8 -*-

import logging
from januscloud.common.utils import error_to_janus_msg, create_janus_msg
from januscloud.common.error import JanusCloudError, JANUS_ERROR_UNKNOWN_REQUEST, JANUS_ERROR_PLUGIN_MESSAGE, \
    JANUS_ERROR_MISSING_REQUEST
from januscloud.common.schema import Schema, Optional, DoNotCare, \
    Use, IntVal, Default, SchemaError, BoolVal, StrRe, ListVal, Or, STRING, \
    FloatVal, AutoDel

log = logging.getLogger(__name__)

JANUS_PLUGIN_OK = 0
JANUS_PLUGIN_OK_WAIT = 1


class FrontendHandleBase(object):
    """ This base class for frontend handle """

    def __init__(self, handle_id, session, plugin_package_name, opaque_id=None):
        self.handle_id = handle_id
        self.opaque_id = opaque_id
        self._session = session
        self._has_destroy = False

        self.plugin_package_name = plugin_package_name

    def detach(self):

        if self._has_destroy:
            return
        self._has_destroy = True

    def has_destroy(self):
        return self._has_destroy

    def handle_hangup(self):
        raise JanusCloudError('hangup not support\'hangup\'', JANUS_ERROR_MISSING_REQUEST)

    def handle_message(self, transaction, body, jsep=None):
        raise JanusCloudError('message not support\'message\'', JANUS_ERROR_PLUGIN_MESSAGE)

    def handle_trickle(self, candidate=None, candidates=None):
        raise JanusCloudError('hangup not support\'trickle\'', JANUS_ERROR_MISSING_REQUEST)

    def _push_event(self, message, jsep=None, transaction=None):
        if self._has_destroy:
            return
        event = create_janus_msg('event', self._session.session_id, transaction)
        event['sender'] = self.handle_id
        if self.opaque_id:
            event['opaque_id'] = self.opaque_id
        event['plugindata'] = {
            'plugin': self.plugin_package_name,
            'data': message
        }
        if jsep:
            event['jsep'] = jsep

        self._session.notify_event(event)






if __name__ == '__main__':
    pass




