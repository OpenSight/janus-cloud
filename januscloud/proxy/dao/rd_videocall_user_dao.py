# -*- coding: utf-8 -*-
import logging
import uuid
import copy
import gevent
import time
from redis import RedisError
from januscloud.common.utils import to_redis_hash
from januscloud.proxy.plugin.videoroom import VideoRoom
from redis import RedisError
from januscloud.common.error import JANUS_ERROR_SESSION_CONFLICT, JanusCloudError, JANUS_ERROR_CONFLICT, \
    JANUS_ERROR_NOT_FOUND
from januscloud.proxy.plugin.videocall import VideoCallUser

log = logging.getLogger(__name__)

"""
januscloud:videocall_users:<username>       hash map of the video call user info, not expired 
januscloud:videocall_proxies:<proxy_uuid>       hash map of the janus-proxy videocall url, will expired 
"""

REDIS_UPDATE_INTERVAL = 10
REDIS_KEY_EXPIRED = 60
REDIS_SCAN_COUNT = 32
REDIS_SCAN_MAX_ITER = 1000


class RDVideoCallUserDao(object):
    def __init__(self, redis_client=None, api_base_url=''):
        self._users_by_name = {}
        self._proxy_uuid = str(uuid.uuid1())
        self._redis_client = redis_client
        self._rd_proxy = {
            'api_base_url': api_base_url
        }
        self._to_resync_rd_users = set()
        self._update_rd_proxy()           # test redis health too
        self._redis_refresh_greenlet = gevent.spawn(self._redis_refresh_routine)

    def get_by_username(self, username):
        videocall_user = None
        try:
            rd_user, rd_proxy = self._get_rd_user(username)
            mem_user = self._users_by_name.get(username)
            # try to sync the user info between redis db and local memory
            if not rd_user and not mem_user:
                videocall_user = None  # no this username
            elif not rd_user and mem_user:
                self._save_rd_user_to_redis(self._from_videocall_user(mem_user))  # sync local memory to redis db
                videocall_user = copy.copy(mem_user)
            elif rd_user and not mem_user:
                if rd_user.get('proxy_uuid', '') == self._proxy_uuid:
                    # rd_user invalid, remove
                    self._redis_client.delete(self._key_user(username))
                    videocall_user = None
                else:
                    videocall_user = self._from_rd_user(rd_user, rd_proxy)  # return rd_user
            else:
                # stored in both db and memory, check consistent
                if rd_user.get('proxy_uuid', '') == self._proxy_uuid:
                    # consistent
                    if username in self._to_resync_rd_users:
                        self._save_rd_user_to_redis(self._from_videocall_user(mem_user))
                    videocall_user = copy.copy(mem_user)  # return local copy
                else:
                    # not consistent, remove user from local memory
                    self._users_by_name.pop(username, None)
                    videocall_user = self._from_rd_user(rd_user, rd_proxy)  # return rd_user

            self._to_resync_rd_users.discard(username)     # this username has been sync again
        except RedisError as e:
            log.warning('Fail to get user {} info because of Redis client error: {}'.format(username, e))
            videocall_user = None

        return videocall_user

    def add(self, videocall_user):
        org_videocall_user = self._users_by_name.get(videocall_user.username)
        self._users_by_name[videocall_user.username] = copy.copy(videocall_user)
        try:
            self._save_rd_user_to_redis(self._from_videocall_user(videocall_user))
        except Exception as e:
            if org_videocall_user:
                self._users_by_name[videocall_user.username] = org_videocall_user
            else:
                self._users_by_name.pop(videocall_user.username, None)
            raise
        self._to_resync_rd_users.discard(videocall_user.username)  # this user has been sync again

    def update(self, videocall_user):     # never raise redis error
        org_videocall_user = self._users_by_name.get(videocall_user.username)
        if not org_videocall_user:
            self._users_by_name[videocall_user.username] = copy.copy(videocall_user)
        else:
            org_videocall_user.__dict__.update(videocall_user.__dict__)
        try:
            self._save_rd_user_to_redis(self._from_videocall_user(videocall_user))
        except RedisError as e:
            log.warning('Fail to edit user {} in redis: {}, re-sync in future'.format(videocall_user.username, e))
            self._to_resync_rd_users.add(videocall_user.username)
        else:
            self._to_resync_rd_users.discard(videocall_user.username)  # this user has been sync again

    def remove(self, videocall_user):
        mem_user = self._users_by_name.get(videocall_user.username)
        if mem_user and mem_user.handle is not videocall_user.handle:
            # videocall_user has been replaced, just return
            # print('mem_user handle {}, videocall_user handle {}, not match'.format(mem_user.handle, videocall_user.handle))
            return
        elif mem_user:
            self._users_by_name.pop(videocall_user.username, None)

        try:
            # print('del rd user {}'.format(videocall_user.username))
            self._del_rd_user(videocall_user.username)
        except RedisError as e:
            log.warning('Fail to del user {} from redis: {}, re-sync in future'.format(videocall_user.username, e))
            self._to_resync_rd_users.add(videocall_user.username)
        else:
            self._to_resync_rd_users.discard(videocall_user.username)  # this user has been sync again

    def get_username_list(self):
        username_list = []
        try:
            user_keys = self._redis_client.scan_iter(match='januscloud:videocall_users:*',
                                                     count=REDIS_SCAN_COUNT)
            for user_key in user_keys:
                username_list.append(user_key.split(':', 2)[2])
                if len(username_list) >= REDIS_SCAN_MAX_ITER:
                    break
        except RedisError as e:
            log.warning('Fail to get username list by Redis client error: {}'.format(e))
            username_list.clear()

        return username_list

    def _get_rd_user(self, username):
        rd_user = self._redis_client.hgetall(self._key_user(username))
        # print('_get_rd_user rd_user:{}'.format(rd_user))
        if not rd_user:
            return None, None
        rd_proxy = self._redis_client.hgetall(self._key_proxy(rd_user.get('proxy_uuid')))
        # print('_get_rd_proxy rd_user:{}'.format(rd_proxy))
        if not rd_proxy:
            return None, None
        return rd_user, rd_proxy

    def _save_rd_user_to_redis(self, rd_user):
        user_key = self._key_user(rd_user.get('username'))
        with self._redis_client.pipeline() as p:
            p.hmset(
                user_key,
                rd_user,
            )
            p.execute()

    def _del_rd_user(self, username):
        rd_user, rd_proxy = self._get_rd_user(username)
        # print('_del_rd_user {}, {}'.format(rd_user, rd_proxy))
        if rd_user and rd_user.get('proxy_uuid', '') == self._proxy_uuid:
            self._redis_client.delete(self._key_user(username))

    def _update_rd_proxy(self):
        proxy_key = self._key_proxy(self._proxy_uuid)
        with self._redis_client.pipeline() as p:
            p.hmset(
                proxy_key,
                self._rd_proxy,
            )
            p.expire(proxy_key, REDIS_KEY_EXPIRED)
            p.execute()

    def _resync_user(self, username):
        mem_user = self._users_by_name.get(username)
        rd_user, rd_proxy = self._get_rd_user(username)
        if not rd_user and not mem_user:
            pass
        elif not rd_user and mem_user:
            self._save_rd_user_to_redis(self._from_videocall_user(mem_user))
        elif rd_user and not mem_user:
            if rd_user.get('proxy_uuid', '') == self._proxy_uuid:
                self._redis_client.delete(self._key_user(username))
        else:
            if rd_user.get('proxy_uuid', '') == self._proxy_uuid:
                self._save_rd_user_to_redis(self._from_videocall_user(mem_user))

    def _redis_refresh_routine(self):
        while True:
            start_time = time.time()
            try:
                self._update_rd_proxy()
                # resync user info
                to_resync_rd_users = self._to_resync_rd_users.copy()
                for username in to_resync_rd_users:
                    self._resync_user(username)
                    self._to_resync_rd_users.discard(username)
            except RedisError as e:
                log.warning(
                    'Fail to refresh user db because of Redis client error: {}'.format(e))

            sleep_time = start_time + REDIS_UPDATE_INTERVAL - time.time()
            if sleep_time > REDIS_UPDATE_INTERVAL:
                sleep_time = REDIS_UPDATE_INTERVAL
            elif sleep_time < 0:
                sleep_time = 0
            gevent.sleep(sleep_time)

    @staticmethod
    def _from_rd_user(rd_user, rd_proxy):
        api_url = str(rd_proxy.get('api_base_url', '')) + '/' + str(rd_user['username'])
        user = VideoCallUser(username=str(rd_user['username']),
                             incall=bool(rd_user.get('incall', False)),
                             peer_name=str(rd_user.get('peer_name', '')),
                             api_url=api_url)
        if 'ctime' in rd_user:
            user.ctime = float(rd_user['ctime'])
        if 'utime' in rd_user:
            user.utime = float(rd_user['utime'])
        return user

    def _from_videocall_user(self, videocall_user):
        rd_user = {
            'username': videocall_user.username,
            'incall': '1' if videocall_user.incall else '',
            'peer_name': videocall_user.peer_name,
            'proxy_uuid': self._proxy_uuid,
            'ctime': videocall_user.ctime,
            'utime': videocall_user.utime,
        }
        return rd_user

    @staticmethod
    def _key_user(username):
        return 'januscloud:videocall_users:{0}'.format(username)

    @staticmethod
    def _key_proxy(proxy_uuid):
        return 'januscloud:videocall_proxies:{0}'.format(proxy_uuid)


