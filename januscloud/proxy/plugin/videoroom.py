# -*- coding: utf-8 -*-
import base64
import copy

import logging
import re
from urllib.parse import urlparse
from januscloud.common.utils import error_to_janus_msg, create_janus_msg, random_uint64, random_uint32, \
    get_monotonic_time
from januscloud.common.error import JanusCloudError, JANUS_ERROR_UNKNOWN_REQUEST, JANUS_ERROR_INVALID_REQUEST_PATH, \
    JANUS_ERROR_BAD_GATEWAY, JANUS_ERROR_CONFLICT, JANUS_ERROR_NOT_IMPLEMENTED, JANUS_ERROR_INTERNAL_ERROR
from januscloud.common.schema import Schema, Optional, DoNotCare, \
    Use, IntVal, Default, SchemaError, BoolVal, StrRe, ListVal, Or, STRING, \
    FloatVal, AutoDel, StrVal, EnumVal
from januscloud.core import backend_handle
from januscloud.core.backend_session import get_backend_session
from januscloud.core.plugin_base import PluginBase
from januscloud.core.frontend_handle_base import FrontendHandleBase, JANUS_PLUGIN_OK_WAIT, JANUS_PLUGIN_OK
import os.path
from januscloud.common.confparser import parse as parse_config
import time
import gevent
from januscloud.proxy.rest.common import post_view, get_params_from_request, get_view, delete_view, put_view
from pyramid.response import Response
import sys
import traceback
import weakref

log = logging.getLogger(__name__)

BACKEND_SESSION_AUTO_DESTROY_TIME = 10    # auto destroy the backend session after 10s if no handle for it

ROOM_CLEANUP_CHECK_INTERVAL = 10  # CHECK EMPTY ROOM INTERVAL

JANUS_VIDEOROOM_ERROR_UNKNOWN_ERROR = 499
JANUS_VIDEOROOM_ERROR_NO_MESSAGE = 421
JANUS_VIDEOROOM_ERROR_INVALID_JSON = 422
JANUS_VIDEOROOM_ERROR_INVALID_REQUEST = 423
JANUS_VIDEOROOM_ERROR_JOIN_FIRST = 424
JANUS_VIDEOROOM_ERROR_ALREADY_JOINED = 425
JANUS_VIDEOROOM_ERROR_NO_SUCH_ROOM = 426
JANUS_VIDEOROOM_ERROR_ROOM_EXISTS = 427
JANUS_VIDEOROOM_ERROR_NO_SUCH_FEED = 428
JANUS_VIDEOROOM_ERROR_MISSING_ELEMENT = 429
JANUS_VIDEOROOM_ERROR_INVALID_ELEMENT = 430
JANUS_VIDEOROOM_ERROR_INVALID_SDP_TYPE = 431
JANUS_VIDEOROOM_ERROR_PUBLISHERS_FULL = 432
JANUS_VIDEOROOM_ERROR_UNAUTHORIZED = 433
JANUS_VIDEOROOM_ERROR_ALREADY_PUBLISHED = 434
JANUS_VIDEOROOM_ERROR_NOT_PUBLISHED = 435
JANUS_VIDEOROOM_ERROR_ID_EXISTS = 436
JANUS_VIDEOROOM_ERROR_INVALID_SDP = 437
JANUS_VIDEOROOM_ERROR_ALREADY_DESTROYED = 470
JANUS_VIDEOROOM_ERROR_ALREADY_BACKEND = 471

JANUS_VIDEOROOM_API_SYNC_VERSION = 'v1.0.3(2022-06-20)'

JANUS_VIDEOROOM_VERSION = 9
JANUS_VIDEOROOM_VERSION_STRING = '0.0.9'
JANUS_VIDEOROOM_DESCRIPTION = 'This is a plugin implementing a videoconferencing SFU for Janus-cloud, ' \
                              'that is an audio/video router whose API is kept sync with videoroom of Janus-gateway ' \
                              'until ' + JANUS_VIDEOROOM_API_SYNC_VERSION
JANUS_VIDEOROOM_NAME = 'JANUS VideoRoom plugin'
JANUS_VIDEOROOM_AUTHOR = 'opensight.cn'
JANUS_VIDEOROOM_PACKAGE = 'janus.plugin.videoroom'


JANUS_VIDEOROOM_API_BASE_PATH = '/plugins/videoroom'

JANUS_RTP_EXTMAP_AUDIO_LEVEL = "urn:ietf:params:rtp-hdrext:ssrc-audio-level"


room_base_schema = Schema({
    Optional('secret'): Default(StrVal(max_len=256), default=''),
    Optional('room'): Default(IntVal(min=0), default=0),
    Optional('permanent'): Default(BoolVal(), default=False),
    AutoDel(str): object  # for all other key we must delete
})

room_params_schema = Schema({
    Optional('description'): StrVal(max_len=256),
    Optional('secret'): StrVal(max_len=256),
    Optional('pin'): StrVal(max_len=256),
    Optional('is_private'): BoolVal(),
    Optional('allowed'): ListVal(StrVal(max_len=256)),
    Optional('require_pvtid'): BoolVal(),
    Optional('publishers'): IntVal(min=1),
    Optional('bitrate'): IntVal(min=0),
    Optional('fir_freq'): IntVal(min=0),
    Optional('audiocodec'): ListVal(EnumVal(
        ['opus', 'multiopus', 'g722', 'pcmu', 'pcma', 'isac32', 'isac16']
    )),
    Optional('videocodec'): ListVal(EnumVal(
        ['vp8', 'vp9', 'h264', 'av1', 'h265']
    )),
    Optional('vp9_profile'): StrVal(max_len=256),
    Optional('h264_profile'): StrVal(max_len=256),
    Optional('opus_fec'): BoolVal(),
    Optional('opus_dtx'): BoolVal(),   
    Optional('video_svc'): BoolVal(),
    Optional('audiolevel_ext'): BoolVal(),
    Optional('audiolevel_event'): BoolVal(),
    Optional('audio_active_packets'): IntVal(min=1),
    Optional('audio_level_average'): IntVal(min=1, max=127),
    Optional('videoorient_ext'): BoolVal(),
    Optional('playoutdelay_ext'): BoolVal(),
    Optional('transport_wide_cc_ext'): BoolVal(),
    Optional('record'): BoolVal(),
    Optional('rec_dir'): StrVal(max_len=1024),
    Optional('notify_joining'): BoolVal(),
    Optional('lock_record'): BoolVal(),
    Optional('require_e2ee'): BoolVal(),
    AutoDel(str): object  # for all other key we must delete
})


room_edit_schema = Schema({
    Optional('new_description'): StrRe('^\w{1,128}$'),
    Optional('new_secret'): StrVal(max_len=256),
    Optional('new_pin'): StrVal(max_len=256),
    Optional('new_is_private'): BoolVal(),
    Optional('new_require_pvtid'): BoolVal(),
    Optional('new_publishers'): IntVal(min=1),
    Optional('new_bitrate'): IntVal(min=0),
    Optional('new_fir_freq'): IntVal(min=0),
    Optional('new_lock_record'): BoolVal(),
    Optional('new_rec_dir'): StrVal(max_len=1024),
    AutoDel(str): object  # for all other key we must delete
})

room_list_schema = Schema({
    Optional('admin_key'): StrVal(),
    Optional('offset'): IntVal(min=0),
    Optional('limit'): IntVal(min=0),
    AutoDel(str): object  # for all other key we must delete
})

allowed_schema = Schema({
    'action': EnumVal(['enable', 'disable', 'add', 'remove']),
    Optional('allowed'): ListVal(StrVal(max_len=256)),
    AutoDel(str): object  # for all other key we must delete
})

kick_schema = Schema({
    'id': IntVal(min=1),
    AutoDel(str): object  # for all other key we must delete
})

moderate_schema = Schema({
    Optional('mid'): StrVal(max_len=32),
    Optional('mute'): BoolVal(),
    AutoDel(str): object  # for all other key we must delete
})
rtp_forward_stream_schema = Schema({
    Optional('mid'): StrVal(max_len=32),
    Optional('host'): StrVal(max_len=256),
    Optional('host_family'): EnumVal(['ipv4', 'ipv6']),
    Optional('port'): IntVal(min=0, max=65535),
    Optional('ssrc'): IntVal(min=0),
    Optional('pt'): IntVal(min=0),
    Optional('rtcp_port'): IntVal(min=0, max=65535),
    Optional('simulcast'): BoolVal(),
    Optional('port_2'): IntVal(min=0, max=65535),
    Optional('ssrc_2'): IntVal(min=0),
    Optional('pt_2'): IntVal(min=0),
    Optional('port_3'): IntVal(min=0, max=65535),
    Optional('ssrc_3'): IntVal(min=0),
    Optional('pt_3'): IntVal(min=0),
    AutoDel(str): object  # for all other key we must delete 
})
rtp_forward_schema = Schema({
    'host': StrVal(max_len=256),
    Optional('host_family'): EnumVal(['ipv4', 'ipv6']),
    Optional('streams'): ListVal(rtp_forward_stream_schema),
    Optional('audio_port'): IntVal(min=0, max=65535),
    Optional('audio_ssrc'): IntVal(min=0),
    Optional('audio_pt'): IntVal(min=0),
    Optional('audio_rtcp_port'): IntVal(min=0, max=65535),
    Optional('video_port'): IntVal(min=0, max=65535),
    Optional('video_ssrc'): IntVal(min=0),
    Optional('video_pt'): IntVal(min=0),
    Optional('video_rtcp_port'): IntVal(min=0, max=65535),
    Optional('simulcast'): BoolVal(),
    Optional('video_port_2'): IntVal(min=0, max=65535),
    Optional('video_ssrc_2'): IntVal(min=0),
    Optional('video_pt_2'): IntVal(min=0),
    Optional('video_port_3'): IntVal(min=0, max=65535),
    Optional('video_ssrc_3'): IntVal(min=0),
    Optional('video_pt_3'): IntVal(min=0),
    Optional('data_port'): IntVal(min=0, max=65535),
    Optional('srtp_suite'): IntVal(min=0),
    Optional('srtp_crypto'): StrVal(),
    AutoDel(str): object  # for all other key we must delete
})
stop_rtp_forward_schema = Schema({
    'publisher_id': IntVal(min=1),
    'stream_id': IntVal(min=0),
    AutoDel(str): object  # for all other key we must delete
})
record_schema = Schema({
    'record':  BoolVal(),
    AutoDel(str): object  # for all other key we must delete
})

join_base_schema = Schema({
    'room': IntVal(min=1),
    'ptype': StrVal(max_len=256),
    Optional('pin'): Default(StrVal(max_len=256), default=''),
    AutoDel(str): object  # for all other key we must delete
})

publisher_join_schema = Schema({
    Optional('id'): IntVal(min=1),
    Optional('display'): StrVal(max_len=256),
    Optional('token'): StrVal(max_len=256),
    AutoDel(str): object  # for all other key we must delete
})

publisher_description_schema = Schema({
    'mid': StrVal(max_len=32),
    'description': StrVal(max_len=256),
    AutoDel(str): object  # for all other key we must delete
})

publisher_configure_stream_schema = Schema({
    'mid': StrVal(max_len=32),
    Optional('send'): BoolVal(),
    # For the playout-delay RTP extension, if negotiated
    Optional('min_delay'): IntVal(),    
    Optional('max_delay'): IntVal(), 
    AutoDel(str): object  # for all other key we must delete
})

publisher_configure_schema = Schema({
    # Deprecated, use mid+send instead
    Optional('audio'): BoolVal(),
    Optional('video'): BoolVal(),
    Optional('data'): BoolVal(),
    # Only needed when configuring, to make a stream active/inactive
    Optional('mid'): StrVal(max_len=32),
    Optional('send'): BoolVal(),

    Optional('audiocodec'): StrVal(max_len=256),
    Optional('videocodec'): StrVal(max_len=256),
    Optional('bitrate'): IntVal(min=0),
    Optional('keyframe'): BoolVal(),
    Optional('record'): BoolVal(),
    Optional('filename'): StrVal(max_len=256),
    Optional('secret'): StrVal(max_len=256),
    Optional('display'): StrVal(max_len=256),

    Optional('update'): BoolVal(),

    Optional('audio_active_packets'): IntVal(min=1),
    Optional('audio_level_average'): IntVal(min=1, max=127),
    # For the playout-delay RTP extension, if negotiated
    Optional('min_delay'): IntVal(),    
    Optional('max_delay'): IntVal(), 
    Optional('streams'): ListVal(publisher_configure_stream_schema),  
    Optional('descriptions'): ListVal(publisher_description_schema),     
    AutoDel(str): object  # for all other key we must delete
})

publisher_publish_schema = Schema({
    Optional('audiocodec'): StrVal(max_len=256),
    Optional('videocodec'): StrVal(max_len=256),
    Optional('bitrate'): IntVal(min=0),
    Optional('record'): BoolVal(),
    Optional('filename'): StrVal(max_len=256),
    Optional('secret'): StrVal(max_len=256),
    Optional('display'): StrVal(max_len=256),
    Optional('audio_active_packets'): IntVal(min=1),
    Optional('audio_level_average'): IntVal(min=1, max=127),
    Optional('descriptions'): ListVal(publisher_description_schema),   
    AutoDel(str): object  # for all other key we must delete
})


subscriber_stream_schema = Schema({
    'feed': IntVal(min=1),
    Optional('mid'): StrVal(max_len=32),
    Optional('substream'): IntVal(min=0, max=2),
    Optional('temporal'): IntVal(min=0, max=2),
    Optional('fallback'): IntVal(min=0),
    Optional('spatial_layer'): IntVal(min=0, max=2),
    Optional('temporal_layer'): IntVal(min=0, max=2),
    # For the playout-delay RTP extension, if negotiated
    Optional('min_delay'): IntVal(),    
    Optional('max_delay'): IntVal(), 
    AutoDel(str): object  # for all other key we must delete
})

subscriber_del_stream_schema = Schema({
    Optional('feed'): IntVal(min=1),
    Optional('mid'): StrVal(max_len=32),
    Optional('sub_mid'): StrVal(max_len=32),
    AutoDel(str): object  # for all other key we must delete
})

subscriber_join_schema = Schema({
    Optional('feed'): IntVal(min=1),
    Optional('private_id'): IntVal(min=1),
    Optional('close_pc'): BoolVal(),
    Optional('audio'): BoolVal(),
    Optional('video'): BoolVal(),
    Optional('data'): BoolVal(),
    Optional('offer_audio'): BoolVal(),
    Optional('offer_video'): BoolVal(),
    Optional('offer_data'): BoolVal(),
    Optional('substream'): IntVal(min=0, max=2),
    Optional('temporal'): IntVal(min=0, max=2),
    Optional('fallback'): IntVal(min=0),
    Optional('spatial_layer'): IntVal(min=0, max=2),
    Optional('temporal_layer'): IntVal(min=0, max=2),
    # For the playout-delay RTP extension, if negotiated
    Optional('min_delay'): IntVal(),    
    Optional('max_delay'): IntVal(), 
    Optional('streams'): ListVal(subscriber_stream_schema),     
    AutoDel(str): object  # for all other key we must delete
})

subscriber_subscribe_schema = Schema({
    'streams': ListVal(subscriber_stream_schema),  
    AutoDel(str): object  # for all other key we must delete
})

subscriber_unsubscribe_schema = Schema({
    'streams': ListVal(subscriber_del_stream_schema),  
    AutoDel(str): object  # for all other key we must delete
})

subscriber_update_schema = Schema({
    Optional('subscribe'): ListVal(subscriber_stream_schema), 
    Optional('unsubscribe'): ListVal(subscriber_del_stream_schema),  
    AutoDel(str): object  # for all other key we must delete
})

subscriber_configure_stream_schema = Schema({
    'mid': StrVal(max_len=32),
    Optional('send'): BoolVal(),
    # For VP8 (or H.264) simulcast 
    Optional('substream'): IntVal(min=0, max=2),
    Optional('temporal'): IntVal(min=0, max=2),
    Optional('fallback'): IntVal(min=0),  

    # For VP9 SVC
    Optional('spatial_layer'): IntVal(min=0, max=2),
    Optional('temporal_layer'): IntVal(min=0, max=2),
    # For the playout-delay RTP extension, if negotiated
    Optional('min_delay'): IntVal(),    
    Optional('max_delay'): IntVal(),   

})

subscriber_configure_schema = Schema({

    Optional('mid'): StrVal(max_len=32),
    Optional('send'): BoolVal(),
    #  Deprecated properties, use mid+send instead 
    Optional('audio'): BoolVal(),
    Optional('video'): BoolVal(),
    Optional('data'): BoolVal(),
    # used for sdp renegotiation and ICE restart
    Optional('update'): BoolVal(),
    Optional('restart'): BoolVal(),
    # For VP8 (or H.264) simulcast 
    Optional('substream'): IntVal(min=0, max=2),
    Optional('temporal'): IntVal(min=0, max=2),
    Optional('fallback'): IntVal(min=0),
    # For VP9 SVC
    Optional('spatial_layer'): IntVal(min=0, max=2),
    Optional('temporal_layer'): IntVal(min=0, max=2),
    # For the playout-delay RTP extension, if negotiated
    Optional('min_delay'): IntVal(),    
    Optional('max_delay'): IntVal(), 

    Optional('streams'): ListVal(subscriber_configure_stream_schema),  

    AutoDel(str): object  # for all other key we must delete
})

JANUS_VIDEOROOM_P_TYPE_NONE = 0
JANUS_VIDEOROOM_P_TYPE_SUBSCRIBER = 1
JANUS_VIDEOROOM_P_TYPE_PUBLISHER = 2


def _send_backend_message(backend_handle, body, jsep=None):
    if backend_handle is None:
        raise JanusCloudError('Not connected', JANUS_ERROR_INTERNAL_ERROR)
    data, reply_jsep = backend_handle.send_message(body=body, jsep=jsep)
    if 'error_code' in data:
        raise JanusCloudError(data.get('error', 'unknown'),
                              data.get('error_code', JANUS_VIDEOROOM_ERROR_UNKNOWN_ERROR))

    return data, reply_jsep

class BackendHandleManager(object):
    def __init__(self) -> None:
        self._backend_handles =  {}

    def get(self, server_name):
        return self._backend_handles.get(server_name)
    
    def exists(self, server_name):
        return server_name in self._backend_handles

    def create(self, server_name, server_url):
        
        backend_handle = self._backend_handles.get(server_name)
        if backend_handle:
            # if exists, just return the old one
            return backend_handle
        
        #if not exists , create new one
        # 1 get backend session
        backend_session = get_backend_session(server_url,
                                              auto_destroy=BACKEND_SESSION_AUTO_DESTROY_TIME)
        # 2. attach backend handle
        backend_handle = backend_session.attach_handle(
            JANUS_VIDEOROOM_PACKAGE, 
            opaque_id=server_name,
            handle_listener=self)

        # 3. check duplicated 
        if server_name not in self._backend_handles:
            self._backend_handles[server_name] = backend_handle
        else:
            dup_backend_handle = backend_handle
            backend_handle = self._backend_handles[server_name]
            dup_backend_handle.detach()
            
        
        return backend_handle
    
    def remove(self, server_name):
        backend_handle = self._backend_handles.pop(server_name, None)
        if backend_handle:
            backend_handle.detach()


    # backend handle listener callback
    def on_async_event(self, handle, event_msg):
        # no event need to process for the backend room control handle
        pass
    
    def on_close(self, handle):
        # log.debug('backend handle ({}) is closed'.format(
        #     handle.opaque_id))   
        server_name = handle.opaque_id
        cached_handle = self._backend_handles.get(server_name)
        if cached_handle == handle:
            # log.debug('backend handle ({}) is popped from BackendHandleManager'.format(
            #     handle.opaque_id))  
            self._backend_handles.pop(server_name, None)

