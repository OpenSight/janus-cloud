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

    def del_by_username(self, username):
        self._users_by_name.pop(username, None)

    def add(self, video_call_user):
        if video_call_user.username in self._users_by_name:
            raise JanusCloudError('videocall user {} already in repo'.format(video_call_user.name), JANUS_ERROR_CONFLICT)
        self._users_by_name[video_call_user.username] = copy.copy(video_call_user)


    def update(self, videocall_user):
        org_videocall_user = self._users_by_name.get(videocall_user.username)
        if not org_videocall_user:
            raise JanusCloudError('server {} NOT found'.format(videocall_user.username), JANUS_ERROR_NOT_FOUND)
        org_videocall_user.__dict__.update(videocall_user.__dict__)

    def get_username_list(self):
        return [video_call_user.username for video_call_user in self._users_by_name.values()]









