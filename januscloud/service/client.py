# -*- coding: utf-8 -*-


class Client(object):
    # remote client. browser or other WEBRTC client

    def __init__(self, transport):
        self._transport = transport

    def _send_msg(self, msg):
        self._transport.send(msg)

    def on_msg(self, msg):
        # message handler
        pass

    def _close_transport(self):
        if self._transport:
            self._transport.force_shutdown()

    def on_transport_closed(self):
        pass
