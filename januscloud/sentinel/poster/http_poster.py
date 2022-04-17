# -*- coding: utf-8 -*-

import logging
import time
import importlib
import gevent
import requests
from gevent.event import Event
from januscloud.common.error import JanusCloudError
from januscloud.sentinel.poster_manager import BasicPoster, register_poster_type


log = logging.getLogger(__name__)


class HttpPoster(BasicPoster):

    MAX_POST_INTERVAL = 60

    def __init__(self, janus_server, post_type, name='', post_urls=[], expire=0, http_timeout=10):
        super().__init__(janus_server, post_type, name)
        self.post_urls = post_urls
        self.expire = expire
        self._http_timeout = http_timeout
        self._post_greenlet = gevent.spawn(self._post_routine)
        self._post_interval = HttpPoster.MAX_POST_INTERVAL
        if expire and (expire / 3) < self._post_interval:
            self._post_interval = self.expire / 3
        self._state_changed_event = Event()
        self._cur_index = 0
        self._post_session = requests.session()

    def _post_routine(self):
        while True:
            self._state_changed_event.wait(timeout=self._post_interval)
            self._state_changed_event.clear()
            self.connected = self.post()

    def on_status_changed(self, new_state):
        self._state_changed_event.set()

    def on_stat_updated(self):
        self._state_changed_event.set()

    def post(self):
        data = {
            'name': self._janus_server.server_name,
            'url': self._janus_server.public_url,
            'status': self._janus_server.status,
            'start_time': self._janus_server.start_time,
            'expire': self.expire,
            'isp': self._janus_server.isp,
            'location': self._janus_server.location
        }

        if self._janus_server.session_num >= 0:
            data['session_num'] = int(self._janus_server.session_num)
        if self._janus_server.handle_num >= 0:
            data['handle_num'] = int(self._janus_server.handle_num)

        for i in range(len(self.post_urls)):
            url = self.post_urls[self._cur_index]
            self._cur_index += 1
            if self._cur_index >= len(self.post_urls):
                self._cur_index = 0
            try:
                r = self._post_session.post(url,  data=data, timeout=self._http_timeout)
                if r.status_code == requests.codes.ok:
                    return True
                else:
                    raise JanusCloudError('HTTP Return error (Status code: {}, text: {})'.format(
                        r.status_code, r.text), r.status_code)

            except Exception as e:
                log.warning('Http post failed for url {}: {}'.format(url, e))
                pass

        return False


register_poster_type('http', HttpPoster)