_backend_handle_manager = BackendHandleManager()

class SubscriberStream(object):
    def __init__(self, mindex: int, type: str, mid: str, 
                 active=False, ready=False, send=False,
                 sources=0, source_ids=[],
                 feed_id=0, feed_display='', feed_mid='',
                 feed_description='',
                 codec='', 
                 h264_profile='', vp9_profile='',
                 playout_delay={},
                 simulcast={},
                 svc={}):
        self.mindex = mindex
        self.type = type
        self.mid = mid
        self.active = active
        self.ready = ready
        self.send = send
        self.sources = sources
        self.source_ids = source_ids
        self.feed_id = feed_id
        self.feed_display = feed_display
        self.feed_mid = feed_mid
        self.feed_description = feed_description
        self.codec = codec
        self.h264_profile = h264_profile
        self.vp9_profile = vp9_profile
        self.playout_delay = playout_delay
        self.simulcast = simulcast
        self.svc = svc

class VideoRoomSubscriber(object):

    def __init__(self, handle, owner=None):

        self.room_id = 0
        self.room_wref = None

        self._frontend_handle = handle

        self.webrtc_started = False  # webrtc peerconnection is up or not

        self.streams = []
        self.streams_bymid = {}

        self._feeds = {}               # Participant this subscriber is subscribed to
        self._data_feed_ids = set()
        self._media_feed_ids = set()

        self._owner = owner        # Participant who owns this subscriber
        self._owner_id = owner.user_id if owner else 0

        self._kicked = False      #  Whether this subscription belongs to a participant that has been kicked
        self._paused = False

        self._has_destroyed = False

        self._wait_sdp_answer = False

        self.sdp = ''  # Offer we sent this listener (may be updated within renegotiations)

        self._backend_handle = None
        self._backend_room = None

        self.utime = time.time()
        self.ctime = time.time()

    def __str__(self):
        return 'Video Room Subscriber (room_id:{}, owner_id:{})'.format(
            self.room_id, self._owner_id)

    def destroy(self):

        if self._has_destroyed:
            return
        self._has_destroyed = True

        self.streams.clear()
        self.streams_bymid.clear()

        if self.room_wref:
            self.room_id = 0
            self.room_wref = None

        self._backend_room = None

        self._wait_sdp_answer = False

        if self._owner:
            # remove from room
            publisher = self._owner
            self._owner = None
            publisher.del_subscription(self)

        for publisher in self._feeds.values():
            publisher.del_subscriber(self)
        self._feeds.clear()
        self._data_feed_ids.clear()
        self._media_feed_ids.clear()

        if self._backend_handle:
            backend_handle = self._backend_handle
            self._backend_handle = None
            # detach the backend_handle
            backend_handle.detach()

        if self.webrtc_started:
            self.webrtc_started = False
            if self._frontend_handle:
                self._frontend_handle.push_event(method='hangup', transaction=None, reason='Close PC')

        if self._frontend_handle:
            self._frontend_handle.on_participant_detach(self)
            self._frontend_handle = None

    def _assert_valid(self):
        if self._has_destroyed:
            raise JanusCloudError('Subscriber already destroyed (room_id: {}, owner_id: {})'.format(
                self.room_id, self._owner_id),
                JANUS_VIDEOROOM_ERROR_ALREADY_DESTROYED)

    def _is_cascade(self, publisher, self_backend_room=None):
        
        if self_backend_room is None:
            self_backend_room = self._backend_room
        if self_backend_room is None:
            return False                                 
        publisher_backend_room = publisher.get_backend_room()
        if publisher_backend_room is None:
            return False
        if publisher_backend_room.server_name == self_backend_room.server_name:
            return False
        return True

    def _sync_feeds(self):

        room = self.room_wref()
        if room is None:
            return # room is destroyed, no need to sync feeds

        cur_feed_ids = self._data_feed_ids |  self._media_feed_ids
        old_feed_ids = set(self._feeds.keys())
        if cur_feed_ids == old_feed_ids:
            return # already sync
        del_feed_ids = old_feed_ids - cur_feed_ids
        add_feed_ids = cur_feed_ids - old_feed_ids

        for user_id in del_feed_ids:
            publisher = self._feeds.pop(user_id, None)
            if publisher:
                publisher.del_subscriber(self)
        
        for user_id in add_feed_ids:
            publisher = room.get_participant_by_user_id(user_id)
            if publisher:
                publisher.add_subscriber(self)
                self._feeds[user_id] = publisher

    def _update_streams(self, streams_info):

        self.streams.clear()
        self.streams_bymid.clear()
        room = self.room_wref()
        self._media_feed_ids.clear()
        self._data_feed_ids.clear()
        
        for index, stream_info in enumerate(streams_info):
            stream_info = dict(stream_info)
            mindex = stream_info.pop('mindex', index)
            # change '-' to '_'
            if 'h264-profile' in stream_info:
                stream_info['h264_profile'] = stream_info.pop('h264-profile')
            if 'vp9-profile' in stream_info:
                stream_info['vp9_profile'] = stream_info.pop('vp9-profile')
            if 'playout-delay' in stream_info:
                stream_info['playout_delay'] = stream_info.pop('playout-delay')
 
            stream = SubscriberStream(mindex=mindex, **stream_info)
            self.streams.append(stream)
            self.streams_bymid[stream.mid] = stream
            
            if stream.type == 'data':
                # data stream
                if stream.source_ids:
                    self._data_feed_ids.update(stream.source_ids)
            else:
                # media stream
                self._media_feed_ids.add(stream.feed_id)
                # update the feed info according to janus-proxy
                if stream.feed_id and room is not None and room.user_id_exists(stream.feed_id):
                    # update the feed info 
                    publisher = room.get_participant_by_user_id(stream.feed_id)
                    stream.feed_display = publisher.display
                    if stream.feed_mid:
                        if self._is_cascade(publisher):
                            feed_index = int(stream.feed_mid)
                            if feed_index < len(publisher.streams):
                                ps = publisher.streams[feed_index]
                                stream.feed_mid = ps.mid
                                stream.feed_description = ps.description
                        else:
                            ps = publisher.streams_bymid.get(stream.feed_mid)
                            if ps:
                                stream.feed_description = ps.description

        # sync *_feed_ids to _feeds
        self._sync_feeds()

    def streams_info(self):
        media = []
        for stream in self.streams:
            info = {
                'type': stream.type,
                'mindex': stream.mindex,
                'mid': stream.mid,
                'active': stream.active,
                'ready': stream.ready,
                'send': stream.send
            }
            if stream.type == 'data':
                if stream.sources:
                    info['sources'] = stream.sources
                if stream.source_ids:
                    info['source_ids'] = stream.source_ids
            else:
                if stream.feed_id:
                    info['feed_id'] = stream.feed_id
                if stream.feed_display:
                    info['feed_display'] = stream.feed_display
                if stream.feed_mid:
                    info['feed_mid'] = stream.feed_mid
                if stream.feed_description:
                    info['feed_description'] = stream.feed_description
                info['codec'] = stream.codec
                if stream.type == 'video':
                    if stream.codec == 'h264' and stream.h264_profile:
                        info['h264-profile'] = stream.h264_profile
                    elif stream.codec == 'vp9'and stream.vp9_profile:
                        info['vp9-profile'] = stream.vp9_profile
                    if stream.playout_delay:
                        info['playout-delay'] = stream.playout_delay
                if stream.simulcast:
                    info['simulcast'] = stream.simulcast
                if stream.svc:
                    info['svc'] = stream.svc
            media.append(info)

        return media
                
    def join(self, room, streams=[], feed=0, **kwargs):

        self._assert_valid()

        if self.room_wref is not None:
            raise JanusCloudError('Already in as a subscriber on this handle',
                                  JANUS_VIDEOROOM_ERROR_ALREADY_JOINED)
        
        if self._backend_handle is not None:
            raise JanusCloudError('Already join',
                                  JANUS_VIDEOROOM_ERROR_ALREADY_BACKEND)
        
        publishers = []
        if streams:
            # for new multi-stream API check all stream's feed
            feed_ids = set()
            for stream in streams:
                feed = stream.get('feed', 0)
                publisher = room.get_participant_by_user_id(feed)
                if publisher is None or not publisher.webrtc_started:
                    raise JanusCloudError('No such feed ({})'.format(feed),
                                        JANUS_VIDEOROOM_ERROR_NO_SUCH_FEED) 
                mid = stream.get('mid', '')  
                if mid:
                    if mid not in publisher.streams_bymid:
                        raise JanusCloudError('No such mid \'{}\' in feed ({})'.format(mid, feed),
                                        JANUS_VIDEOROOM_ERROR_NO_SUCH_FEED) 
                


                if publisher.streams and feed not in feed_ids:
                    feed_ids.add(feed)
                    publishers.append(publisher)

        elif feed:
            # for deprecated API, check the feed            
            publisher = room.get_participant_by_user_id(feed)
            if publisher is None or not publisher.webrtc_started:
                raise JanusCloudError('No such feed ({})'.format(feed),
                                        JANUS_VIDEOROOM_ERROR_NO_SUCH_FEED)
            
            do_data = kwargs.get('offer_data', True)
            do_video = kwargs.get('offer_video', True)
            do_audio = kwargs.get('offer_audio', True)
            stream_num = 0
            for ps in publisher.streams:
                if ps.type == 'data' and do_data: 
                    stream_num += 1
                elif ps.type == 'video' and do_video:
                    stream_num += 1
                elif ps.type == 'audio' and do_audio:
                    stream_num += 1
            if stream_num:
                publishers.append(publisher)
        else:
            raise JanusCloudError("At least one between 'streams' and 'feed' must be specified",
                                  JANUS_VIDEOROOM_ERROR_MISSING_ELEMENT)            

        if not publishers:
            # not found any feeds
            raise JanusCloudError("Can't offer an SDP with no stream",
                                  JANUS_VIDEOROOM_ERROR_INVALID_SDP)            
        if len(publishers) > 1:
            raise JanusCloudError("Can only subcribe one feed in non-cascade mode",
                                  JANUS_VIDEOROOM_ERROR_INVALID_REQUEST)

        # for non-cascade mode
        publisher = publishers[0]
        backend_room = publisher.get_backend_room()

        if backend_room is None:
            raise JanusCloudError('No such feed ({})'.format(publisher.user_id),
                                  JANUS_VIDEOROOM_ERROR_NO_SUCH_FEED)

        # change mid to the backend mid
        for stream in streams:
            publisher = room.get_participant_by_user_id(stream.get('feed', 0))  
            if publisher:
                mid = stream.get('mid', '')  
                if mid and mid in publisher.streams_bymid:
                    if self._is_cascade(publisher=publisher, self_backend_room=backend_room):
                        ps = publisher.streams_bymid[mid]
                        stream['mid'] = '{}'.format(ps.mindex)

        # backend session
        backend_session = get_backend_session(backend_room.server_url,
                                              auto_destroy=BACKEND_SESSION_AUTO_DESTROY_TIME)
        # attach backend handle
        backend_handle = backend_session.attach_handle(JANUS_VIDEOROOM_PACKAGE, handle_listener=self)
        try:
            # join the backend room as a subscriber
            body = {
                'request':  'join',
                'ptype': 'subscriber',
                'room': backend_room.backend_room_id,
            }
            if feed:
                body['feed'] = feed
            if streams:
                body['streams'] = streams
  
            if len(kwargs) > 0:
                for k, v in kwargs.items():
                    if k not in body:
                        body[k] = v

            reply_data, reply_jsep = _send_backend_message(backend_handle, body)

            self._backend_handle = backend_handle
            self._backend_room = backend_room
            self.room_wref = weakref.ref(room)
            self.room_id = room.room_id

            if reply_jsep:
                self.sdp = reply_jsep.get('sdp', '')
                if self.sdp:
                    self._wait_sdp_answer = True  

            if 'streams' in reply_data:
                self._update_streams(reply_data['streams'])   

            return reply_jsep

        except Exception:
            backend_handle.detach()
            self._backend_handle = None
            self._backend_room = None
            self.room_wref = None
            self.room_id = 0
            raise

    def subscribe(self, streams=[]):

        self._assert_valid()
        if self._backend_handle is None:
            raise JanusCloudError('backend handle invalid',
                                  JANUS_VIDEOROOM_ERROR_JOIN_FIRST) 
        room = self.room_wref() 
        if room is None:
            raise JanusCloudError('No such room ({})'.format(self.room_id),
                                  JANUS_VIDEOROOM_ERROR_NO_SUCH_ROOM)
        if not streams:
            # streams is empty
            raise JanusCloudError('Empty subscription list',
                                  JANUS_VIDEOROOM_ERROR_INVALID_ELEMENT)

        for stream in streams:
            feed = stream.get('feed', 0)
            publisher = room.get_participant_by_user_id(feed)
            if publisher is None or not publisher.webrtc_started:
                raise JanusCloudError('No such feed ({})'.format(feed),
                                    JANUS_VIDEOROOM_ERROR_NO_SUCH_FEED) 

            if feed not in self._feeds:
                raise JanusCloudError("Can only subcribe one feed in non-cascade mode",
                                    JANUS_VIDEOROOM_ERROR_INVALID_REQUEST)

            mid = stream.get('mid', '')  
            if mid:
                # only subscribe one stream
                if mid in publisher.streams_bymid:
                    ps = publisher.streams_bymid[mid]
                    # change mid to backend mid if needed
                    if self._is_cascade(publisher=publisher):
                        stream['mid'] = '{}'.format(ps.mindex)                       
                else:
                    raise JanusCloudError('No such mid \'{}\' in feed ({})'.format(mid, feed),
                                        JANUS_VIDEOROOM_ERROR_NO_SUCH_FEED)

        # join the backend room as a subscriber
        body = {
            'request': 'subscribe',
            'streams': streams
        }

        reply_data, reply_jsep = _send_backend_message(self._backend_handle, body)

        if reply_jsep:
            self.sdp = reply_jsep.get('sdp', '')
            if self.sdp:
                self._wait_sdp_answer = True 

        if 'streams' in reply_data:
            self._update_streams(reply_data['streams']) 
       

        return reply_data.get('videoroom',''), reply_jsep

    def unsubscribe(self, streams=[]):
        self._assert_valid()

        if self._backend_handle is None:
            raise JanusCloudError('backend handle invalid',
                                  JANUS_VIDEOROOM_ERROR_JOIN_FIRST) 

        room = self.room_wref()
        if room is None:
            raise JanusCloudError('No such room ({})'.format(self.room_id),
                                  JANUS_VIDEOROOM_ERROR_NO_SUCH_ROOM)

        if not streams:
            # streams is empty
            raise JanusCloudError('Empty unsubscription list',
                                  JANUS_VIDEOROOM_ERROR_INVALID_ELEMENT)

        for stream in streams:
            sub_mid = stream.get('sub_mid', '')
            if sub_mid:
                ss = self.streams_bymid.get(sub_mid)
                if ss is None:
                    log.warning('Subscriber stream with mid \'{}\' not found, not unsubscribing...'.format(sub_mid))

            else:
                feed = stream.get('feed', 0)
                publisher = room.get_participant_by_user_id(feed)
                if publisher is None or not publisher.webrtc_started:
                    log.warning('Publisher \'{}\' not found, not unsubscribing...'.format(feed))

                if publisher is not None and 'mid' in stream :
                    # change mid to backend mid if needed
                    if self._is_cascade(publisher):
                        ps = publisher.streams_bymid.get(stream['mid'])
                        if ps is not None:
                            stream['mid'] = '{}'.format(ps.mindex) 

        # join the backend room as a subscriber
        body = {
            'request': 'unsubscribe',
            'streams': streams
        }

        reply_data, reply_jsep = _send_backend_message(self._backend_handle, body)

        if reply_jsep:
            self.sdp = reply_jsep.get('sdp', '')
            if self.sdp:
                self._wait_sdp_answer = True  

        if 'streams' in reply_data:
            self._update_streams(reply_data['streams'])  

        return reply_data.get('videoroom', ''), reply_jsep



    def update(self, subscribe=None, unsubscribe=None):
        self._assert_valid()

        if self._backend_handle is None:
            raise JanusCloudError('backend handle invalid',
                                  JANUS_VIDEOROOM_ERROR_JOIN_FIRST) 

        room = self.room_wref()
        if room is None:
            raise JanusCloudError('No such room ({})'.format(self.room_id),
                                  JANUS_VIDEOROOM_ERROR_NO_SUCH_ROOM)

        if subscribe is None and unsubscribe is None:
            raise JanusCloudError("At least one of either 'subscribe' or 'unsubscribe' must be present",
                                  JANUS_VIDEOROOM_ERROR_MISSING_ELEMENT)            

        if subscribe is not None:
            # Adding new subscriber streams
            if len(subscribe) == 0:
                # streams is empty
                raise JanusCloudError('Empty subscription list',
                                    JANUS_VIDEOROOM_ERROR_INVALID_ELEMENT)  
            for stream in subscribe:
                feed = stream.get('feed', 0)
                publisher = room.get_participant_by_user_id(feed)
                if publisher is None or not publisher.webrtc_started:
                    raise JanusCloudError('No such feed ({})'.format(feed),
                                        JANUS_VIDEOROOM_ERROR_NO_SUCH_FEED) 

                if feed not in self._feeds:
                    raise JanusCloudError("Can only subcribe one feed in non-cascade mode",
                                        JANUS_VIDEOROOM_ERROR_INVALID_REQUEST)

                mid = stream.get('mid', '')  
                if mid:
                    # only subscribe one stream
                    if mid in publisher.streams_bymid:
                        ps = publisher.streams_bymid[mid]
                        # change mid to backend mid if needed
                        if self._is_cascade(publisher=publisher):
                            stream['mid'] = '{}'.format(ps.mindex)                       
                    else:
                        raise JanusCloudError('No such mid \'{}\' in feed ({})'.format(mid, feed),
                                            JANUS_VIDEOROOM_ERROR_NO_SUCH_FEED)

        if unsubscribe is not None:
            # Removing subscriber streams
            if len(unsubscribe) == 0:
                # streams is empty
                raise JanusCloudError('Empty unsubscription list',
                                      JANUS_VIDEOROOM_ERROR_INVALID_ELEMENT)
            for stream in unsubscribe:
                sub_mid = stream.get('sub_mid', '')
                if sub_mid:
                    ss = self.streams_bymid.get(sub_mid)
                    if ss is None:
                        log.warning('Subscriber stream with mid \'{}\' not found, not unsubscribing...'.format(sub_mid))
                else:
                    feed = stream.get('feed', 0)
                    publisher = room.get_participant_by_user_id(feed)
                    if publisher is None or not publisher.webrtc_started:
                        log.warning('Publisher \'{}\' not found, not unsubscribing...'.format(feed))

                    if publisher is not None and 'mid' in stream :
                        # change mid to backend mid if needed
                        if self._is_cascade(publisher):
                            ps = publisher.streams_bymid.get(stream['mid'])
                            if ps is not None:
                                stream['mid'] = '{}'.format(ps.mindex) 



        # join the backend room as a subscriber
        body = {
            'request': 'update',
        }
        if subscribe is not None:
            body['subscribe'] = subscribe
        if unsubscribe is not None:
            body['unsubscribe'] = unsubscribe

        reply_data, reply_jsep = _send_backend_message(self._backend_handle, body)

        if reply_jsep:
            self.sdp = reply_jsep.get('sdp', '')
            if self.sdp:
                self._wait_sdp_answer = True  

        if 'streams' in reply_data:
            self._update_streams(reply_data['streams'])  

        return reply_data.get('videoroom', ''), reply_jsep

    def configure(self, mid='', send=None,
                  audio=None, video=None, data=None,
                  update=False, restart=False,
                  substream=-1, temporal=-1, fallback=-1,
                  spatial_layer=-1, temporal_layer=-1,
                  min_delay=-1, max_delay=-1,
                  streams=[],
                  **kwargs):
        self._assert_valid()
        if self._kicked:
            raise JanusCloudError('Unauthorized, you have been kicked',
                                  JANUS_VIDEOROOM_ERROR_UNAUTHORIZED)

        if self._backend_handle is None:
            raise JanusCloudError('backend handle invalid',
                                  JANUS_VIDEOROOM_ERROR_JOIN_FIRST)
        if update or restart:
            # need renegotiate and will create a new offer
            if self._wait_sdp_answer:
                raise JanusCloudError('Still waiting for sdp answer',
                                    JANUS_VIDEOROOM_ERROR_INVALID_REQUEST)                
        # check twhether the given mid exists
        if streams:
            for stream in streams:
                if stream.get('mid', '') not in self.streams_bymid:
                    raise JanusCloudError('No such mid \'{}\' in subscription'.format(mid),
                                           JANUS_VIDEOROOM_ERROR_NO_SUCH_FEED)     
        elif mid and mid not in self.streams_bymid:
            raise JanusCloudError('No such mid \'{}\' in subscription'.format(mid),
                                  JANUS_VIDEOROOM_ERROR_NO_SUCH_FEED)          

        # backend configure
        # send request to backend
        body = {
            'request': 'configure',
        }
        if update:
            body['update'] = update,
        if restart:
            body['restart'] = restart
        if streams:
            body['streams'] = streams
            audio = None
            video = None
            data = None
        else:
            if mid:
                body['mid'] = mid
            if send is not None:
                body['send'] = bool(send)
            if substream >= 0:
                body['substream'] = substream
            if temporal >= 0:
                body['temporal'] = temporal
            if fallback >= 0:
                body['fallback'] = fallback
            if spatial_layer >= 0:
                body['spatial_layer'] = spatial_layer
            if temporal_layer >= 0:
                body['temporal_layer'] = temporal_layer
            if min_delay >= 0:
                body['min_delay'] = min_delay
            if max_delay >= 0:
                body['max_delay'] = max_delay
            if audio is not None:
                body['audio'] = bool(audio)
            if video is not None:
                body['video'] = bool(video)
            if data is not None:
                body['data'] = bool(data)

        if len(kwargs) > 0:
            for k, v in kwargs.items():
                if k not in body:
                    body[k] = v

        reply_data, reply_jsep = _send_backend_message(self._backend_handle, body=body)

        # successful
        if reply_jsep:
            self.sdp = reply_jsep.get('sdp', '')
            if self.sdp:
                self._wait_sdp_answer = True  

        return reply_jsep

    def start(self, jsep):
        self._assert_valid()
        if self._kicked:
            raise JanusCloudError('Unauthorized, you have been kicked',
                                  JANUS_VIDEOROOM_ERROR_UNAUTHORIZED)
        if self._backend_handle is None:
            raise JanusCloudError('backend handle invalid',
                                  JANUS_VIDEOROOM_ERROR_JOIN_FIRST)

        org_wait_sdp_answer = self._wait_sdp_answer

        if jsep is not None:
            self._wait_sdp_answer = False
        
        try:
            # backend start
            _send_backend_message(self._backend_handle, body={
                'request': 'start'
            }, jsep=jsep)
            self._paused = False
        except Exception:
            self._wait_sdp_answer = org_wait_sdp_answer

    def pause(self):

        self._paused = True
        # backend pause
        if self._backend_handle:
            _send_backend_message(self._backend_handle, body={
                'request': 'pause'
            })

    def kick(self):
        if self._kicked:
            return     # already kick
        self._kicked = True
        try:
            # pause the backend relay
            if self._backend_handle:
                self._backend_handle.async_send_message({
                    'request': 'pause'
                })
        except Exception:
            log.warning('Subscriber (room_id:{}, owner_id:{}) pause error for kicked'.format(
                self.room_id, self._owner_id))

    def hangup(self):
        if self._has_destroyed:
            return
        if self._backend_handle and self.webrtc_started:
            self._backend_handle.send_hangup()

    def trickle(self, candidate=None, candidates=None):
        if self._has_destroyed:
            return
        if self._backend_handle:
            self._backend_handle.send_trickle(candidate=candidate, candidates=candidates)

    def on_async_event(self, handle, event_msg):
        if self._has_destroyed:
            return

        if event_msg['janus'] == 'event':
            data = event_msg['plugindata']['data']
            jsep = event_msg.get('jsep')
            op = data.get('videoroom', '')
            if op == 'slow_link':
                if self._frontend_handle:
                    self._frontend_handle.push_plugin_event(data, jsep)
            elif op == 'event':
                if 'substream' in data or 'temporal' in data or \
                    'spatial_layer' in data or 'temporal_layer' in data:
                    reply_event = data.copy()
                    reply_event['videoroom'] = 'event'
                    reply_event['room'] = self.room_id     
                    if self._frontend_handle:
                        self._frontend_handle.push_plugin_event(reply_event, jsep)               
                else:
                    # ignore other event
                    pass   
            elif op == 'updated':
                if jsep:
                    self.sdp = jsep.get('sdp', '')
                    if self.sdp:
                        self._wait_sdp_answer = True  
                if 'streams' in data:
                    self._update_streams(data['streams'])

                reply_event = {
                    'videoroom': 'updated',
                    'room': self.room_id
                }
                if 'streams' in data:
                    reply_event['streams'] = self.streams_info()

                if self._frontend_handle:
                        self._frontend_handle.push_plugin_event(reply_event, jsep)  
            else:
                # ignore other operations
                pass
        else:
            if event_msg['janus'] == 'webrtcup':
                # webrtc pc is up
                self.webrtc_started = True

            elif event_msg['janus'] == 'hangup':
                # webrtc pc is closed
                self.webrtc_started = False
                self._paused = True
                self.sdp = ''
                self._wait_sdp_answer = False
                self.streams.clear()
                self.streams_bymid.clear()

                for publisher in self._feeds.values():
                    publisher.del_subscriber(self)
                self._feeds.clear()
                self._data_feed_ids.clear()
                self._media_feed_ids.clear()

            params = dict()
            for key, value in event_msg.items():
                if key not in ['janus', 'session_id', 'sender', 'opaque_id', 'transaction']:
                    params[key] = value
            if self._frontend_handle:
                self._frontend_handle.push_event(event_msg['janus'], None, **params)

    def on_close(self, handle):
        if self._has_destroyed:
            return
        self._backend_handle = None     #detach with backend handle
        self.destroy()

    def on_feed_hangup(self, publisher):

        self._data_feed_ids.discard(publisher.user_id)
        self._media_feed_ids.discard(publisher.user_id)
        self._feeds.pop(publisher.user_id, None)

    def on_owner_destroy(self, publisher):
        if self._owner == publisher:
            self._owner = None


