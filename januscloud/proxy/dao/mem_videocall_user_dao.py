# -*- coding: utf-8 -*-
import copy

from januscloud.common.error import JANUS_ERROR_SESSION_CONFLICT, JanusCloudError, JANUS_ERROR_CONFLICT, \
    JANUS_ERROR_NOT_FOUND

class MemVideoCallUserDao(object):
    def __init__(self):
        self._users_by_name = {}

    def get_by_username(self, username):
        video_call_user = self._users_by_name.get(username)
        if video_call_user:
            return copy.copy(video_call_user)
        else:
            return None

    def remove(self, videocall_user):
        mem_user = self._users_by_name.get(videocall_user.username)
        if mem_user and mem_user.handle is not videocall_user.handle:
            # videocall_user has been replaced, just return
            return
        self._users_by_name.pop(videocall_user.username, None)

    def add(self, videocall_user):
        self._users_by_name[videocall_user.username] = copy.copy(videocall_user)

    def update(self, videocall_user):
        org_videocall_user = self._users_by_name.get(videocall_user.username)
        if not org_videocall_user:
            self._users_by_name[videocall_user.username] = copy.copy(videocall_user)
        else:
            org_videocall_user.__dict__.update(videocall_user.__dict__)

    def get_username_list(self):
        return [video_call_user.username for video_call_user in self._users_by_name.values()]