def test_redis():
    # test redis client
    import redis
    from januscloud.proxy.plugin.videoroom import VideoRoom

    api_base_url = 'http://127.0.0.1/test'
    connection_pool = redis.BlockingConnectionPool.from_url(
        url='redis://127.0.0.1:6379',
        decode_responses=True,
        health_check_interval=30,
        timeout=10)
    redis_client = redis.Redis(connection_pool=connection_pool)
    videocall_user_dao = RDVideoCallUserDao(redis_client, api_base_url=api_base_url)

    username = 'test'
    handle = 'test_handle'
    assert videocall_user_dao.get_by_username(username) is None
    videocall_user = VideoCallUser(username='test', handle=handle, api_url=api_base_url + '/' + username)
    videocall_user_dao.add(videocall_user)
    return_user = videocall_user_dao.get_by_username(username)
    assert return_user.handle is handle
    assert not return_user.incall
    assert return_user.peer_name == ''

    assert username in videocall_user_dao.get_username_list()

    videocall_user.peer_name = 'peer'
    videocall_user.incall = True
    videocall_user.utime = time.time()
    videocall_user_dao.update(videocall_user)
    return_user = videocall_user_dao.get_by_username(username)
    assert return_user.incall
    assert return_user.peer_name == 'peer'
    assert return_user.handle is handle

    videocall_user_dao.remove(videocall_user)
    assert videocall_user_dao.get_by_username(username) is None

    assert username not in videocall_user_dao.get_username_list()

    print('redis db test successful')


if __name__ == '__main__':
    test_redis()

