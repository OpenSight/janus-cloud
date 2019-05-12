# -*- coding: utf-8 -*-


class JanusCloudError(Exception):
    EC = 1
    MSG = 'General Janus Cloud Error'
    HTTP_STATUS_CODE = 505

    def __init__(self, msg=None, http_status_code=None, **msg_kwargs):
        if not msg and msg_kwargs:
            msg = self.MSG.format(**msg_kwargs)
        elif not msg:
            msg = self.MSG
        elif msg_kwargs:
            msg = msg.format(**msg_kwargs)
        super(JanusCloudError, self).__init__(msg)
        self.http_status_code = http_status_code or self.HTTP_STATUS_CODE