class PublisherStream(object):
    def __init__(self, mindex: int, type: str, mid: str, 
                 disabled=False, codec='', 
                 description='', moderated=False, 
                 fec=False, dtx=False, stereo=False,
                 audiolevel_ext=False, talking=False,
                 h264_profile='', vp9_profile='',
                 simulcast=False, svc=False):
        self.mindex = mindex
        self.type = type
        self.mid = mid
        self.disabled = disabled
        self.codec = codec
        self.description = description
        self.moderated = moderated
        self.fec = fec
        self.dtx = dtx
        self.stereo = stereo
        self.audiolevel_ext = audiolevel_ext
        self.talking = talking
        self.h264_profile = h264_profile
        self.vp9_profile = vp9_profile
        self.simulcast = simulcast
        self.svc = svc

class VideoRoomPublisher(object):

    def __init__(self, user_id, handle, display=''):
        self.user_id = user_id     # Unique ID in the room
        self.display = display     # Display name (just for fun)

        self.room = None      # Room
        self.room_id = 0      # deal later
        self.webrtc_started = False  # webrtc peerconnection is up or not

        self.sdp = ''              # The SDP this publisher negotiated, if any
        self.acodec = ''           # Audio codec this publisher is using
        self.vcodec = ''           # Video codec this publisher is using

        self.user_audio_active_packets = 0  # Participant's audio_active_packets overwriting global room setting
        self.user_audio_level_average = 0  # Participant's audio_level_average overwriting global room setting

        self.pvt_id = 0     # This is sent to the publisher for mapping purposes, but shouldn't be shared with others
        self.e2ee = False

        self.data_stream = None   # whether thereis data-type stream 
        self.streams = []
        self.streams_bymid = {}

        self._rtp_forwarders = {}

        self._frontend_handle = handle

        self._record_active = None

        # backend handle info
        self._backend_handle = None
        self._backend_room = None

        self._has_destroyed = False

        self._subscribers = set()    # Subscriptions to this publisher (who's watching this publisher)
        self._subscriptions = set()  # Subscriptions this publisher has created (who this publisher is watching)

        self.utime = time.time()
        self.ctime = time.time()

    def destroy(self):
        if self._has_destroyed:
            return
        self._has_destroyed = True

        self.streams.clear()
        self.streams_bymid.clear()
        self.data_stream = None

        if len(self._subscriptions) > 0:
            subscriptions = self._subscriptions.copy()
            self._subscriptions.clear()
            for subscriber in subscriptions:
                subscriber.on_owner_destroy(self)

        self._rtp_forwarders.clear()

        if self._backend_room:
            self._backend_room.del_publisher(self.user_id)
            self._backend_room = None

        if self.room:
            # remove from room
            self.room.on_participant_destroy(self.user_id)
            self.room = None
            self.room_id = 0

        if self._backend_handle:
            backend_handle = self._backend_handle
            self._backend_handle = None


            # detach backend handle directly to make destroy() faster
            # 1. leave the room
            # try:
            #     backend_handle.send_message({
            #         'request': 'leave',
            #     })
            # except Exception:
            #     pass  # ignore leave failed

            # 2. detach the backend_handle
            backend_handle.detach()

        if self.webrtc_started:
            self.webrtc_started = False
            if self._frontend_handle:
                self._frontend_handle.push_event(method='hangup', transaction=None, reason='Close PC')
            if len(self._subscribers) > 0:
                subscribers = self._subscribers.copy()
                self._subscribers.clear()
                for subscriber in subscribers:
                    subscriber.on_feed_hangup(self)

        if self._frontend_handle:
            self._frontend_handle.on_participant_detach(self)
            self._frontend_handle = None

        log.info('Video Room Publisher "{0}"({1}) is destroyed'.format(self.user_id, self.display))

    def __str__(self):
        return 'Video Room Publisher "{0}"({1})'.format(self.user_id, self.display)

    def _assert_valid(self):
        if self._has_destroyed:
            raise JanusCloudError('Already destroyed {} ({})'.format(self.user_id, self.display),
                                  JANUS_VIDEOROOM_ERROR_ALREADY_DESTROYED)

    def join(self, room, backend_room):
        self._assert_valid()

        if self._backend_handle is not None:
            raise JanusCloudError('Already construct backend handle {} ({})'.format(self.user_id, self.display),
                                  JANUS_VIDEOROOM_ERROR_ALREADY_BACKEND)
        if self.room is not None:
            raise JanusCloudError('Already in as a publisher on this handle',
                                    JANUS_VIDEOROOM_ERROR_ALREADY_JOINED)

        # backend session
        backend_session = get_backend_session(backend_room.server_url,
                                              auto_destroy=BACKEND_SESSION_AUTO_DESTROY_TIME)

        # attach backend handle
        backend_handle = backend_session.attach_handle(JANUS_VIDEOROOM_PACKAGE, handle_listener=self)
        try:

            # join the single room
            body = {
                'request':  'join',
                'ptype': 'publisher',
                'room': backend_room.backend_room_id,
                'id': self.user_id
            }
            if self.display:
                body['display'] = self.display
            _send_backend_message(backend_handle, body)

        except Exception:
            backend_handle.detach()
            raise

        self._backend_handle = backend_handle
        self._backend_room = backend_room
        backend_room.add_publisher(self.user_id)

        self.room = room
        self.room_id = room.room_id

    def get_backend_room(self):
        return self._backend_room

    def _update_streams(self, streams_info):
        
        org_streams_bymid = self.streams_bymid.copy()
        self.streams.clear()
        self.streams_bymid.clear()
        self.data_stream = None

        for index, stream_info in enumerate(streams_info):
            stream_info = dict(stream_info)
            mindex = stream_info.pop('mindex', index)

            stream = PublisherStream(mindex=mindex, **stream_info)
            # Some of the stream's properties can not be acquired from the new streams_info,
            # so they are acruired from the old one
            org_stream = org_streams_bymid.get(stream.mid)
            if org_stream:
                stream.audiolevel_ext = org_stream.audiolevel_ext
                stream.talking = org_stream.talking
                stream.moderated = org_stream.moderated

            self.streams.append(stream)
            self.streams_bymid[stream.mid] = stream
            if stream.type == 'data':
                self.data_stream = stream

        del org_streams_bymid

    def streams_info(self):
        media = []
        for stream in self.streams:
            info = {
                'type': stream.type,
                'mindex': stream.mindex,
                'mid': stream.mid
            }
            if stream.disabled:
                info['disabled'] = True
            else:
                if stream.description:
                    info['description'] = stream.description
                if stream.moderated:
                    info['moderated'] = True

                if stream.type == 'audio':
                    info['codec'] = stream.codec
                    if stream.fec:
                        info['fec'] = True
                    if stream.dtx:
                        info['dtx'] = True
                    if stream.stereo:
                        info['stereo'] = True
                    if stream.audiolevel_ext:
                        info['talking'] = stream.talking
                elif stream.type == 'video':
                    info['codec'] = stream.codec
                    if stream.codec == 'h264' and stream.h264_profile:
                        info['h264_profile'] = stream.h264_profile
                    if stream.codec == 'vp9' and stream.vp9_profile:
                        info['vp9_profile'] = stream.vp9_profile
                    if stream.simulcast:
                        info['simulcast'] = True
                    if stream.svc:
                        info['svc'] = True
            media.append(info)
        return media

    def publish(self, 
                audiocodec='', videocodec='',
                bitrate=-1,
                record=None, filename='',
                display='',
                secret='',
                audio_active_packets=0, audio_level_average=0,
                descriptions=[],
                jsep=None,
                **kwargs):
        self._assert_valid()
        if self.sdp or self.webrtc_started:
            raise JanusCloudError('Can\'t publish, already published',
                                  JANUS_VIDEOROOM_ERROR_ALREADY_PUBLISHED)

        return self.configure(audiocodec=audiocodec, videocodec=videocodec,
                              bitrate=bitrate,
                              record=record, filename=filename,
                              display=display,
                              secret=secret,
                              audio_active_packets=audio_active_packets, audio_level_average=audio_level_average,
                              descriptions=descriptions,
                              jsep=jsep,
                              **kwargs)

    def configure(self, mid='', send=None, 
                  audio=None, video=None, data=None,
                  audiocodec='', videocodec='',
                  bitrate=-1, keyframe=False,
                  record=None, filename='',
                  secret='',
                  audio_active_packets=0, audio_level_average=0,
                  min_delay=-1, max_delay=-1,
                  display='', update=False,
                  descriptions=[],
                  streams=[],
                  jsep=None,
                  **kwargs):

        # check param conflict
        self._assert_valid()
        if self._backend_handle is None:
            raise JanusCloudError('Backend handle invalid for publisher {}({})'.format(self.user_id, self.display),
                                  JANUS_VIDEOROOM_ERROR_INVALID_ELEMENT)

        if audiocodec:
            if audiocodec not in self.room.audiocodec:
                log.error('Participant asked for audio codec \'{}\', but it\'s not allowed (room {}, user {})'.format(
                    audiocodec, self.room_id, self.user_id))
                raise JanusCloudError('Audio codec unavailable in this room',
                                      JANUS_VIDEOROOM_ERROR_ALREADY_PUBLISHED)
            # log.debug('Participant asked for audio codec \'{}\' (room {}, user {})'.format(
            #     audiocodec, self.room_id, self.user_id))
        if videocodec:
            if videocodec not in self.room.videocodec:
                log.error('Participant asked for video codec \'{}\', but it\'s not allowed (room {}, user {})'.format(
                    videocodec, self.room_id, self.user_id
                ))
                raise JanusCloudError('Video codec unavailable in this room',
                                      JANUS_VIDEOROOM_ERROR_ALREADY_PUBLISHED)
            # log.debug('Participant asked for video codec \'{}\' (room {}, user {})'.format(
            #     videocodec, self.room_id, self.user_id))
        e2ee = jsep.get('e2ee', False) if jsep is not None else False
        if self.room and self.room.require_e2ee and not e2ee and not self.e2ee:
            log.error('Room requires end-to-end encrypted media')
            raise JanusCloudError('Room requires end-to-end encrypted media',
                                   JANUS_VIDEOROOM_ERROR_UNAUTHORIZED)       

        # check twhether the given mid exists
        if streams:
            for stream in streams:
                if stream.get('mid', '') not in self.streams_bymid:
                    raise JanusCloudError('No such mid \'{}\' published'.format(mid),
                                           JANUS_VIDEOROOM_ERROR_NO_SUCH_FEED)     
        elif mid and mid not in self.streams_bymid:
            raise JanusCloudError('No such mid \'{}\' published'.format(mid),
                                  JANUS_VIDEOROOM_ERROR_NO_SUCH_FEED)          

        # check record lock
        record_locked = False
        if (record is not None or filename) and self.room.lock_record and self.room.secret:
            if secret != self.room.secret:
                record_locked = True


        # send request to backend
        body = {
            'request': 'configure',
        }
        if streams:
            body['streams'] = streams
            audio = None
            video = None
            data = None
            
        else:
            if mid:
                body['mid'] = mid
            if send is not None:
                body['send'] = send            
            if keyframe:
                body['keyframe'] = keyframe
            if min_delay >= 0:
                body['min_delay'] = min_delay
            if max_delay >= 0:
                body['max_delay'] = max_delay
            if audio is not None:
                body['audio'] = audio
            if video is not None:
                body['video'] = video
            if data is not None:
                body['data'] = data

        if update:
            body['update'] = update
        if audiocodec:
            body['audiocodec'] = audiocodec
        if videocodec:
            body['videocodec'] = videocodec
        if bitrate >= 0:
            body['bitrate'] = bitrate
        if descriptions:
            body['descriptions'] = descriptions

        if record is not None and not record_locked:
            body['record'] = record
        else:
            if self._record_active is None: # not yet configured, use room's record setting
                body['record'] = self.room.record
            else:  # already configured, not change
                pass
        if filename and not record_locked:
            body['filename'] = filename
        if display:
            body['display'] = display
        if audio_active_packets:
            body['audio_active_packets'] = audio_active_packets
        if audio_level_average:
            body['audio_level_average'] = audio_level_average


        # other future parameters
        if len(kwargs) > 0:
            for k, v in kwargs.items():
                if k not in body:
                    body[k] = v

        reply_data, reply_jsep = _send_backend_message(self._backend_handle, body=body, jsep=jsep)

        # successful
        if 'streams' in reply_data:
            self._update_streams(reply_data['streams'])

        if reply_jsep:
            self.sdp = reply_jsep.get('sdp', '')
            log.debug('Setting sdp property len={} (room {}, user {})'.format(
                len(self.sdp), self.room_id, self.user_id))
            if e2ee and not self.e2ee:
                self.e2ee = True   
                log.debug('Publisher (room {}, user {}) is going to do end-to-end media encryption'.format(
                    self.room_id, self.user_id))         
        
        # print the media send or not
        if streams:
            for stream in streams:
                ps = self.streams_bymid.get(stream['mid'])
                active = stream.get('send')
                if ps is not None and active is not None:
                    if ps.type == 'audio':
                        log.debug('Setting audio property({}): {} (room {}, user {})'.format(
                            ps.mid, active, self.room_id, self.user_id))                    
                    elif ps.type == 'video':
                        log.debug('Setting video property({}): {} (room {}, user {})'.format(
                            ps.mid, active, self.room_id, self.user_id))  
                    elif ps.type == 'data':
                        log.debug('Setting data property({}): {} (room {}, user {})'.format(
                            ps.mid, active, self.room_id, self.user_id))      
        else:
            if (audio is not None) or (video is not None) or \
            (data is not None) or (mid and send is not None):
                for ps in self.streams:
                    mid_found = mid and (send is not None) and (mid == ps.mid)
                    if ps.type == 'audio' and ((audio is not None ) or mid_found):
                        active = send if mid_found else audio
                        log.debug('Setting audio property({}): {} (room {}, user {})'.format(
                            ps.mid, active, self.room_id, self.user_id))                    
                    elif ps.type == 'video' and ((video is not None ) or mid_found):
                        active = send if mid_found else video
                        log.debug('Setting video property({}): {} (room {}, user {})'.format(
                            ps.mid, active, self.room_id, self.user_id))  
                    elif ps.type == 'data' and ((data is not None ) or mid_found):
                        active = send if mid_found else data
                        log.debug('Setting data property({}): {} (room {}, user {})'.format(
                            ps.mid, active, self.room_id, self.user_id))  

        if audiocodec and jsep: 
            self.acodec = audiocodec
            log.debug('Setting audio codec property: {} (room {}, user {})'.format(
                self.acodec, self.room_id, self.user_id))            
        elif 'audio_codec' in reply_data:
            self.acodec = reply_data['audio_codec']
            log.debug('Setting audio codec property: {} (room {}, user {})'.format(
                self.acodec, self.room_id, self.user_id))
        if videocodec and jsep:
            self.vcodec = videocodec
            log.debug('Setting video codec property: {} (room {}, user {})'.format(
                self.vcodec, self.room_id, self.user_id))
        elif 'video_codec' in reply_data:
            self.vcodec = reply_data['video_codec']
            log.debug('Setting video codec property: {} (room {}, user {})'.format(
                self.vcodec, self.room_id, self.user_id))
        if jsep:
            sdp = jsep.get('sdp', '')
            if sdp:
                if self.room.audiolevel_ext and JANUS_RTP_EXTMAP_AUDIO_LEVEL in sdp:
                    for stream in self.streams:
                        if stream.type == 'audio':
                            stream.audiolevel_ext = True
                else:
                    for stream in self.streams:
                        if stream.type == 'audio':
                            stream.audiolevel_ext = False

        if bitrate >= 0:
            log.debug('Setting video bitrate: {} (room {}, user {})'.format(
                bitrate, self.room_id, self.user_id))            
        if audio_active_packets:
            self.user_audio_active_packets = audio_active_packets
            log.debug('Setting user audio_active_packet: {} (room {}, user {})'.format(
                audio_active_packets, self.room_id, self.user_id))
        if audio_level_average:
            self.user_audio_level_average = audio_level_average
            log.debug('Setting user audio_level_average: {} (room {}, user {})'.format(
                audio_level_average, self.room_id, self.user_id))

        if record is not None and not record_locked:
            log.debug('Setting record property: {} (room {}, user {})'.format(
                record, self.room_id, self.user_id))
        if filename and not record_locked:
            log.debug('Setting recording basename: {} (room {}, user {})'.format(
                filename, self.room_id, self.user_id))

        self._record_active = body.get('record', self._record_active)

        if display and display != self.display:
            self.display = display
            display_event = {
                'videoroom': 'event',
                'id': self.user_id,
                'display': self.display
            }
            if self.room:
                self.room.notify_other_participants(self, display_event)

        if descriptions and (jsep is None or jsep.get('sdp') is None):
            # We only do this here if this is an SDP-less configure: in case
            # a sdp negotiation is involved, descriptions are updated later */
            desc_updated = False
            for d in descriptions:
                d_mid = d.get('mid', '')
                d_desc = d.get('description', '') 
                ps = self.streams_bymid.get(mid)
                if ps and d_desc and ps.description != d_desc:
                    desc_updated = True
                    ps.description = d_desc

            if desc_updated:
                # If at least a description changed, n
                # otify everyone else about the publisher details
                if self.room:
                    self.room.notify_about_publisher(self)

        return reply_jsep

    def unpublish(self):
        self._assert_valid()
        if self._backend_handle is None:
            raise JanusCloudError('Backend handle invalid for publisher {}({})'.format(self.user_id, self.display),
                                  JANUS_VIDEOROOM_ERROR_INVALID_ELEMENT)
        if self.sdp == '' or not self.webrtc_started:
            raise JanusCloudError('Can\'t unpublish, not published',
                                  JANUS_VIDEOROOM_ERROR_NOT_PUBLISHED)

        _send_backend_message(self._backend_handle, {
            'request': 'unpublish',
        })
    
    def rtp_forward(self, host, **kwargs):

        self._assert_valid()
        if self._backend_handle is None:
            raise JanusCloudError('Backend handle invalid for publisher {}({})'.format(self.user_id, self.display),
                                  JANUS_VIDEOROOM_ERROR_INVALID_ELEMENT)

        # send request to backend
        body = {
            'request': 'rtp_forward',
            'room': self._backend_room.backend_room_id,
            'publisher_id': self.user_id,
            'host': host
        }
        if self._backend_room.backend_admin_key:
            body['admin_key'] = self._backend_room.backend_admin_key
        if len(kwargs) > 0:
            for k, v in kwargs.items():
                if k not in body:
                    body[k] = v

        reply_data, reply_jsep = _send_backend_message(self._backend_handle, body=body)
        if 'forwarders' in reply_data:
            forwarders = reply_data.get('forwarders', [])
            for forwarder in forwarders:
                stream_id = forwarder.get('stream_id', 0)
                if stream_id:
                    self._rtp_forwarders[stream_id] = forwarder
                    forwarder['local_host'] = urlparse(self._backend_room.server_url).hostname
                    
            return forwarders
        elif 'rtp_stream' in reply_data:
            #  Old deprecated approach 
            rtp_stream = reply_data.get('rtp_stream', {})

            # get the new stream id
            new_rtp_stream_ids = set()
            for key, value in rtp_stream.items():
                if key == 'audio_stream_id' or key == 'video_stream_id' or key == 'video_stream_id_2' or \
                key == 'video_stream_id_3' or key == 'data_stream_id':
                    new_rtp_stream_ids.add(value)

            # get the new forwarder info
            reply_data, reply_jsep = _send_backend_message(self._backend_handle, body={
                'request': 'listforwarders',
                'room': self._backend_room.backend_room_id,
            })
            forwarders = []
            for publisher in reply_data.get('publishers', []):
                if publisher.get('publisher_id', 0) == self.user_id:
                    forwarders = publisher.get('forwarders', [])
                    break
            for forwarder in forwarders:
                stream_id = forwarder.get('stream_id', 0)
                if stream_id in new_rtp_stream_ids:
                    # this backend_rtp_forwarder is the new added one
                    self._rtp_forwarders[stream_id] = forwarder
                    # add local_rtcp_host key                
                    forwarder['local_host'] = urlparse(self._backend_room.server_url).hostname

            return rtp_stream
        

    def stop_rtp_forward(self, stream_id):
        self._assert_valid()
        if self._backend_handle is None:
            raise JanusCloudError('Backend handle invalid for publisher {}({})'.format(self.user_id, self.display),
                                  JANUS_VIDEOROOM_ERROR_INVALID_ELEMENT)

        if stream_id not in self._rtp_forwarders:
            raise JanusCloudError('No such stream ({})'.format(stream_id),
                                  JANUS_VIDEOROOM_ERROR_NO_SUCH_FEED)

        # send request to backend
        body = {
            'request': 'stop_rtp_forward',
            'room': self._backend_room.backend_room_id,
            'publisher_id': self.user_id,
            'stream_id': stream_id
        }
        if self._backend_room.backend_admin_key:
            body['admin_key'] = self._backend_room.backend_admin_key

        _send_backend_message(self._backend_handle, body=body)

        self._rtp_forwarders.pop(stream_id, None)

    def rtp_forwarder_list(self):
        return list(self._rtp_forwarders.values())

    def add_subscriber(self, subscriber):
        self._subscribers.add(subscriber)

    def del_subscriber(self, subscriber):
        self._subscribers.discard(subscriber)
    
    def subscriber_num(self):
        return len(self._subscribers)

    def add_subscription(self, subscriber):
        self._subscriptions.add(subscriber)

    def del_subscription(self, subscriber):
        self._subscriptions.discard(subscriber)

    def kick_all_subscriptions(self):
        subscriptions = self._subscriptions.copy()
        for subscriber in subscriptions:
            subscriber.kick()

    def async_enable_recording(self, record):
        self._assert_valid()
        if self._backend_handle is None:
            raise JanusCloudError('Backend handle invalid for publisher {}({})'.format(self.user_id, self.display),
                                  JANUS_VIDEOROOM_ERROR_INVALID_ELEMENT)

        log.debug('Async Enable recording: {} for user {} ({}) of room {}'.
                  format(record, self.user_id, self.display, self.room_id))

        # send request to backend
        body = {
            'request': 'configure',
            'record': record
        }
        self._backend_handle.async_send_message(body)

        self._record_active = record

    def moderate(self, mid: str, mute: bool):
        self._assert_valid()
        if self._backend_handle is None:
            raise JanusCloudError('Backend handle invalid for publisher {}({})'.format(self.user_id, self.display),
                                  JANUS_VIDEOROOM_ERROR_INVALID_ELEMENT)

        if mid not in self.streams_bymid:
            raise JanusCloudError('No such stream {}'.format(mid),
                                  JANUS_VIDEOROOM_ERROR_NO_SUCH_FEED)  

    
        # send request to backend
        body = {
            'request': 'moderate',
            'room': self._backend_room.backend_room_id,
            'id': self.user_id,
            'mid': mid,
            'mute': mute
        }

        _send_backend_message(self._backend_handle, body=body)

    def push_videoroom_event(self, data):
        if self._has_destroyed:
            return
        if self._frontend_handle:
            self._frontend_handle.push_plugin_event(data=data)

    def hangup(self):
        if self._has_destroyed:
            return
        if self._backend_handle and self.webrtc_started:
            self._backend_handle.send_hangup()

    def trickle(self, candidate=None, candidates=None):
        if self._has_destroyed:
            return
        if self._backend_handle:
            self._backend_handle.send_trickle(candidate=candidate, candidates=candidates)

    def on_async_event(self, handle, event_msg):
        if self._has_destroyed:
            return
        if self._backend_handle is None:
            return       # not finish backend

        if event_msg['janus'] == 'event':
            data = event_msg['plugindata']['data']
            jsep = event_msg.get('jsep')
            op = data.get('videoroom', '')

            if op == 'slow_link':
                if self._frontend_handle:
                    self._frontend_handle.push_plugin_event(data, jsep)
            elif op == 'talking' or op =='stopped-talking':

                id = data.get('id', 0)
                if id != self.user_id:
                    # it's other publisher's talking/stopped-talking event
                    # ignore
                    return
                
                mindex = data.get('mindex')
                if mindex is None:
                    # invalid, ignore
                    return
                if mindex >= len(self.streams):
                    # invalid, ignore
                    return
                ps = self.streams[mindex]
                if op == 'talking':
                    ps.talking = True
                else:
                    ps.talking = False
                    
                if self.room is not None and self.room.audiolevel_event:
                    talk_event = data.copy()
                    talk_event['id'] = self.user_id
                    talk_event['room'] = self.room_id
                    if self._frontend_handle:
                        self._frontend_handle.push_plugin_event(talk_event)
                    if self.room:
                        self.room.notify_other_participants(self, talk_event)
            elif op == 'event':
                if ('moderation' in data) :

                    id = data.get('id', 0)
                    if id != self.user_id:
                        # it's other publisher's moderation event
                        # ignore
                        return                    
                    # update self property
                    ps = self.streams_bymid.get(data.get('mid', ''))
                    if ps is None:
                        # invalid mid, ignore
                        return
                    moderation = data.get('moderation')
                    if moderation:
                        if moderation == 'muted':
                            ps.moderated = True
                        elif moderation == 'unmuted':
                            ps.moderated = False
                        else:
                            # invalid string, ignore
                            return
                    moderation_event = data.copy()
                    moderation_event['id'] = self.user_id
                    moderation_event['room'] = self.room_id
                    if self._frontend_handle:
                        self._frontend_handle.push_plugin_event(moderation_event)
                    if self.room:
                        self.room.notify_other_participants(self, moderation_event)
                else:
                    # ignore other event
                    return
            else:
                # ignore other operations
                return
        else:
            if event_msg['janus'] == 'webrtcup':
                # webrtc pc is up
                self.webrtc_started = True
                log.info('Pulisher (id:{}, display:{}) PeerConnection startup'.format(
                        self.user_id, self.display))

                if self.room:
                    self.room.notify_about_publisher(self)

            elif event_msg['janus'] == 'hangup':
                # webrtc pc is closed
                self.webrtc_started = False

                self.streams.clear()
                self.streams_bymid.clear()
                self.data_stream = None

                self.acodec = ''
                self.vcodec = ''
                self.sdp = ''
                self.user_audio_active_packets = 0
                self.user_audio_level_average = 0

                log.info('Pulisher (id:{}, display:{}) PeerConnection hangup'.format(
                        self.user_id, self.display))

                # notify other participant unpublished
                unpub_event = {
                    'videoroom': 'event',
                    'room': self.room_id,
                    'unpublished': self.user_id
                }
                if self.room:
                    self.room.notify_other_participants(self, unpub_event)

                # hangup/remove all subscribers
                if len(self._subscribers) > 0:
                    subscribers = self._subscribers.copy()
                    self._subscribers.clear()
                    for subscriber in subscribers:
                        subscriber.on_feed_hangup(self)

            params = dict()
            for key, value in event_msg.items():
                if key not in ['janus', 'session_id', 'sender', 'opaque_id', 'transaction']:
                    params[key] = value
            if self._frontend_handle:
                self._frontend_handle.push_event(event_msg['janus'], None, **params)

    def on_close(self, handle):
        if self._has_destroyed:
            return

        self._backend_handle = None #detach with backend handle
     
        if self.room:
            # simulate kick this publisher
            kick_event = {
                'videoroom': 'event',
                'room': self.room_id,
                'leaving': 'ok',
                'reason': 'kicked',
            }
            self.push_videoroom_event(kick_event)

        self.destroy()

