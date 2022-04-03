# -*- coding: utf-8 -*-
import logging
from januscloud.common.utils import to_redis_hash
from januscloud.proxy.plugin.videoroom import VideoRoom
from redis import RedisError
log = logging.getLogger(__name__)

"""
januscloud:video_rooms:<room_id>       hash map of the video room info, not expired 

"""


class RDRoomDao(object):

    def __init__(self, redis_client=None):
        self._redis_client = redis_client
        self._redis_client.client_getname()     # test the connection if available or not

    def get_by_room_id(self, room_id):
        try:
            rd_room = self._redis_client.hgetall(self._key_room(room_id))
            if rd_room:
                return self._from_rd_room(rd_room)
            else:
                return None
        except RedisError as e:
            log.warning('Fail to get video room {} configuration because of Redis client error: {}'.format(room_id, e))
            return None

    def del_by_room_id(self, room_id):
        self._redis_client.delete(self._key_room(room_id))

    def del_by_list(self, room_list):
        room_key_list = []
        for room in room_list:
            room_key_list.append(self._key_room(room.room_id))
        if room_key_list:
            self._redis_client.delete(*room_key_list)

    def add(self, room):
        room_key = self._key_room(room.room_id)
        with self._redis_client.pipeline() as p:
            p.hmset(
                room_key,
                to_redis_hash(room),
            )
            p.execute()

    def update(self, room):
        room_key = self._key_room(room.room_id)
        with self._redis_client.pipeline() as p:
            p.hmset(
                room_key,
                to_redis_hash(room),
            )
            p.execute()

    def get_list(self):
        room_list = []
        try:
            room_key_list = self._redis_client.keys(pattern='januscloud:video_rooms:*')
            start = 0
            step = 32
            total = len(room_key_list)
            while True:
                with self._redis_client.pipeline() as p:
                    for room_key in room_key_list[start:start+step]:
                        p.hgetall(room_key)
                    result = p.execute()
                    for rd_room in result:
                        if rd_room:
                            room_list.append(self._from_rd_room(rd_room))
                start += step
                if start >= total:
                    break
        except RedisError as e:
            log.warning('Fail to get backend server list because of Redis client error: {}'.format(e))

        return room_list

    @staticmethod
    def _key_room(room_id):
        return 'januscloud:video_rooms:{0}'.format(room_id)

    @staticmethod
    def _from_rd_room(rd_room):
        room = VideoRoom(room_id=int(rd_room['room_id']),
                         description=str(rd_room.get('description', '')),
                         secret=str(rd_room.get('secret', '')),
                         pin=str(rd_room.get('pin', '')),
                         is_private=bool(rd_room.get('is_private', '')),
                         require_pvtid=bool(rd_room.get('require_pvtid', False)),
                         publishers=int(rd_room.get('publishers', 3)),
                         bitrate=int(rd_room.get('bitrate', 0)),
                         bitrate_cap=bool(rd_room.get('bitrate_cap', False)),
                         fir_freq=int(rd_room.get('fir_freq', 0)),
                         audiocodec=rd_room.get('audiocodec', 'opus').split(','),
                         videocodec=rd_room.get('videocodec', 'vp8').split(','),
                         opus_fec=bool(rd_room.get('opus_fec', False)),
                         opus_dtx=bool(rd_room.get('opus_dtx', False)),
                         video_svc=bool(rd_room.get('video_svc', False)),
                         audiolevel_ext=bool(rd_room.get('audiolevel_ext', False)),
                         audiolevel_event=bool(rd_room.get('audiolevel_event', False)),
                         audio_active_packets=int(rd_room.get('audio_active_packets', 100)),
                         audio_level_average=int(rd_room.get('audio_level_average', 25)),
                         videoorient_ext=bool(rd_room.get('videoorient_ext', True)),
                         playoutdelay_ext=bool(rd_room.get('playoutdelay_ext', True)),
                         transport_wide_cc_ext=bool(rd_room.get('transport_wide_cc_ext', False)),
                         record=bool(rd_room.get('record', False)),
                         rec_dir=str(rd_room.get('rec_dir', '')),
                         notify_joining=bool(rd_room.get('notify_joining', False)),
                         lock_record=bool(rd_room.get('lock_record', False)),
                         require_e2ee=bool(rd_room.get('require_e2ee', False)),
                         vp9_profile=str(rd_room.get('vp9_profile', '')),
                         h264_profile=str(rd_room.get('h264_profile', '')))

        if 'ctime' in rd_room:
            room.ctime = float(rd_room['ctime'])
        if 'utime' in rd_room:
            room.utime = float(rd_room['utime'])

        return room


def test_redis():
    # test redis client
    import redis
    from januscloud.proxy.plugin.videoroom import VideoRoom

    connection_pool = redis.BlockingConnectionPool.from_url(
        url='redis://127.0.0.1:6379',
        decode_responses=True,
        health_check_interval=30,
        timeout=10)
    redis_client = redis.Redis(connection_pool=connection_pool)
    room_dao = RDRoomDao(redis_client)

    room_id = 1234
    assert room_dao.get_by_room_id(room_id) is None
    room = VideoRoom(room_id=room_id, description='1234')
    room_dao.add(room)
    return_room = room_dao.get_by_room_id(room_id)
    assert return_room.room_id == 1234
    assert return_room.description == '1234'

    room_list = room_dao.get_list()
    assert len(room_list) > 0
    for room in room_list:
        if room.room_id == room_id:
            break
    else:
        raise AssertionError('room list test fail')

    room.description = 'test'
    room_dao.update(room)
    return_room = room_dao.get_by_room_id(room_id)
    assert return_room.description == 'test'

    room_dao.del_by_room_id(room_id)
    assert room_dao.get_by_room_id(room_id) is None

    rooms = []
    rooms.append(VideoRoom(room_id=111, description='list_111'))
    room_dao.add(rooms[0])
    rooms.append(VideoRoom(room_id=222, description='list_222'))
    room_dao.add(rooms[1])
    room_dao.del_by_list(rooms)
    assert room_dao.get_by_room_id(111) is None



    print('redis db test successful')


if __name__ == '__main__':
    test_redis()