class BackendRoom(object):

    def __init__(self, backend_server, backend_room_id, backend_admin_key=''):
        self.backend_room_id = backend_room_id
        self.server_name = backend_server.name
        self.server_url = backend_server.url
        self.backend_admin_key = backend_admin_key
        self._backend_publishers = set()
        self._has_destroyed = False
        self._has_activated = False

        # the edit params cache
        self._edit_cache = {}

        log.info('Backend room "{}"({}) is created'.format(
            self.backend_room_id, 
            self.server_url))             
        
    def __str__(self):
        return 'Backend Room "{0}"({1})'.format(self.backend_room_id, self.server_url)
    
    def destroy(self):
        if self._has_destroyed:
            return
        self._has_destroyed = True

        self._has_activated = False

        self._backend_publishers.clear()

        backend_handle = _backend_handle_manager.get(self.server_name)

        if backend_handle is None: 
            # this backend room is inactive
            log.debug('Backend room "{}"({}) is destroyed without backend sync'.format(
                self.backend_room_id, 
                self.server_url))             
            return

        try:
            # 1 destroy the room of the backend server
            if backend_handle:
                # 2. async destroy backend room
                backend_handle.async_send_message({
                    'request': 'destroy',
                    'room': self.backend_room_id
                })
        except Exception as e:
            log.exception('Backend room "{}"({}) failed to destroyed: {}, ignore'.
                    format(self.backend_room_id, self.server_url, str(e)))             
            pass   # ignore destroy failed

        
        log.info('Backend room "{}"({}) is destroyed'.
                    format(self.backend_room_id, self.server_url)) 
                    
    def activate(self, room):
        if self._has_destroyed:
            raise JanusCloudError('No such backend room "{}" ({})'.format(
                self.backend_room_id, self.server_url),
                JANUS_VIDEOROOM_ERROR_NO_SUCH_ROOM)
        
        # fast path, no need to talk to backend server
        if _backend_handle_manager.exists(self.server_name) and \
            self._backend_publishers and \
            not self._edit_cache:
            # There are stll some publishers in this backend room,
            # not needed to check
            return
        
        # slow (normal) path
        backend_handle = _backend_handle_manager.create(self.server_name, self.server_url)
        if backend_handle is None:
            raise JanusCloudError('backend room "{}" ({}) failed to activate: backend handle creates failed'.format(
                self.backend_room_id, self.server_url),
                JANUS_VIDEOROOM_ERROR_NO_SUCH_ROOM)            

        if self._has_activated:
            # already activated before
            if self._check_exist(backend_handle):
                # if cached, update the room
                if self._edit_cache:
                    # update
                    body = {
                        'request': 'edit',
                        'room': self._backend_room_id,
                    }
                    body.update(self._edit_cache)
                    self._edit_cache.clear()

                    _send_backend_message(backend_handle, body=body)                  
            else:

                self._backend_publishers.clear() # surely no publishers for new backend room

                # re-create it if non-exist
                try:
                    self._create_backend(backend_handle, room)

                except Exception as e:
                    if e.code == JANUS_VIDEOROOM_ERROR_ROOM_EXISTS:
                        pass # the room already exist, continue
                    else:
                        raise                

                log.info('Backend room "{}"({}) is reactivated on the backend'.format(
                            self.backend_room_id, 
                            self.server_url)) 
        else: 
            try:
                self._create_backend(backend_handle, room)
            except Exception as e:
                if e.code == JANUS_VIDEOROOM_ERROR_ROOM_EXISTS:
                    pass # the room already exist, continue
                else:
                    raise  

            self._has_activated = True

            log.info('Backend room "{}"({}) is activated on the backend'.format(
                        self.backend_room_id, 
                        self.server_url)) 
 
    def edit(self, new_bitrate=None, new_rec_dir=None):
        if self._has_destroyed:
            return # no need to edit

        if new_bitrate is None and new_rec_dir is None:
            return # no need to edit

        backend_handle = _backend_handle_manager.get(self.server_name)

        if backend_handle is None: 
            # inactive, cache the new params for next activation
            if new_bitrate is not None:
                self._edit_cache['new_bitrate'] = new_bitrate

            if new_rec_dir is not None:
                self._edit_cache['new_rec_dir'] = new_rec_dir
            
            log.debug('Backend room "{}"({}) is postpone to edited because of inactive'.format(
                self.backend_room_id, 
                self.server_url)) 
            return 

        # the backend room is active by now, edit it
        body = {
            'request': 'edit',
            'room': self._backend_room_id,
        }
        if new_bitrate is not None:
            body['new_bitrate'] = new_bitrate

        if new_rec_dir is not None:
            body['new_rec_dir'] = new_rec_dir

        try:
            _send_backend_message(backend_handle, body=body)

            log.debug('Backend room "{}"({}) is edited'.format(
                    self.backend_room_id, 
                    self.server_url)) 

        except Exception as e:
            if e.code == JANUS_VIDEOROOM_ERROR_ROOM_EXISTS:
                # backend room does not exist by now, it will be created with new params for next activation
                log.debug('Backend room "{}"({}) is postpone to edited because of nonexist'.format(
                    self.backend_room_id, 
                    self.server_url)) 
                pass
            else:
                raise  

    def add_publisher(self, user_id):
        self._backend_publishers.add(user_id)

    def del_publisher(self, user_id):
        self._backend_publishers.discard(user_id)

    def _check_exist(self, backend_handle):
            # 3. check exist on backend server
            reply_data, reply_jsep =  _send_backend_message(backend_handle, {
                'request': 'exists',
                'room': self.backend_room_id
            })
            return reply_data.get('exists', False)        

    def _create_backend(self, backend_handle, room):
        body = {
            'request':  'create',
            'room': self.backend_room_id,
            'description': 'januscloud-{}'.format(room.room_id),
            'permanent': False,
            'is_private': False,
            'require_pvtid': False,
            'bitrate': room.bitrate,
            'fir_freq': room.fir_freq,
            'audiocodec': ','.join(room.audiocodec),
            'videocodec': ','.join(room.videocodec),
            'opus_fec': room.opus_fec,
            'opus_dtx': room.opus_dtx,                
            'video_svc': room.video_svc,
            'audiolevel_ext': room.audiolevel_ext,
            'audiolevel_event': room.audiolevel_event,
            'audio_active_packets': room.audio_active_packets,
            'audio_level_average': room.audio_level_average,
            'videoorient_ext': room.videoorient_ext,
            'playoutdelay_ext': room.playoutdelay_ext,
            'transport_wide_cc_ext': room.transport_wide_cc_ext,
            'record': False, # backend room always disable room record
            'rec_dir': room.rec_dir,
            'notify_joining': False,
            'require_e2ee': room.require_e2ee,
        }
        if room.description:
            body['description'] = 'januscloud-{}'.format(room.description)
        if self.backend_admin_key:
            body['admin_key'] = self.backend_admin_key
        if room.h264_profile:
            body['h264_profile'] = room.h264_profile
        if room.vp9_profile:
            body['vp9_profile'] = room.vp9_profile

        _send_backend_message(backend_handle, body)

        # created successfully, no need to cache params again
        self._edit_cache.clear()

class VideoRoom(object):

    def __init__(self, room_id, backend_admin_key='', 
                 description='', secret='', pin='',
                 is_private=False, require_pvtid=False, publishers=3, bitrate=0,
                 bitrate_cap=False, fir_freq=0, audiocodec=['opus'], videocodec=['vp8'], opus_fec=False,
                 opus_dtx=False,
                 video_svc=False, audiolevel_ext=True, audiolevel_event=False, audio_active_packets=100,
                 audio_level_average=25, videoorient_ext=True, playoutdelay_ext=True,
                 transport_wide_cc_ext=False, record=False, rec_dir='', allowed=None,
                 notify_joining=False, lock_record=False, require_e2ee=False,
                 vp9_profile='', h264_profile='',
                 utime=None, ctime=None):

        # public property
        self.room_id = room_id                   # Unique room ID
        
        self.description = description           # Room description
        if self.description == '':
            self.description = 'Room {}'.format(room_id)
        self.secret = secret                     # Secret needed to manipulate (e.g., destroy) this room
        self.pin = pin                           # Password needed to join this room, if any
        self.is_private = is_private             # Whether this room is 'private' (as in hidden) or not
        self.require_pvtid = require_pvtid       # Whether subscriptions in this room require a private_id
        self.publishers = publishers             # Maximum number of concurrent publishers, 0 means no limited
        self.bitrate = bitrate                   # Global bitrate limit
        if 0 < self.bitrate < 64000:
            self.bitrate = 64000  # Don't go below 64k
        self.bitrate_cap = bitrate_cap           # Whether the above limit is insormountable
        self.fir_freq = fir_freq                 # Regular FIR frequency (0=disabled)

        self.audiocodec = audiocodec[:5]         # Audio codec(s) to force on publishers, max 5 codec
        self.videocodec = videocodec[:5]         # Video codec(s) to force on publishers
        self.opus_fec = opus_fec                 # Whether inband FEC must be negotiated
                                                 # (note: only available for Opus)
        self.opus_dtx = opus_dtx                 # Whether DTX must be negotiated 
                                                 # (note: only available for Opus)                                                 
                                                 
        if self.opus_fec and 'opus' not in self.audiocodec:
            self.opus_fec = False
            log.warning('Inband FEC is only supported for rooms that allow Opus: disabling it...')
        
        if self.opus_dtx and 'opus' not in self.audiocodec:
            self.opus_dtx = False
            log.warning('DTX is only supported for rooms that allow Opus: disabling it...')

        self.video_svc = False
        if video_svc:                         # Whether SVC must be done for video
            if self.videocodec == ['vp9']:
                self.video_svc = True
            else:
                log.warning('SVC is only supported, in an experimental way, for VP9 only rooms: disabling it...')
                                                 # (note: only available for VP9 right now)

        self.audiolevel_ext = audiolevel_ext     # Whether the ssrc-audio-level extension must
                                                 # be negotiated or not for new publishers
        self.audiolevel_event = audiolevel_event            # Whether to emit event to other users about audiolevel
        self.audio_active_packets = audio_active_packets    # Amount of packets with audio level for checkup
        self.audio_level_average = audio_level_average      # Average audio level
        self.videoorient_ext = videoorient_ext              # Whether the video-orientation extension must be
                                                            # negotiated or not for new publishers
        self.playoutdelay_ext = playoutdelay_ext            # Whether the playout-delay extension must be negotiated
                                                            # or not for new publishers
        self.transport_wide_cc_ext = transport_wide_cc_ext  # Whether the transport wide cc extension must be
                                                            # negotiated or not for new publishers
        self.record = record                     # Whether the feeds from publishers in this room should be recorded
        self.rec_dir = rec_dir                   # Where to save the recordings of this room, if enabled
        self.check_allowed = False               # Whether to check tokens when participants join, default is False
        if allowed is None:
            self.allowed = set()                 # Map of participants (as tokens) allowed to join
        else:
            self.allowed = set(allowed)
            self.check_allowed = True       # if allowed is given in params, enable this room check allow by default
        self.notify_joining = notify_joining     # Whether an event is sent to notify all participants if a new
                                                 # participant joins the room
        self.lock_record = lock_record           # Whether recording state can only be changed providing the room secret

        if 'h264' in self.videocodec:
            self.h264_profile = h264_profile         # H.264 codec profile to prefer, if more are negotiated
        else:
            self.h264_profile = ''

        if 'vp9' in self.videocodec:
            self.vp9_profile = vp9_profile           # VP9 codec profile to prefer, if more are negotiated
        else:
            self.vp9_profile = ''

        self.require_e2ee = require_e2ee         # Whether end-to-end encrypted publishers are required

        #internal property
        self._participants = {}                  # Map of potential publishers (we get subscribers from them)
        self._private_id = {}                    # Map of existing private IDs
        self._creating_user_id = set()           # user_id which are creating

        self._has_destroyed = False

        self._backend_rooms = {}                 # Map of backend rooms for janus-gateway
        self._backend_admin_key = backend_admin_key

        self.idle_ts = get_monotonic_time()

        self._backend_room_id = random_uint64()

        if utime is None:
            self.utime = time.time()
        else:
            self.utime = utime

        if ctime is None:
            self.ctime = time.time()
        else:
            self.ctime = ctime

    def __str__(self):
        return 'Video Room-"{0}"({1})'.format(self.room_id, self.description)

    def _assert_valid(self):
        if self._has_destroyed:
            raise JanusCloudError('No such room ({})'.format(self.room_id),
                                  JANUS_VIDEOROOM_ERROR_NO_SUCH_ROOM)

    def check_idle(self):
        if len(self._participants) == 0:
            if self.idle_ts == 0:
                self.idle_ts = get_monotonic_time()
            return True
        else:
            self.idle_ts = 0
            return False

    def destroy(self):
        if self._has_destroyed:
            return
        self._has_destroyed = True

        participants = list(self._participants.values())
        backend_rooms = list(self._backend_rooms.values())

        self._participants.clear()
        self._private_id.clear()
        self._creating_user_id.clear()
        self._backend_rooms.clear()

        # Notify all participants that the fun is over, and that they'll be kicked
        log.debug("Room {} is destroyed, Notifying all participants".format(
            self.room_id)
        )
        destroyed_event = {
            'videoroom': 'destroyed',
            'room': self.room_id,
        }
        # log.debug("after clear, len of participants is {}".format(len(participants)))
        for publisher in participants:
            publisher.room = None    # already removed from room, no need to call back room's on_participant destroy()
            publisher.room_id = 0

            # log.debug('destroy publisher user_id {}'.format(publisher.user_id))
            publisher.push_videoroom_event(destroyed_event)

            # according to janus-gateway,  don't destory the publisherswhen room is destroyed
            # publisher.destroy() 

        log.debug("all backend rooms of Room {} are destroyed".format(
            self.room_id)
        )
        for backend_room in backend_rooms:
            backend_room.destroy()

        log.info("Room {} is destroyed".format(self.room_id))

    def update(self):
        self.utime = time.time()


    def edit(self, new_description=None, new_secret=None, new_pin=None, new_is_private=None,
               new_require_pvtid=None, new_bitrate=None, new_publishers=None,
               new_lock_record=None, new_rec_dir=None):


        need_update = False
        if new_description is not None and len(new_description) > 0:
            self.description = new_description
        if new_secret is not None:
            self.secret = new_secret
        if new_pin is not None:
            self.pin = new_pin
        if new_is_private is not None:
            self.is_private = new_is_private
        if new_require_pvtid is not None:
            self.require_pvtid = new_require_pvtid
        if new_bitrate is not None:
            self.bitrate = new_bitrate
            if 0 < self.bitrate < 64000:
                self.bitrate = 64000    # Don't go below 64k
            need_update = True
        if new_publishers is not None:
            self.publishers = new_publishers
        if new_lock_record is not None:
            self.lock_record = new_lock_record
        if new_rec_dir is not None:
            self.rec_dir = new_rec_dir
            need_update = True

        self.update()

        if need_update:
            backend_rooms = list(self._backend_rooms.values())
            greenlets = []
            for backend_room in backend_rooms:
                greenlet = gevent.spawn(
                    backend_room.edit, 
                    new_bitrate=new_bitrate, 
                    new_rec_dir=new_rec_dir
                )
                greenlets.append(greenlet)
            # executes concurrently and wait for all finished
            gevent.joinall(greenlets, timeout=30)

            # check results
            for index, greenlet in enumerate(greenlets):
                if not greenlet.successful():
                    backend_room = backend_rooms[index]
                    e = greenlet.exception if greenlet.exception is not None else 'Timeout'
                    log.warning('Exception when edit {} of room {} : {}, ignore it'.format(
                        backend_room, self.room_id, e))
        
    def activate_backend_room(self, backend_server):
        backend_room = self._backend_rooms.get(backend_server.name)
        if backend_room is None:
            backend_room = BackendRoom(
                backend_server=backend_server, 
                backend_room_id=self._backend_room_id,
                backend_admin_key=self._backend_admin_key)
            self._backend_rooms[backend_server.name] = backend_room
            
        backend_room.activate(self)

        return backend_room



    def new_participant(self, user_id, handle, display=''):
        if handle is None:
            raise JanusCloudError('handle invalid', JANUS_VIDEOROOM_ERROR_INVALID_REQUEST)
        self._assert_valid()

        # choose backend server
        backend_server = handle.choose_server()
        if backend_server is None:
            raise JanusCloudError('No backend server available', JANUS_ERROR_BAD_GATEWAY)


        # get id
        if user_id == 0:
            user_id = random_uint64()
            while user_id in self._participants or user_id in self._creating_user_id:
                user_id = random_uint64()
        else:
            if user_id in self._participants or user_id in self._creating_user_id:
                raise JanusCloudError('User ID {} already exists'.format(user_id),
                                      JANUS_VIDEOROOM_ERROR_ID_EXISTS)

        log.debug('  -- Publisher ID: {}'.format(user_id))


        new_publisher = VideoRoomPublisher(user_id=user_id, handle=handle,
                                       display=display)
        log.info('A new pulisher (id:{}, display:{}) is created on handle {}'.format(
            new_publisher.user_id, new_publisher.display, handle.handle_id))

        try:
            self._creating_user_id.add(user_id)

            # activate backend room
            backend_room = self.activate_backend_room(backend_server) 

            # join the room
            new_publisher.join(room=self, backend_room=backend_room)

        except Exception:
            new_publisher.room = None
            new_publisher.room_id = 0    
            self._creating_user_id.discard(user_id)       
            new_publisher.destroy()
            raise

        # get pvt id
        new_publisher.pvt_id = random_uint32()
        while new_publisher.pvt_id in self._private_id:
            new_publisher.pvt_id = random_uint32()

        # add to the room
        self._participants[user_id] = new_publisher
        self._private_id[new_publisher.pvt_id] = new_publisher
        self._creating_user_id.discard(user_id)

        self.check_idle()

        

        # notify other new participant join
        if self.notify_joining:
            user = {
                'id': new_publisher.user_id
            }
            if new_publisher.display:
                user['display'] = new_publisher.display
            event = {
                'videoroom': 'event',
                'room': self.room_id,
                'joining': user
            }
            self.notify_other_participants(new_publisher, event)    


        return new_publisher

    def new_subscriber(self, handle, pvt_id=0, **join_params):

        if handle is None:
            raise JanusCloudError('handle invalid', JANUS_VIDEOROOM_ERROR_INVALID_REQUEST)

        self._assert_valid()

        owner = None
        if self.require_pvtid:
            owner = self._private_id.get(pvt_id)
            if owner is None:
                raise JanusCloudError('Unauthorized (this room requires a valid private_id)',
                                      JANUS_VIDEOROOM_ERROR_UNAUTHORIZED)

        new_subscriber = VideoRoomSubscriber(handle=handle, owner=owner)
        if owner:
            owner.add_subscription(new_subscriber)
        try:
            # join the room
            jsep = new_subscriber.join(room=self, **join_params)

        except Exception:
            new_subscriber.destroy()
            raise        

        return new_subscriber, jsep

    def get_participant_by_pvt_id(self, pvt_id):
        return self._private_id.get(pvt_id)

    def get_participant_by_user_id(self, user_id):
        return self._participants.get(user_id)

    def get_backend_room_id(self):
        return self._backend_room_id

    def get_backend_admin_key(self):
        return self._backend_admin_key

    def pvt_id_exists(self, pvt_id):
        return pvt_id in self._private_id

    def user_id_exists(self, user_id):
        return user_id in self._participants

    def list_participants(self):
        return list(self._participants.values())

    def num_participants(self):
        return len(self._participants)

    def kick_participant(self, participant_id):
        self._assert_valid()
        publisher = self._participants.get(participant_id, None)
        if publisher is None:
            raise JanusCloudError('No such user {} in room {}'.format(participant_id, self.room_id),
                                  JANUS_VIDEOROOM_ERROR_ID_EXISTS)

        # remove from room
        self._participants.pop(participant_id, None)
        self._private_id.pop(publisher.pvt_id, None)
        publisher.room = None
        publisher.room_id = 0

        # notify publisher kick
        kick_event = {
            'videoroom': 'event',
            'room': self.room_id,
            'leaving': 'ok',
            'reason': 'kicked',
        }
        publisher.push_videoroom_event(kick_event)

        # notify others
        event = {
            'videoroom': 'event',
            'room': self.room_id,
            'kicked': participant_id
        }
        self.notify_other_participants(publisher, event)

        publisher.kick_all_subscriptions()
        publisher.destroy()

        self.check_idle()

    def on_participant_destroy(self, participant_id):
        publisher = self._participants.pop(participant_id, None)
        if publisher is None:
            return  # already removed
        self._private_id.pop(publisher.pvt_id, None)

        event = {
            'videoroom': 'event',
            'room': self.room_id,
            'leaving': participant_id
        }
        self.notify_other_participants(publisher, event)

        self.check_idle()

    def notify_about_publisher(self, publisher):

        pl = {'id': publisher.user_id}
        if publisher.display:
            pl['display'] = publisher.display
        audio_added = False
        video_added = False
        media = []
        for stream in publisher.streams:
            info = {
                'type': stream.type,
                'mindex': stream.mindex,
                'mid': stream.mid
            }
            if stream.disabled:
                info['disabled'] = True
            else:
                if stream.description:
                    info['description'] = stream.description
                if stream.moderated:
                    info['moderated'] = True

                if stream.type == 'audio':
                    info['codec'] = stream.codec

                    if not audio_added:
                        audio_added = True
                        pl['audio_codec'] = stream.codec

                    if stream.codec == 'opus':
                        if stream.fec:
                            info['fec'] = True
                        if stream.dtx:
                            info['dtx'] = True
                        if stream.stereo:
                            info['stereo'] = True
                    
                elif stream.type == 'video':
                    info['codec'] = stream.codec
                    if not video_added:
                        video_added = True
                        pl['video_codec'] = stream.codec
                    if stream.codec == 'h264' and stream.h264_profile:
                        info['h264_profile'] = stream.h264_profile
                    if stream.codec == 'vp9' and stream.vp9_profile:
                        info['vp9_profile'] = stream.vp9_profile                    
                    if stream.simulcast:
                        info['simulcast'] = True
                    if stream.svc:
                        info['svc'] = True
            media.append(info)
        pl['streams'] = media

        event = {
            'videoroom': 'event',
            'room': self.room_id,
            'publishers':[pl]
        }
        self.notify_other_participants(publisher, event)


    def notify_other_participants(self, src_participant, event):
        
        if self._has_destroyed: # if destroyed, just return
            return

        participant_list = list(self._participants.values())
        for publisher in participant_list:
            if publisher != src_participant:
                try:
                    publisher.push_videoroom_event(event)
                except Exception as e:
                    log.warning('Notify publisher {} ({}) of room {} Failed:{}'.format(
                        publisher.user_id, publisher.display, self.room_id, e))
                    pass     # ignore errors during push event to each publisher

    def enable_allowed(self):
        log.debug('Enabling the check on allowed authorization tokens for room {}'.format(self.room_id))
        self.check_allowed = True
        self.update()

    def disable_allowed(self):
        log.debug('Disabling the check on allowed authorization tokens for room {}'.format(self.room_id))
        self.check_allowed = False
        self.update()

    def add_allowed(self, allowed=[]):
        self.allowed.update(allowed)
        self.update()

    def remove_allowed(self, allowed=[]):
        self.allowed.difference_update(allowed)
        self.update()

    def enable_recording(self, record):
        if self.record != record:   # record state changed
            self.record = record

            # async enable recording of the exist participants concurrently
            participant_list = list(self._participants.values())
            for publisher in participant_list:
                try:
                    publisher.async_enable_recording(record)
                except Exception as e:
                    log.warning('Exception when enable recording for publisher {} ({}) of room {} : {}, ignore it'.format(
                        publisher.user_id, publisher.display, self.room_id, e))
                    pass     # ignore errors during enable recording for each participant

    def check_modify(self, secret):
        if self.secret and self.secret != secret:
            raise JanusCloudError('Unauthorized (wrong {})'.format('secret'),
                                  JANUS_VIDEOROOM_ERROR_UNAUTHORIZED)
        return self

    def check_join(self, pin):
        if self.pin and self.pin != pin:
            raise JanusCloudError('Unauthorized (wrong {})'.format('pin'),
                                  JANUS_VIDEOROOM_ERROR_UNAUTHORIZED)
        return self

    def check_token(self, token):
        if self.check_allowed and token not in self.allowed:
            raise JanusCloudError('Unauthorized (not in the allowed list)',
                                  JANUS_VIDEOROOM_ERROR_UNAUTHORIZED)
        return self

    def check_max_publishers(self):
        count = 0
        for publisher in self._participants.values():
            if publisher.sdp:
                count += 1
        if count >= self.publishers:
            raise JanusCloudError('Maximum number of publishers ({}) already reached'.format(self.publishers),
                                  JANUS_VIDEOROOM_ERROR_PUBLISHERS_FULL)


class VideoRoomManager(object):

    def __init__(self, room_db='', room_dao=None, auto_cleanup_sec=0, admin_key=''):
        self._rooms_map = {}
        self._public_rooms_list = []
        self._admin_key = admin_key
        self._room_dao = room_dao
        self._room_db = room_db
        self._auto_cleanup_sec = auto_cleanup_sec
        if 0 < self._auto_cleanup_sec < 60:
            self._auto_cleanup_sec = 60    # above 60 secs
        self._auto_cleanup_greenlet = None
        if self._room_dao is not None:
            self._load_from_dao()
        if auto_cleanup_sec:
            self._auto_cleanup_greenlet = gevent.spawn(self._room_auto_cleanup_routine)

    def __len__(self):
        return len(self._rooms_map)

    def create(self, room_id=0, permanent=False, admin_key='', room_params={}):
        if permanent and self._room_dao is None:
            raise JanusCloudError('permanent not support',
                                  JANUS_VIDEOROOM_ERROR_INVALID_REQUEST)
        if self._admin_key:
            if admin_key == '':
                raise JanusCloudError('Need admin key for creating room',
                                      JANUS_VIDEOROOM_ERROR_MISSING_ELEMENT)
            if admin_key != self._admin_key:
                raise JanusCloudError('Unauthorized (wrong {})'.format('admin_key'),
                                      JANUS_VIDEOROOM_ERROR_UNAUTHORIZED)

        if room_id == 0:
            room_id = random_uint64()
            while room_id in self._rooms_map:
                room_id = random_uint64()
        if room_id in self._rooms_map:
            raise JanusCloudError('Room {} already exists'.format(room_id),
                                  JANUS_VIDEOROOM_ERROR_ROOM_EXISTS)

        try:
            self._rooms_map[room_id] = None   # avoid re-allocate room_id
            new_room = VideoRoom(room_id=room_id, 
                                 backend_admin_key=self._admin_key,
                                 **room_params)
            self._rooms_map[room_id] = new_room
        except Exception as e:
            self._rooms_map.pop(room_id, None)
            raise
        if not new_room.is_private:
            self._public_rooms_list.append(new_room)

        # debug print the new room info
        log.info('Created videoroom: {0} ({1}, private: {2}, {3}/{4} codecs, secret: {5}, pin: {6}, pvtid:{7})'.format(
            new_room.room_id, new_room.description, new_room.is_private,
            new_room.audiocodec, new_room.videocodec,
            new_room.secret, new_room.pin, new_room.require_pvtid
        ))
        if new_room.record:
            log.info('  -- Room is going to be recorded in {}'.format(new_room.rec_dir))
        if new_room.require_e2ee:
            log.info('  -- All publishers MUST use end-to-end encryption')

        saved = False
        if permanent and self._room_dao is not None:
            try:
                self._room_dao.add(new_room)
                saved = True
            except Exception as e:
                log.warning('Fail to add room to DB: {}'.format(e))

        return new_room, saved

    def update(self, room_id, secret='', permanent=False,
               new_description=None, new_secret=None, new_pin=None, new_is_private=None,
               new_require_pvtid=None, new_bitrate=None, new_publishers=None,
               new_lock_record=None, new_rec_dir=None):
        if permanent and self._room_dao is None:
            raise JanusCloudError('permanent not support',
                                  JANUS_VIDEOROOM_ERROR_INVALID_REQUEST)

        room = self.get(room_id).check_modify(secret)

        room.edit(new_description=new_description, new_secret=new_secret, new_pin=new_pin, new_is_private=new_is_private,
               new_require_pvtid=new_require_pvtid, new_bitrate=new_bitrate, new_publishers=new_publishers,
               new_lock_record=new_lock_record, new_rec_dir=new_rec_dir)

        saved = False
        if permanent and self._room_dao is not None:
            try:
                self._room_dao.update(room)
                saved = True
            except Exception as e:
                log.warning('Fail to update room config to DB: {}'.format(e))

        return room, saved

    def get(self, room_id):
        room = self._rooms_map.get(room_id)
        if room is None:
            raise JanusCloudError('No such room ({})'.format(room_id),
                                  JANUS_VIDEOROOM_ERROR_NO_SUCH_ROOM)
        return room

    def exists(self, room_id):
        return room_id in self._rooms_map

    def destroy(self, room_id, secret='', permanent=False):
        if permanent and self._room_dao is None:
            raise JanusCloudError('permanent not support',
                                  JANUS_VIDEOROOM_ERROR_INVALID_REQUEST)
        room = self.get(room_id).check_modify(secret)

        self._rooms_map.pop(room_id, None)

        if room in self._public_rooms_list:
            self._public_rooms_list.remove(room)
        room.destroy()

        saved = False
        if permanent and self._room_dao is not None:
            try:
                self._room_dao.del_by_room_id(room_id)
                saved = True
            except Exception as e:
                log.warning('Fail to delete room from DB: {}'.format(e))

        return saved

    def list(self, admin_key='', offset=0, limit=100):
        room_list = self._public_rooms_list
        # check admin_key is correct, then list the private room
        if self._admin_key and admin_key:
            if admin_key != self._admin_key:
                raise JanusCloudError('Unauthorized (wrong {})'.format('admin_key'),
                                      JANUS_VIDEOROOM_ERROR_UNAUTHORIZED)
            else:
                room_list = list(self._rooms_map.values())

        return room_list[offset:(offset+limit)]

    def load_from_config(self, rooms_config=[]):
        for room_config in rooms_config:
            room_id = room_config.get('room_id')
            room_params = room_params_schema.validate(room_config)
            room = self._rooms_map.get(room_id)
            if room is None:
                self.create(room_id=room_id,
                            permanent=False,
                            room_params=room_params)
            else:
                for k, v in room_params.items():
                    if hasattr(room, k):
                        setattr(room, k, v)
                room.update()

    def _load_from_dao(self):
        if self._room_dao is None:
            return
        room_list = self._room_dao.get_list()
        for room in room_list:
            self._rooms_map[room.room_id] = room
            if not room.is_private:
                self._public_rooms_list.append(room)
        log.info('Video rooms are loaded from DB ({}) successfully, total {} rooms'.format(
            self._room_db,
            len(room_list)))

    def _room_auto_cleanup_routine(self):
        cleanup_rooms = []
        while True:
            if self._auto_cleanup_sec > 0:
                # room auto cleanup is enable
                now = get_monotonic_time()
                cleanup_rooms.clear()
                for room in self._rooms_map.values():
                    if room.check_idle() and now - room.idle_ts > self._auto_cleanup_sec:
                        cleanup_rooms.append(room)
                for room in cleanup_rooms:
                    self._rooms_map.pop(room.room_id, None)  # avoid future usage
                    if room in self._public_rooms_list:
                        self._public_rooms_list.remove(room)

                # kick out all timeout session
                for room in cleanup_rooms:
                    try:
                        log.info('Video room {} timeout for auto cleanup'.format(room.room_id))   
                        room.destroy()                                            
                    except Exception as e:
                        log.warning('Failed to destroy the empty room "{}": {}'.format(room.room_id, e))

                if self._room_dao is not None:
                    try:
                        self._room_dao.del_by_list(cleanup_rooms)
                    except Exception as e:
                        log.warning('Failed to delete the empty rooms from DB: {}'.format(e))

                cleanup_rooms.clear()
                delta_time = get_monotonic_time() - now
                if delta_time < ROOM_CLEANUP_CHECK_INTERVAL:
                    gevent.sleep(ROOM_CLEANUP_CHECK_INTERVAL - delta_time)
            else:
                # session timeout check is disable, just None loop
                gevent.sleep(ROOM_CLEANUP_CHECK_INTERVAL)

class VideoRoomHandle(FrontendHandleBase):

    def __init__(self, handle_id, session, plugin, opaque_id=None, *args, **kwargs):
        super().__init__(handle_id, session, plugin, opaque_id, *args, **kwargs)

        self._pending_candidates = []

        self._room_mgr = plugin.room_mgr

        # self.webrtc_started = False

        self.participant_type = JANUS_VIDEOROOM_P_TYPE_NONE
        self.participant = None

        if self._plugin:
            self._plugin.handles.add(handle_id)

    def detach(self):
        if self._has_destroy:
            return
        super().detach()

        if self._plugin:
            self._plugin.handles.discard(self.handle_id)

        if self.participant:
            participant = self.participant
            self.participant = None
            self.participant_type = JANUS_VIDEOROOM_P_TYPE_NONE
            participant.destroy()

        self._pending_candidates.clear()

    def handle_hangup(self):
        # log.debug('handle_hangup for videoroom Handle {}'.format(self.handle_id))

        if self.participant_type == JANUS_VIDEOROOM_P_TYPE_PUBLISHER:
            self.participant.hangup()
        elif self.participant_type == JANUS_VIDEOROOM_P_TYPE_SUBSCRIBER:
            self.participant.hangup()

    def handle_trickle(self, candidate=None, candidates=None):
        # log.debug('handle_trickle for videoroom handle {}.candidate:{} candidates:{}'.
        #           format(self.handle_id, candidate, candidates))

        if self.participant_type == JANUS_VIDEOROOM_P_TYPE_NONE:
            # not join yet, buffer candidates
            if candidates:
                self._pending_candidates.extend(candidates)
            if candidate:
                self._pending_candidates.append(candidate)

        elif self.participant_type == JANUS_VIDEOROOM_P_TYPE_PUBLISHER:
            self.participant.trickle(candidate=candidate, candidates=candidates)
        elif self.participant_type == JANUS_VIDEOROOM_P_TYPE_SUBSCRIBER:
            self.participant.trickle(candidate=candidate, candidates=candidates)

    def handle_message(self, transaction, body, jsep=None):
        # log.debug('handle_message for videoroom handle {}. transaction:{} body:{} jsep:{}'.
        #         format(self.handle_id, transaction, body, jsep))
        result = None
        try:
            request = body.get('request')
            if request is None:
                raise JanusCloudError('Request {}  format invalid'.format(body), JANUS_VIDEOROOM_ERROR_INVALID_ELEMENT)
            if request in ('create', 'edit', 'destroy', 'list',  'exists', 'allowed', 'kick',
                           'listparticipants', 'listforwarders', 'rtp_forward',
                           'stop_rtp_forward', 'moderate', 'enable_recording'):

                result = self._handle_sync_message(transaction, body, jsep)

            elif request in ('join', 'joinandconfigure', 'configure',
                             'publish', 'unpublish', 'start', 'pause',
                             'switch', 'leave'):

                self._enqueue_async_message(transaction, body, jsep)
                return JANUS_PLUGIN_OK_WAIT, None
            else:
                raise JanusCloudError('Unknown request {}'.format(body),
                                      JANUS_VIDEOROOM_ERROR_INVALID_REQUEST)

        except JanusCloudError as e:
            log.exception('Fail to handle message ({}) for handle {}'.format(body, self.handle_id))
            type, dummy, tb = sys.exc_info()
            tb_list = traceback.format_list(traceback.extract_tb(tb)[-10:])
            result = {
                'videoroom': 'event',
                'error_code': e.code,
                'error': str(e),
                'traceback': tb_list
            }
        except SchemaError as e:
            log.exception('invalid message format ({}) for handle {}'.format(body, self.handle_id))
            type, dummy, tb = sys.exc_info()
            tb_list = traceback.format_list(traceback.extract_tb(tb)[-10:])
            result = {
                'videoroom': 'event',
                'error_code': JANUS_VIDEOROOM_ERROR_INVALID_ELEMENT,
                'error': str(e),
                'traceback': tb_list
            }
        except Exception as e:
            log.exception('Fail to handle message ({}) for handle {}'.format(body, self.handle_id))
            type, dummy, tb = sys.exc_info()
            tb_list = traceback.format_list(traceback.extract_tb(tb)[-10:])
            result = {
                'videoroom': 'event',
                'error_code': JANUS_VIDEOROOM_ERROR_UNKNOWN_ERROR,
                'error': str(e),
                'traceback': tb_list
            }

        return JANUS_PLUGIN_OK, result

    def _handle_sync_message(self, transaction, body, jsep=None):
        result = None

        request = body.get('request')

        if request == 'create':
            log.debug('Creating a new videoroom')
            room_base_info = room_base_schema.validate(body)
            admin_key = body.get('admin_key', '')
            room_params = room_params_schema.validate(body)
            new_room, saved = self._room_mgr.create(room_id=room_base_info['room'],
                                                    permanent=room_base_info['permanent'],
                                                    admin_key=admin_key,
                                                    room_params=room_params)
            result = {
                'videoroom': 'created',
                'room': new_room.room_id,
                'permanent': saved
            }
        elif request == 'edit':
            log.debug('Attempt to edit the properties of an existing videoroom room')
            room_base_info = room_base_schema.validate(body)
            room_new_params = room_edit_schema.validate(body)
            room, saved = self._room_mgr.update(room_id=room_base_info['room'],
                                                secret=room_base_info['secret'],
                                                permanent=room_base_info['permanent'],
                                                **room_new_params)
            result = {
                'videoroom': 'edited',
                'room': room_base_info['room'],
                'permanent': saved
            }
        elif request == 'destroy':
            log.debug('Attempt to destroy an existing videoroom room')
            room_base_info = room_base_schema.validate(body)
            saved = self._room_mgr.destroy(room_id=room_base_info['room'],
                                           secret=room_base_info['secret'],
                                           permanent=room_base_info['permanent'])
            result = {
                'videoroom': 'destroyed',
                'room': room_base_info['room'],
                'permanent': saved
            }
        elif request == 'list':
            log.debug('Getting the list of video rooms')
            room_list_params = room_list_schema.validate(body)
            room_list = self._room_mgr.list(**room_list_params)

            room_info_list = []
            for room in room_list:
                room_info = {
                    'room': room.room_id,
                    'description': room.description,
                    'pin_required': bool(room.pin),
                    'max_publishers': room.publishers,
                    'bitrate': room.bitrate,
                    'fir_freq': room.fir_freq,
                    'require_pvtid': room.require_pvtid,
                    'notify_joining': room.notify_joining,
                    'audiocodec': ','.join(room.audiocodec),
                    'videocodec': ','.join(room.videocodec),
                    'record': room.record,
                    'rec_dir': room.rec_dir,
                    'lock_record': room.lock_record,
                    'num_participants': room.num_participants(),
                    'audiolevel_ext': room.audiolevel_ext,
                    'audiolevel_event': room.audiolevel_event,
                    'videoorient_ext': room.videoorient_ext,
                    'playoutdelay_ext': room.playoutdelay_ext,
                    'transport_wide_cc_ext': room.transport_wide_cc_ext,
                    'require_e2ee': room.require_e2ee,
                    'is_private': room.is_private,
                }
                if room.bitrate_cap:
                    room_info['bitrate_cap'] = True
                if room.opus_fec:
                    room_info['opus_fec'] = True
                if room.opus_dtx:
                    room_info['opus_dtx'] = True
                if room.video_svc:
                    room_info['video_svc'] = True
                if room.audiolevel_event:
                    room_info['audio_active_packets'] = room.audio_active_packets
                    room_info['audio_level_average'] = room.audio_level_average

                room_info_list.append(room_info)

            result = {
                'videoroom': 'success',
                'list': room_info_list,
            }

        elif request == 'exists':
            room_base_info = room_base_schema.validate(body)
            result = {
                'videoroom': 'success',
                'room': room_base_info['room'],
                'exists': self._room_mgr.exists(room_base_info['room'])
            }

        elif request == 'allowed':
            log.debug('Attempt to edit the list of allowed participants in an existing videoroom room')
            room_base_info = room_base_schema.validate(body)
            allowed_params = allowed_schema.validate(body)
            room = self._room_mgr.get(room_base_info['room']).\
                check_modify(room_base_info['secret'])
            if allowed_params['action'] == 'enable':
                room.enable_allowed()
            elif allowed_params['action'] == 'disable':
                room.disable_allowed()
            elif allowed_params['action'] == 'add':
                room.add_allowed(allowed_params.get('allowed', []))
            elif allowed_params['action'] == 'remove':
                room.remove_allowed(allowed_params.get('allowed', []))
            else:
                raise JanusCloudError("Unsupported action '{}' (allowed)".format(allowed_params['action']),
                                      JANUS_VIDEOROOM_ERROR_INVALID_ELEMENT)
            result = {
                'videoroom': 'success',
                'room': room_base_info['room']
            }
            if allowed_params['action'] != 'disable':
                result['allowed'] = list(room.allowed)

            log.debug('VideoRoom room allowed list updated')

        elif request == 'kick':
            log.debug('Attempt to kick a participant from an existing videoroom room')
            room_base_info = room_base_schema.validate(body)
            kick_params = kick_schema.validate(body)
            room = self._room_mgr.get(room_base_info['room']). \
                check_modify(room_base_info['secret'])
            room.kick_participant(kick_params['id'])
            result = {
                'videoroom': 'success'
            }

        elif request == 'moderate':
            log.debug('Attempt to moderate a participant as a moderator in an existing VideoRoom room')
            room_base_info = room_base_schema.validate(body)
            moderate_params = moderate_schema.validate(body)
            room = self._room_mgr.get(room_base_info['room']). \
                check_modify(room_base_info['secret'])
            publisher_id = body.get('id', 0)
            publisher = room.get_participant_by_user_id(publisher_id)
            if publisher is None:
                raise JanusCloudError("No such user {} in room {}".format(publisher_id, room_base_info['room']),
                                      JANUS_VIDEOROOM_ERROR_NO_SUCH_FEED)
            publisher.moderate(**moderate_params)

            result = {
                'videoroom': 'success'
            }

        elif request == 'listparticipants':
            room_base_info = room_base_schema.validate(body)
            room = self._room_mgr.get(room_base_info['room'])
            publisher_list = room.list_participants()
            part_info_list = []
            for publisher in publisher_list:
                part_info = {
                    'id': publisher.user_id,
                    'publisher': publisher.webrtc_started,
                }
                if publisher.display:
                    part_info['display'] = publisher.display
              
                if publisher.webrtc_started:
                    found = False
                    talking = False
                    for ps in publisher.streams:
                        if ps.type == 'audio' and ps.audiolevel_ext:
                            found = True
                            talking = True if ps.talking else talking
                    if found: 
                        part_info['talking'] = talking

                part_info_list.append(part_info)

            result = {
                'videoroom': 'participants',
                'room': room_base_info['room'],
                'participants': part_info_list
            }
        elif request == 'listforwarders':
            log.debug('Attempt to list all forwarders in the room')
            room_base_info = room_base_schema.validate(body)
            room = self._room_mgr.get(room_base_info['room']).check_modify(room_base_info['secret'])
            publisher_list = room.list_participants()
            publisher_rtp_forwarders = []
            for publisher in publisher_list:
                rtp_forwarder_list = publisher.rtp_forwarder_list()

                if len(rtp_forwarder_list) == 0:
                    continue

                publisher_rtp_forwarder_info = {
                    'publisher_id': publisher.user_id,
                    'forwarders': rtp_forwarder_list,
                }
                if publisher.display:
                    publisher_rtp_forwarder_info['display'] = publisher.display

                publisher_rtp_forwarders.append(publisher_rtp_forwarder_info)

            result = {
                'videoroom': 'listforwarders',
                'room': room_base_info['room'],
                'publishers': publisher_rtp_forwarders
            }
        elif request == 'rtp_forward':
            log.debug('Attemp to start rtp forwarder')
            # check admin_key
            if self._plugin.config['general']['lock_rtp_forward'] and \
               self._plugin.config['general']['admin_key']:
                admin_key = body.get('admin_key', '')
                if admin_key != self._plugin.config['general']['admin_key']:
                    raise JanusCloudError("Unauthorized (wrong {})".format('admin_key'),
                                          JANUS_VIDEOROOM_ERROR_UNAUTHORIZED)

            room_base_info = room_base_schema.validate(body)
            room = self._room_mgr.get(room_base_info['room']).check_modify(room_base_info['secret'])
            publisher_id = body.get('publisher_id', 0)
            publisher = room.get_participant_by_user_id(publisher_id)
            if publisher is None:
                raise JanusCloudError("No such feed ({})".format(publisher_id),
                                      JANUS_VIDEOROOM_ERROR_NO_SUCH_FEED)

            forward_params = rtp_forward_schema.validate(body)
            forwarders = publisher.rtp_forward(**forward_params)

            result = {
                'videoroom': 'rtp_forward',
                'room': room_base_info['room'],
                'publisher_id': publisher_id
            }
            if isinstance(forwarders, dict):
                result['rtp_stream'] = forwarders
            else:
                result['forwarders'] = forwarders

        elif request == 'stop_rtp_forward':
            log.debug('Attempt to stop one rtp forwarder')

            # check admin_key
            if self._plugin.config['general']['lock_rtp_forward'] and \
               self._plugin.config['general']['admin_key']:
                admin_key = body.get('admin_key', '')
                if admin_key != self._plugin.config['general']['admin_key']:
                    raise JanusCloudError("Unauthorized (wrong {})".format('admin_key'),
                                          JANUS_VIDEOROOM_ERROR_UNAUTHORIZED)

            room_base_info = room_base_schema.validate(body)
            room = self._room_mgr.get(room_base_info['room']).check_modify(room_base_info['secret'])
            stream_info = stop_rtp_forward_schema.validate(body)
            publisher = room.get_participant_by_user_id(stream_info['publisher_id'])
            if publisher is None:
                raise JanusCloudError("No such feed ({})".format(stream_info['publisher_id']),
                                      JANUS_VIDEOROOM_ERROR_NO_SUCH_FEED)

            publisher.stop_rtp_forward(stream_info['stream_id'])

            result = {
                'videoroom': 'stop_rtp_forward',
                'room': room_base_info['room'],
                'publisher_id': stream_info['publisher_id'],
                'stream_id': stream_info['stream_id']
            }
        elif request == 'enable_recording':

            room_base_info = room_base_schema.validate(body)
            room = self._room_mgr.get(room_base_info['room']).check_modify(room_base_info['secret'])
            record_params = record_schema.validate(body)

            log.debug('Enable Recording: {} for room {}'.format(record_params['record'], room.room_id))

            room.enable_recording(record_params['record'])

            result = {
                'videoroom': 'success',
                'record': record_params['record'],
            }

        else:
            raise JanusCloudError('Unknown request {}'.format(body),
                                  JANUS_VIDEOROOM_ERROR_INVALID_REQUEST)

        if result is None:
            raise JanusCloudError('Invalid response',
                                  JANUS_VIDEOROOM_ERROR_UNKNOWN_ERROR)
        return result

    def _handle_async_message(self, transaction, body, jsep):
        try:
            request = body.get('request')
            if request is None:
                raise JanusCloudError('Request {}  format invalid'.format(body), JANUS_VIDEOROOM_ERROR_INVALID_ELEMENT)

            reply_event = None
            reply_jsep = None
            if self.participant_type == JANUS_VIDEOROOM_P_TYPE_NONE:
                if request == 'join' or request == 'joinandconfigure':
                    join_base_info = join_base_schema.validate(body)
                    room = self._room_mgr.get(join_base_info['room']). \
                        check_join(join_base_info['pin'])
                    ptype = join_base_info['ptype']

                    if ptype == 'publisher':
                        log.debug("Configuring new publisher")
                        join_params = publisher_join_schema.validate(body)
                        room.check_token(join_params.get('token', ''))


                        if request == 'joinandconfigure':
                            room.check_max_publishers()

                        new_publisher = room.new_participant(
                            user_id=join_params.get('id', 0),
                            handle=self,
                            display=join_params.get('display', '')
                        )
                        try:
                            if request == 'joinandconfigure':
                                # configure the publisher at once
                                publish_params = publisher_publish_schema.validate(body)
                                publish_params.pop('display', None)  # no new display to set
                                if jsep:
                                    publish_params['jsep'] = jsep
                                reply_jsep = new_publisher.publish(**publish_params)

                            # attach publisher to self
                            self.participant_type = JANUS_VIDEOROOM_P_TYPE_PUBLISHER
                            self.participant = new_publisher

                            # flush candidates if pending
                            if len(self._pending_candidates) > 0:
                                new_publisher.trickle(candidates=self._pending_candidates)
                                self._pending_candidates.clear()

                            # prepare reply_event
                            if room.notify_joining:
                                attendees = []
                            else:
                                attendees = None
                            publishers = []
                            publisher_list = room.list_participants()
                            for publisher in publisher_list:
                                if publisher != new_publisher and publisher.webrtc_started and publisher.sdp:
                                    publisher_info = {
                                        'id': publisher.user_id,
                                    }
                                    if publisher.display:
                                        publisher_info['display'] = publisher.display
                                    audio_added = False
                                    video_added = False
                                    talking_found = False
                                    talking = False
                                    media = []
                                    for ps in publisher.streams:
                                        info = {
                                            'type': ps.type,
                                            'mindex': ps.mindex,
                                            'mid': ps.mid
                                        }
                                        if ps.disabled:
                                            info['disabled'] = True
                                        else:
                                            if ps.description:
                                                info['description'] = ps.description

                                            if ps.type == 'audio':
                                                info['codec'] = ps.codec
                                                if not audio_added:
                                                    audio_added = True
                                                    publisher_info['audio_codec'] = ps.codec
                                                if ps.codec == 'opus':
                                                    if ps.fec:
                                                        info['fec'] = True
                                                    if ps.dtx:
                                                        info['dtx'] = True
                                                    if ps.stereo:
                                                        info['stereo'] = True
                                                if ps.audiolevel_ext:
                                                    info['talking'] = ps.talking
                                                    talking_found = True
                                                    talking = True if ps.talking else talking
                                            elif ps.type == 'video':
                                                info['codec'] = ps.codec
                                                if not video_added:
                                                    video_added = True
                                                    publisher_info['video_codec'] = ps.codec                                            
                                                if ps.codec == 'h264' and ps.h264_profile:
                                                    info['h264_profile'] = ps.h264_profile
                                                if ps.codec == 'vp9' and ps.vp9_profile:
                                                    info['vp9_profile'] = ps.vp9_profile
                                                if ps.simulcast:
                                                    info['simulcast'] = True
                                                if ps.svc:
                                                    info['svc'] = True
                                            if ps.moderated:
                                                info['moderated'] = True
                                        media.append(info)

                                    publisher_info['streams'] = media
                                    if talking_found:
                                        publisher_info['talking'] = talking
                                    publishers.append(publisher_info)

                                elif publisher != new_publisher and attendees is not None:
                                    attendee_info = {
                                        'id': publisher.user_id,
                                    }
                                    if publisher.display:
                                        attendee_info['display'] = publisher.display
                                    attendees.append(attendee_info)

                            reply_event = {
                                'videoroom': 'joined',
                                'room': room.room_id,
                                'description': room.description,
                                'id': new_publisher.user_id,
                                'private_id': new_publisher.pvt_id,
                                'publishers': publishers
                            }
                            if attendees is not None:
                                reply_event['attendees'] = attendees
                            if new_publisher.user_audio_active_packets:
                                reply_event['audio_active_packets'] = new_publisher.user_audio_active_packets
                            if new_publisher.user_audio_level_average:
                                reply_event['user_audio_level_average'] = new_publisher.user_audio_level_average

                            # For backwards compatibility
                            if reply_jsep and new_publisher.vcodec:
                                reply_event['video_codec'] = new_publisher.vcodec
                            if reply_jsep and new_publisher.acodec:
                                reply_event['audio_codec'] = new_publisher.acodec                            
                            
                            if reply_jsep:
                                reply_event['streams'] = new_publisher.streams_info()

                        except Exception:
                            self.participant_type = JANUS_VIDEOROOM_P_TYPE_NONE
                            self.participant = None
                            if new_publisher:
                                new_publisher.destroy()
                            raise
                    elif ptype == 'subscriber' :
                        if request != 'join':
                            raise JanusCloudError('Invalid element (ptype)',
                                                  JANUS_VIDEOROOM_ERROR_INVALID_ELEMENT)
                        
                        log.debug("Configuring new subscriber")
                        join_params = subscriber_join_schema.validate(body)
                        pvt_id = join_params.pop('private_id', 0)

                        legacy = False
                        streams =  join_params.get('streams')
                        if not streams:
                            legacy_feed_id = join_params.get('feed', 0)
                            legacy_publisher = room.get_participant_by_user_id(legacy_feed_id)
                            legacy_display = legacy_publisher.display if legacy_publisher else ''
                            legacy = True
                            # JANUS_LOG(LOG_WARN, "Deprecated subscriber 'join' API: please start looking into the new one for the future\n");
                            log.warning( "Deprecated subscriber 'join' API: please start looking into the new one for the future")

                        new_subscriber, reply_jsep = \
                            room.new_subscriber(handle=self, pvt_id=pvt_id, **join_params)
                        try:
                            # reply_jsep = new_subscriber.join(room=room, **join_params)

                            # attach subscriber to self
                            self.participant_type = JANUS_VIDEOROOM_P_TYPE_SUBSCRIBER
                            self.participant = new_subscriber

                            # flush candidates if pending
                            if len(self._pending_candidates) > 0:
                                new_subscriber.trickle(candidates=self._pending_candidates)
                                self._pending_candidates.clear()

                            # prepare reply event
                            reply_event = {
                                'videoroom': 'attached',
                                'room': room.room_id,
                                'streams': new_subscriber.streams_info()
                            }

                            if legacy:
                                reply_event['id'] = legacy_feed_id
                                if legacy_display:
                                    reply_event['display'] = legacy_display
                                reply_event['warning'] = \
                                    'deprecated_api'

                        except Exception:
                            self.participant_type = JANUS_VIDEOROOM_P_TYPE_NONE
                            self.participant = None
                            if new_subscriber:
                                new_subscriber.destroy()
                            raise
                    else:
                        raise JanusCloudError('Invalid element (ptype)',
                                              JANUS_VIDEOROOM_ERROR_INVALID_ELEMENT)
                else:
                    raise JanusCloudError('Invalid request "{}" on unconfigured participant'.format(request),
                                          JANUS_VIDEOROOM_ERROR_JOIN_FIRST)
            elif self.participant_type == JANUS_VIDEOROOM_P_TYPE_PUBLISHER:
                publisher = self.participant
                if publisher.room is None:
                    raise JanusCloudError('No such room',
                                          JANUS_VIDEOROOM_ERROR_NO_SUCH_ROOM)

                if request == 'configure':
                    configure_params = publisher_configure_schema.validate(body)
                    if jsep:
                        configure_params['jsep'] = jsep
                    if publisher.sdp == '' and jsep:
                        publisher.room.check_max_publishers()

                    sdp_update = False
                    if publisher.sdp and jsep and jsep.get('sdp'):
                        sdp_update = True

                    reply_jsep = publisher.configure(**configure_params)

                    reply_event = {
                        'videoroom': 'event',
                        'room': publisher.room_id,
                        'configured': 'ok',
                    }
                    # For backwards compatibility, update the event with info on the codecs that we'll be handling
				    # TODO This will make no sense in the future, as different streams may use different codecs 
                    if reply_jsep and publisher.vcodec:
                        reply_event['video_codec'] = publisher.vcodec
                    if reply_jsep and publisher.acodec:
                        reply_event['audio_codec'] = publisher.acodec
                    if reply_jsep:
                        reply_event['streams'] = publisher.streams_info()
                    
                    if sdp_update and publisher.webrtc_started:
                        self._push_plugin_event(data=reply_event, 
                                                    jsep=reply_jsep, 
                                                    transaction=transaction)
                        reply_event = None
                        reply_jsep = None
                        if publisher.room is not None:
                            publisher.room.notify_about_publisher(publisher)

                elif request == 'publish':
                    publish_params = publisher_publish_schema.validate(body)
                    if jsep:
                        publish_params['jsep'] = jsep
                    if publisher.sdp == '' and jsep:
                        publisher.room.check_max_publishers()
                    reply_jsep = publisher.publish(**publish_params)
                    reply_event = {
                        'videoroom': 'event',
                        'room': publisher.room_id,
                        'configured': 'ok',
                    }

                    # For backwards compatibility
                    if reply_jsep and publisher.vcodec:
                        reply_event['video_codec'] = publisher.vcodec
                    if reply_jsep and publisher.acodec:
                        reply_event['audio_codec'] = publisher.acodec
                    if reply_jsep:
                        reply_event['streams'] = publisher.streams_info()

                elif request == 'unpublish':
                    publisher.unpublish()
                    reply_event = {
                        'videoroom': 'event',
                        'room': publisher.room_id,
                        'unpublished': 'ok',
                    }
                elif request == 'leave':
                    room_id = publisher.room_id
                    publisher.destroy()
                    reply_event = {
                        'videoroom': 'event',
                        'room': room_id,
                        'leaving': 'ok',
                    }
                elif request == 'join' or request == 'joinandconfigure':
                    raise JanusCloudError('Already in as a publisher on this handle',
                                          JANUS_VIDEOROOM_ERROR_ALREADY_JOINED)
                else:
                    raise JanusCloudError('Unknown request \'{}\''.format(request),
                                          JANUS_VIDEOROOM_ERROR_INVALID_REQUEST)

            elif self.participant_type == JANUS_VIDEOROOM_P_TYPE_SUBSCRIBER:
                subscriber = self.participant
                if request == 'configure':
                    configure_params = subscriber_configure_schema.validate(body)
                    reply_jsep = subscriber.configure(**configure_params)
                    reply_event = {
                        'videoroom': 'event',
                        'room': subscriber.room_id,
                        'configured': 'ok',
                    }
                elif request == 'start':
                    subscriber.start(jsep)
                    reply_event = {
                        'videoroom': 'event',
                        'room': subscriber.room_id,
                        'started': 'ok',
                    }
                elif request == 'pause':
                    subscriber.pause()
                    reply_event = {
                        'videoroom': 'event',
                        'room': subscriber.room_id,
                        'paused': 'ok',
                    }
                elif request == 'join':
                    raise JanusCloudError('Already in as a subscriber on this handle',
                                          JANUS_VIDEOROOM_ERROR_ALREADY_JOINED)
                elif request == 'switch':
                    raise JanusCloudError('unsupported request {}'.format(body),
                                          JANUS_VIDEOROOM_ERROR_INVALID_REQUEST)
                elif request == 'leave':
                    room_id = subscriber.room_id
                    subscriber.destroy()
                    reply_event = {
                        'videoroom': 'event',
                        'room': room_id,
                        'left': 'ok',
                    }

                elif request == 'subscribe':
                    log.debug('Adding new subscriber streams')

                    subscribe_params = subscriber_subscribe_schema.validate(body)
                    update_result, reply_jsep = subscriber.subscribe(**subscribe_params)
                    reply_event = {
                        'videoroom': update_result,
                        'room': subscriber.room_id,
                    }
                    if reply_jsep:
                        reply_event['streams'] = subscriber.streams_info()
                elif request == 'unsubscribe':
                    log.debug('Removing subscriber streams')

                    unsubscribe_params = subscriber_unsubscribe_schema.validate(body)
                    update_result, reply_jsep = subscriber.unsubscribe(**unsubscribe_params)

                    reply_event = {
                        'videoroom': update_result,
                        'room': subscriber.room_id,
                    }
                    if reply_jsep:
                        reply_event['streams'] = subscriber.streams_info()
                elif request == 'update':
                    log.debug('Adding new subscriber streams')
                    log.debug('Removing subscriber streams')

                    update_params = subscriber_update_schema.validate(body)
                    update_result, reply_jsep = subscriber.update(**update_params)

                    reply_event = {
                        'videoroom': update_result,
                        'room': subscriber.room_id,
                    }
                    if reply_jsep:
                        reply_event['streams'] = subscriber.streams_info()

                else:
                    raise JanusCloudError('Unknown request \'{}\''.format(request),
                                          JANUS_VIDEOROOM_ERROR_INVALID_REQUEST)

            # Process successfully
            if reply_event:
                self._push_plugin_event(data=reply_event, jsep=reply_jsep, transaction=transaction)

        except JanusCloudError as e:
            log.exception('Fail to handle async message ({}) for handle {}'.format(body, self.handle_id))
            type, dummy, tb = sys.exc_info()
            tb_list = traceback.format_list(traceback.extract_tb(tb)[-10:])
            self._push_plugin_event({
                'videoroom': 'event',
                'error_code': e.code,
                'error': str(e),
                'traceback': tb_list,
            }, transaction=transaction)
        except SchemaError as e:
            log.exception('invalid message format ({}) for handle {}'.format(body, self.handle_id))
            type, dummy, tb = sys.exc_info()
            tb_list = traceback.format_list(traceback.extract_tb(tb)[-10:])
            self._push_plugin_event({
                'videoroom': 'event',
                'error_code': JANUS_VIDEOROOM_ERROR_INVALID_ELEMENT,
                'error': str(e),
                'traceback': tb_list,
            }, transaction=transaction)
        except Exception as e:
            log.exception('Fail to handle async message ({}) for handle {}'.format(body, self.handle_id))
            type, dummy, tb = sys.exc_info()
            tb_list = traceback.format_list(traceback.extract_tb(tb)[-10:])
            self._push_plugin_event({
                'videoroom': 'event',
                'error_code': JANUS_ERROR_BAD_GATEWAY,
                'error': str(e),
                'traceback': tb_list,
            }, transaction=transaction)

    def on_participant_detach(self, participant):
        if self.participant == participant:
            self.participant_type = JANUS_VIDEOROOM_P_TYPE_NONE
            self.participant = None

    def push_plugin_event(self, data, jsep=None, transaction=None):
        self._push_plugin_event(data=data, jsep=jsep, transaction=transaction)

    def push_event(self, method, transaction=None, **kwargs):
        self._push_event(method=method, transaction=transaction, **kwargs)


    def choose_server(self, transport=None):
        if transport is None:
            transport = self._session.ts
        return self._plugin.backend_server_mgr.choose_server(self._session.ts)

class VideoRoomPlugin(PluginBase):
    """ This video room plugin """

    def __init__(self, proxy_config, backend_server_mgr, pyramid_config):
        super().__init__(proxy_config, backend_server_mgr, pyramid_config)
        self.handles = set()
        self.config = self.read_config(
            os.path.join(proxy_config['general']['configs_folder'], 'janus-proxy.plugin.videoroom.yml')
        )
        self.backend_server_mgr = backend_server_mgr
        room_dao = None
        if self.config['general']['room_db'].startswith('memory'):
            room_dao = None
        elif self.config['general']['room_db'].startswith('redis://'):
            import redis
            from januscloud.proxy.dao.rd_room_dao import RDRoomDao
            connection_pool = redis.BlockingConnectionPool.from_url(
                url=self.config['general']['room_db'],
                decode_responses=True,
                health_check_interval=30,
                timeout=10)
            redis_client = redis.Redis(connection_pool=connection_pool)
            room_dao = RDRoomDao(redis_client)
        else:
            raise JanusCloudError(
                'room_db \'{}\' not support by videoroom plugin'.format(self.config['general']['room_db']),
                JANUS_ERROR_NOT_IMPLEMENTED)

        self.room_mgr = VideoRoomManager(
            room_db=self.config['general']['room_db'],
            room_dao=room_dao,
            auto_cleanup_sec=self.config['general']['room_auto_destroy_timeout'],
            admin_key=self.config['general']['admin_key']
        )

        self.room_mgr.load_from_config(self.config['rooms'])

        includeme(pyramid_config)
        pyramid_config.registry.videoroom_plugin = self

        log.info('{} initialized!'.format(JANUS_VIDEOROOM_NAME))

    def get_version(self):
        return JANUS_VIDEOROOM_VERSION

    def get_version_string(self):
        return JANUS_VIDEOROOM_VERSION_STRING

    def get_description(self):
        return JANUS_VIDEOROOM_DESCRIPTION

    def get_name(self):
        return JANUS_VIDEOROOM_NAME

    def get_author(self):
        return JANUS_VIDEOROOM_AUTHOR

    def get_package(self):
        return JANUS_VIDEOROOM_PACKAGE

    def create_handle(self, handle_id, session, opaque_id=None, *args, **kwargs):
        return VideoRoomHandle(handle_id, session, self, opaque_id, *args, **kwargs)

    @staticmethod
    def read_config(config_file):

        videoroom_config_schema = Schema({
            Optional("general"): Default({
                Optional("room_db"): Default(StrVal(), default='memory'),
                Optional("room_auto_destroy_timeout"): Default(IntVal(min=0, max=86400), default=0),
                Optional("admin_key"): Default(StrVal(), default=''),
                Optional("lock_rtp_forward"): Default(BoolVal(), default=False),
                AutoDel(str): object  # for all other key we don't care
            }, default={}),
            Optional("rooms"): Default([{
                'room_id': IntVal(),
                Optional('description'): StrVal(),
                Optional('is_private'): BoolVal(),
                Optional('secret'): StrVal(),
                Optional('pin'): StrVal(),
                Optional('require_pvtid'): BoolVal(),
                Optional('publishers'): IntVal(min=1, max=8192),
                Optional('bitrate'): IntVal(min=0),
                Optional('bitrate_cap'): BoolVal(),
                Optional('fir_freq'): IntVal(min=0),
                Optional('audiocodec'): ListVal(EnumVal(['opus', 'multiopus', 'g722', 'pcmu', 'pcma', 'isac32', 'isac16'])),
                Optional('videocodec'): ListVal(EnumVal(['vp8', 'vp9', 'h264', 'av1', 'h265'])),
                Optional('vp9_profile'): StrVal(max_len=256),
                Optional('h264_profile'): StrVal(max_len=256),
                Optional('opus_fec'): BoolVal(),
                Optional('opus_dtx'): BoolVal(),
                Optional('video_svc'): BoolVal(),
                Optional('audiolevel_ext'): BoolVal(),
                Optional('audiolevel_event'): BoolVal(),
                Optional('audio_active_packets'): IntVal(min=1),
                Optional('audio_level_average'): IntVal(min=1, max=127),
                Optional('videoorient_ext'): BoolVal(),
                Optional('playoutdelay_ext'): BoolVal(),
                Optional('transport_wide_cc_ext'): BoolVal(),
                Optional('record'): BoolVal(),
                Optional('rec_dir'): StrVal(),
                Optional('lock_record'): BoolVal(),
                Optional('notify_joining'): BoolVal(),
                Optional('require_e2ee'): BoolVal(),
                AutoDel(str): object  # for all other key we don't care
            }], default=[]),
            DoNotCare(str): object  # for all other key we don't care

        })
        #print('config file:', config_file)
        if config_file is None or config_file == '':
            config = videoroom_config_schema.validate({})
        else:
            log.info('Videoroom plugin loads the config file: {}'.format(os.path.abspath(config_file)))
            config = parse_config(config_file, videoroom_config_schema)

        # check other configure option is valid or not

        return config


def includeme(config):
    config.add_route('videoroom_info', JANUS_VIDEOROOM_API_BASE_PATH)
    config.add_route('videoroom_room_list', JANUS_VIDEOROOM_API_BASE_PATH + '/rooms')
    config.add_route('videoroom_room', JANUS_VIDEOROOM_API_BASE_PATH + '/rooms/{room_id}')
    config.add_route('videoroom_participant_list', JANUS_VIDEOROOM_API_BASE_PATH + '/rooms/{room_id}/participants')
    config.add_route('videoroom_participant', JANUS_VIDEOROOM_API_BASE_PATH + '/rooms/{room_id}/participants/{user_id}')
    config.add_route('videoroom_tokens', JANUS_VIDEOROOM_API_BASE_PATH + '/rooms/{room_id}/tokens')
    config.add_route('videoroom_forwarder_list', JANUS_VIDEOROOM_API_BASE_PATH + '/rooms/{room_id}/rtp_forwarders')
    config.scan('januscloud.proxy.plugin.videoroom')


@get_view(route_name='videoroom_info')
def get_videoroom_info(request):
    plugin = request.registry.videoroom_plugin
    room_mgr = plugin.room_mgr

    videoroom_info = {
        'package': plugin.get_package(),
        'version': plugin.get_version(),
        'version_str': plugin.get_version_string(),
        'name': plugin.get_name(),
        'author': plugin.get_author(),
        'description': plugin.get_description(),
        'handles': len(plugin.handles),
        'rooms': len(room_mgr)
    }
    return videoroom_info


@get_view(route_name='videoroom_room_list')
def get_videoroom_room_list(request):
    plugin = request.registry.videoroom_plugin
    room_mgr = plugin.room_mgr

    room_list_params = get_params_from_request(request, room_list_schema)
    room_list = room_mgr.list(**room_list_params)

    room_info_list = []
    for room in room_list:
        room_info = {
            'room': room.room_id,
            'description': room.description,
            'pin_required': bool(room.pin),
            'max_publishers': room.publishers,
            'bitrate': room.bitrate,
            'fir_freq': room.fir_freq,
            'require_pvtid': room.require_pvtid,
            'notify_joining': room.notify_joining,
            'audiocodec': ','.join(room.audiocodec),
            'videocodec': ','.join(room.videocodec),
            'record': room.record,
            'record_dir': room.rec_dir,
            'lock_record': room.lock_record,
            'num_participants': room.num_participants()
        }
        if room.bitrate_cap:
            room_info['bitrate_cap'] = True
        if room.opus_fec:
            room_info['opus_fec'] = True
        if room.opus_dtx:
            room_info['opus_dtx'] = True            
        if room.video_svc:
            room_info['video_svc'] = True

        room_info_list.append(room_info)

    return room_info_list


@post_view(route_name='videoroom_room_list')
def post_videoroom_room_list(request):
    plugin = request.registry.videoroom_plugin
    room_mgr = plugin.room_mgr

    log.debug('Creating a new videoroom')
    params = get_params_from_request(request)
    room_base_info = room_base_schema.validate(params)
    admin_key = params.get('admin_key', '')
    room_params = room_params_schema.validate(params)
    new_room, saved = room_mgr.create(room_id=room_base_info['room'],
                               permanent=room_base_info['permanent'],
                               admin_key=admin_key,
                               room_params=room_params)
    reply = {
        'videoroom': 'created',
        'room': new_room.room_id,
        'permanent': saved
    }

    return reply

@get_view(route_name='videoroom_room')
def get_videoroom_room(request):
    plugin = request.registry.videoroom_plugin
    room_mgr = plugin.room_mgr
    room_id = int(request.matchdict['room_id'])
    room = room_mgr.get(room_id)

    room_info = {
        'room': room.room_id,
        'description': room.description,
        'pin_required': bool(room.pin),
        'max_publishers': room.publishers,
        'bitrate': room.bitrate,
        'fir_freq': room.fir_freq,
        'require_pvtid': room.require_pvtid,
        'notify_joining': room.notify_joining,
        'audiocodec': ','.join(room.audiocodec),
        'videocodec': ','.join(room.videocodec),
        'record': room.record,
        'record_dir': room.rec_dir,
        'lock_record': room.lock_record,
        'num_participants': room.num_participants()
    }
    if room.bitrate_cap:
        room_info['bitrate_cap'] = True
    if room.opus_fec:
        room_info['opus_fec'] = True
    if room.opus_dtx:
        room_info['opus_dtx'] = True        
    if room.video_svc:
        room_info['video_svc'] = True

    return room_info


@delete_view(route_name='videoroom_room')
def delete_videoroom_room(request):
    plugin = request.registry.videoroom_plugin
    room_mgr = plugin.room_mgr
    room_id = int(request.matchdict['room_id'])
    room_base_info = get_params_from_request(request, room_base_schema)

    room_mgr.destroy(room_id=room_id,
                     secret=room_base_info['secret'],
                     permanent=room_base_info['permanent'])

    return Response(status=200)


#@get_view(route_name='videoroom_tokens')
#def get_videoroom_tokens(request):
#    plugin = request.registry.videoroom_plugin
#    room_mgr = plugin.room_mgr
#    room_id = int(request.matchdict['room_id'])
#    room = room_mgr.get(room_id)
#    tokens_info = list(room.allowed)
#    return tokens_info


@post_view(route_name='videoroom_tokens')
def post_videoroom_tokens(request):
    plugin = request.registry.videoroom_plugin
    room_mgr = plugin.room_mgr
    room_id = int(request.matchdict['room_id'])
    params = get_params_from_request(request)
    room_base_info = room_base_schema.validate(params)
    allowed_params = allowed_schema.validate(params)
    room = room_mgr.get(room_id).check_modify(room_base_info['secret'])
    if allowed_params['action'] == 'enable':
        room.enable_allowed()
    elif allowed_params['action'] == 'disable':
        room.disable_allowed()
    elif allowed_params['action'] == 'add':
        room.add_allowed(allowed_params.get('allowed', []))
    elif allowed_params['action'] == 'remove':
        room.remove_allowed(allowed_params.get('allowed', []))
    else:
        raise JanusCloudError("Unsupported action '{}' (allowed)".format(allowed_params['action']),
                              JANUS_VIDEOROOM_ERROR_INVALID_ELEMENT)
    reply = {
        'videoroom': 'success',
        'room': room_id
    }
    if allowed_params['action'] != 'disable':
        reply['allowed'] = list(room.allowed)

    return reply


@get_view(route_name='videoroom_participant_list')
def get_videoroom_participant_list(request):
    plugin = request.registry.videoroom_plugin
    room_mgr = plugin.room_mgr
    room_id = int(request.matchdict['room_id'])
    room = room_mgr.get(room_id)
    publisher_list = room.list_participants()

    part_info_list = []
    for publisher in publisher_list:
        part_info = {
            'id': publisher.user_id,
            'publisher': publisher.webrtc_started,
        }
        if publisher.display:
            part_info['display'] = publisher.display

        if publisher.webrtc_started:
            found = False
            talking = False
            for ps in publisher.streams:
                if ps.type == 'audio' and ps.audiolevel_ext:
                    found = True
                    talking = True if ps.talking else talking
            if found: 
                part_info['talking'] = talking

        backend_room = publisher.get_backend_room()
        if backend_room:
            part_info['backend_server'] = '{} ({})'.format(
                backend_room.server_name, 
                backend_room.server_url)
            part_info['backend_room_id'] = backend_room.backend_room_id

        part_info_list.append(part_info)

    return part_info_list


@delete_view(route_name='videoroom_participant')
def delete_videoroom_participant(request):
    plugin = request.registry.videoroom_plugin
    room_mgr = plugin.room_mgr
    room_id = int(request.matchdict['room_id'])
    user_id = int(request.matchdict['user_id'])
    room_base_info = get_params_from_request(request, room_base_schema)
    room = room_mgr.get(room_id).check_modify(room_base_info['secret'])
    room.kick_participant(user_id)

    return Response(status=200)

@get_view(route_name='videoroom_forwarder_list')
def get_videoroom_forwarder_list(request):
    plugin = request.registry.videoroom_plugin
    room_mgr = plugin.room_mgr
    room_id = int(request.matchdict['room_id'])
    params = get_params_from_request(request)
    room_base_info = room_base_schema.validate(params)
    room = room_mgr.get(room_id).check_modify(room_base_info['secret'])
    publisher_list = room.list_participants()
    publisher_rtp_forwarders = []
    for publisher in publisher_list:

        rtp_forwarder_list = publisher.rtp_forwarder_list()

        if len(rtp_forwarder_list) == 0:
            continue

        publisher_rtp_forwarder_info = {
            'publisher_id': publisher.user_id,
            'forwarders': rtp_forwarder_list,
        }

        if publisher.display:
            publisher_rtp_forwarder_info['display'] = publisher.display
        publisher_rtp_forwarders.append(publisher_rtp_forwarder_info)

    return publisher_rtp_forwarders


@post_view(route_name='videoroom_forwarder_list')
def post_videoroom_forwarder_list(request):
    plugin = request.registry.videoroom_plugin
    room_mgr = plugin.room_mgr
    room_id = int(request.matchdict['room_id'])
    params = get_params_from_request(request)

    log.debug('Attemp to start rtp forwarder')
    # check admin_key
    if plugin.config['general']['lock_rtp_forward'] and \
            plugin.config['general']['admin_key']:
        admin_key = params.get('admin_key', '')
        if admin_key != plugin.config['general']['admin_key']:
            raise JanusCloudError("Unauthorized (wrong {})".format('admin_key'),
                                  JANUS_VIDEOROOM_ERROR_UNAUTHORIZED)

    room_base_info = room_base_schema.validate(params)
    room = room_mgr.get(room_id).check_modify(room_base_info['secret'])
    publisher_id = int(params.get('publisher_id', 0))
    publisher = room.get_participant_by_user_id(publisher_id)
    if publisher is None:
        raise JanusCloudError("No such feed ({})".format(publisher_id),
                              JANUS_VIDEOROOM_ERROR_NO_SUCH_FEED)

    forward_params = rtp_forward_schema.validate(params)
    forwarders = publisher.rtp_forward(**forward_params)

    return forwarders


@delete_view(route_name='videoroom_forwarder_list')
def delete_videoroom_forwarder_list(request):
    plugin = request.registry.videoroom_plugin
    room_mgr = plugin.room_mgr
    room_id = int(request.matchdict['room_id'])
    params = get_params_from_request(request)

    log.debug('Attempt to stop one rtp forwarder')

    # check admin_key
    if plugin.config['general']['lock_rtp_forward'] and \
            plugin.config['general']['admin_key']:
        admin_key = params.get('admin_key', '')
        if admin_key != plugin.config['general']['admin_key']:
            raise JanusCloudError("Unauthorized (wrong {})".format('admin_key'),
                                  JANUS_VIDEOROOM_ERROR_UNAUTHORIZED)

    room_base_info = room_base_schema.validate(params)
    room = room_mgr.get(room_id).check_modify(room_base_info['secret'])
    stream_info = stop_rtp_forward_schema.validate(params)
    publisher = room.get_participant_by_user_id(stream_info['publisher_id'])
    if publisher is None:
        raise JanusCloudError("No such feed ({})".format(stream_info['publisher_id']),
                              JANUS_VIDEOROOM_ERROR_NO_SUCH_FEED)

    publisher.stop_rtp_forward(stream_info['stream_id'])

    return Response(status=200)
