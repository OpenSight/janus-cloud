# -*- coding: utf-8 -*-
import base64
import copy

import logging
import re
from urllib.parse import urlparse
from januscloud.common.utils import error_to_janus_msg, create_janus_msg, random_uint64, random_uint32, \
    get_monotonic_time
from januscloud.common.error import JanusCloudError, JANUS_ERROR_UNKNOWN_REQUEST, JANUS_ERROR_INVALID_REQUEST_PATH, \
    JANUS_ERROR_BAD_GATEWAY, JANUS_ERROR_CONFLICT, JANUS_ERROR_NOT_IMPLEMENTED, JANUS_ERROR_INTERNAL_ERROR, \
    JANUS_ERROR_GATEWAY_TIMEOUT
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
from gevent.lock import BoundedSemaphore

log = logging.getLogger(__name__)

BACKEND_SESSION_AUTO_DESTROY_TIME = 10    # auto destroy the backend session after 10s if no handle for it

ROOM_CLEANUP_CHECK_INTERVAL = 10  # CHECK EMPTY ROOM INTERVAL

JANUS_AUDIOBRIDGE_ERROR_UNKNOWN_ERROR = 499
JANUS_AUDIOBRIDGE_ERROR_NO_MESSAGE = 480
JANUS_AUDIOBRIDGE_ERROR_INVALID_JSON = 481
JANUS_AUDIOBRIDGE_ERROR_INVALID_REQUEST	= 482
JANUS_AUDIOBRIDGE_ERROR_MISSING_ELEMENT = 483
JANUS_AUDIOBRIDGE_ERROR_INVALID_ELEMENT = 484
JANUS_AUDIOBRIDGE_ERROR_NO_SUCH_ROOM = 485
JANUS_AUDIOBRIDGE_ERROR_ROOM_EXISTS = 486
JANUS_AUDIOBRIDGE_ERROR_NOT_JOINED = 487
JANUS_AUDIOBRIDGE_ERROR_LIBOPUS_ERROR = 488
JANUS_AUDIOBRIDGE_ERROR_UNAUTHORIZED = 489
JANUS_AUDIOBRIDGE_ERROR_ID_EXISTS = 490
JANUS_AUDIOBRIDGE_ERROR_ALREADY_JOINED = 491
JANUS_AUDIOBRIDGE_ERROR_NO_SUCH_USER = 492
JANUS_AUDIOBRIDGE_ERROR_INVALID_SDP = 493
JANUS_AUDIOBRIDGE_ERROR_NO_SUCH_GROUP = 494
JANUS_AUDIOBRIDGE_ERROR_ALREADY_DESTROYED = 470
JANUS_AUDIOBRIDGE_ERROR_ALREADY_BACKEND = 471

JANUS_AUDIOBRIDGE_API_SYNC_VERSION = 'v1.0.3(2022-06-20)'

JANUS_AUDIOBRIDGE_VERSION = 12
JANUS_AUDIOBRIDGE_VERSION_STRING = '0.0.12'
JANUS_AUDIOBRIDGE_DESCRIPTION = 'This is a plugin implementing an audio conference bridge for Janus-cloud, mixing Opus streams, ' \
                              'whose API is kept sync with the audiobridge plugin of Janus-gateway ' \
                              'until ' + JANUS_AUDIOBRIDGE_API_SYNC_VERSION
JANUS_AUDIOBRIDGE_NAME = 'JANUS AudioBridge plugin'
JANUS_AUDIOBRIDGE_AUTHOR = 'opensight.cn'
JANUS_AUDIOBRIDGE_PACKAGE = 'janus.plugin.audiobridge'


JANUS_AUDIOBRIDGE_API_BASE_PATH = '/plugins/audiobridge'


DEFAULT_PREBUFFERING = 6
MAX_PREBUFFERING = 50

DEFAULT_COMPLEXITY = 4

JANUS_AUDIOBRIDGE_MAX_GROUPS = 5

JANUS_RTP_EXTMAP_AUDIO_LEVEL = "urn:ietf:params:rtp-hdrext:ssrc-audio-level"

JANUS_AUDIOBRIDGE_P_TYPE_NONE = 0
JANUS_AUDIOBRIDGE_P_TYPE_PARTICIPANT = 1
JANUS_AUDIOBRIDGE_P_TYPE_LISTENER = 2

room_base_schema = Schema({
    Optional('secret'): Default(StrVal(max_len=256), default=''),
    Optional('room'): Default(IntVal(min=0), default=0),
    Optional('permanent'): Default(BoolVal(), default=False),
    AutoDel(str): object  # for all other key we must delete
})

room_params_schema = Schema({
    Optional('description'): StrVal(),
    Optional('is_private'): BoolVal(),
    Optional('secret'): StrVal(),
    Optional('pin'): StrVal(),
    Optional('allowed'): ListVal(StrVal(max_len=256)),
    Optional('sampling_rate'): IntVal(values=(8000, 12000, 16000, 24000, 48000)),
    Optional('spatial_audio'): BoolVal(),
    Optional('audiolevel_ext'): BoolVal(),
    Optional('audiolevel_event'): BoolVal(),
    Optional('audio_active_packets'): IntVal(min=1),
    Optional('audio_level_average'): IntVal(min=1, max=127),
    Optional('default_prebuffering'): IntVal(min=0, max=MAX_PREBUFFERING),
    Optional('default_expectedloss'): IntVal(min=0, max=20),
    Optional('default_bitrate'): IntVal(min=500, max=512000),
    Optional('record'): BoolVal(),
    Optional('record_file'): StrVal(),
    Optional('record_dir'): StrVal(),
    Optional('mjrs'): BoolVal(),
    Optional('mjrs_dir'): StrVal(),
    Optional('allow_rtp_participants'): BoolVal(),
    Optional('groups'): ListVal(StrVal(), max_len=JANUS_AUDIOBRIDGE_MAX_GROUPS),

# no these code in janus-gateway except document
#    Optional('rtp_forward_id'): IntVal(min=0),
#    Optional('rtp_forward_host'): StrVal(),
#    Optional('rtp_forward_host_family'): EnumVal(['ipv4', 'ipv6']),
#    Optional('rtp_forward_port'): IntVal(min=0, max=65535),
#    Optional('rtp_forward_ssrc'): IntVal(min=0),
#    Optional('rtp_forward_codec'): EnumVal(['opus', 'pcma', 'pcmu']),
#    Optional('rtp_forward_group'): StrVal(),
#    Optional('rtp_forward_srtp_suite'): IntVal(values=(32, 80)),
#    Optional('rtp_forward_srtp_crypto'): StrVal(),
#    Optional('rtp_forward_always_on'): BoolVal(),

    AutoDel(str): object  # for all other key we don't care
})


room_edit_schema = Schema({
    Optional('new_description'): StrRe('^\w{1,128}$'),
    Optional('new_secret'): StrVal(max_len=256),
    Optional('new_pin'): StrVal(max_len=256),
    Optional('new_is_private'): BoolVal(),
    Optional('new_record_dir'): StrVal(max_len=1024),
    Optional('new_mjrs_dir'): StrVal(max_len=1024),
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

rtp_forward_schema = Schema({
    'host': StrVal(max_len=256),
    'port': IntVal(min=1, max=65535),
    Optional('host_family'): EnumVal(['ipv4', 'ipv6']),
    Optional('ssrc'): IntVal(min=1),
    Optional('ptype'): IntVal(min=1),
    Optional('group'): StrVal(),
    Optional('codec'): EnumVal(['opus', 'pcma', 'pcmu']),
    Optional('srtp_suite'): IntVal(values=[32, 80]),
    Optional('srtp_crypto'): StrVal(),
    Optional('always_on'): BoolVal(),    
    AutoDel(str): object  # for all other key we must delete
})
stop_rtp_forward_schema = Schema({
    'stream_id': IntVal(min=0),
    AutoDel(str): object  # for all other key we must delete
})

play_file_schema = Schema({
    'filename':  StrVal(),
    Optional('file_id'): StrVal(),
    Optional('group'): StrVal(),
    Optional('loop'): BoolVal(),    
    AutoDel(str): object  # for all other key we must delete
})

stop_file_schema = Schema({
    'file_id': StrVal(),  
    AutoDel(str): object  # for all other key we must delete
})

record_schema = Schema({
    'record':  BoolVal(),
    Optional('record_file'): StrVal(),
    Optional('record_dir'): StrVal(),
    AutoDel(str): object  # for all other key we must delete
})

mjrs_schema = Schema({
    'mjrs':  BoolVal(),
    Optional('mjrs_dir'): StrVal(),
    AutoDel(str): object  # for all other key we must delete
})

join_base_schema = Schema({
    'room': IntVal(min=1),
    Optional('pin'): Default(StrVal(max_len=256), default=''),
    Optional('token'): StrVal(max_len=256),
    Optional('id'): IntVal(min=1),
    
    AutoDel(str): object  # for all other key we must delete
})

join_params_schema = Schema({
    Optional('display'): StrVal(max_len=256),
    Optional('muted'): BoolVal(),
    Optional('codec'): EnumVal(['opus', 'pcma', 'pcmu']),
    Optional('prebuffering'): IntVal(min=0, max=MAX_PREBUFFERING),
    Optional('bitrate'): IntVal(min=500, max=512000),
    Optional('quality'): IntVal(min=1, max=10),
    Optional('expected_loss'): IntVal(min=0, max=20),
    Optional('volume'): IntVal(),
    Optional('spatial_position'): IntVal(min=0, max=100),
    Optional('secret'): StrVal(),
    Optional('audio_active_packets'): IntVal(min=1),
    Optional('audio_level_average'): IntVal(min=1, max=127),
    Optional('record'): BoolVal(),
    Optional('filename'): StrVal(),
    Optional('group'): StrVal(),
    Optional('generate_offer'): BoolVal(),
    Optional('rtp'): {
        Optional('ip'): StrVal(),
        Optional('port'): IntVal(min=0, max=65535),
        Optional('payload_type'): IntVal(),
        Optional('audiolevel_ext'): IntVal(min=1),
        Optional('fec'): BoolVal(),  
        AutoDel(str): object  # for all other key we must delete
    },
    AutoDel(str): object  # for all other key we must delete
})

participant_configure_schema = Schema({
    Optional('display'): StrVal(max_len=256),
    Optional('muted'): BoolVal(),
    Optional('prebuffering'): IntVal(min=0, max=MAX_PREBUFFERING),
    Optional('bitrate'): IntVal(min=500, max=512000),
    Optional('quality'): IntVal(min=1, max=10),
    Optional('expected_loss'): IntVal(min=0, max=20),
    Optional('volume'): IntVal(),
    Optional('spatial_position'): IntVal(min=0, max=100),
    Optional('record'): BoolVal(),
    Optional('filename'): StrVal(),
    Optional('group'): StrVal(),
    Optional('generate_offer'): BoolVal(),
    Optional('update'): BoolVal(),
    AutoDel(str): object  # for all other key we must delete
})




_backend_server_mgr = None

def _send_backend_message(backend_handle, body, jsep=None):
    if backend_handle is None:
        raise JanusCloudError('Not connected', JANUS_ERROR_INTERNAL_ERROR)
    data, reply_jsep = backend_handle.send_message(body=body, jsep=jsep)
    if 'error_code' in data:
        raise JanusCloudError(data.get('error', 'unknown'),
                              data.get('error_code', JANUS_AUDIOBRIDGE_ERROR_UNKNOWN_ERROR))

    return data, reply_jsep


class AudioBridgeParticipant(object):

    def __init__(self, user_id, handle, display=''):
        self.user_id = user_id     # Unique ID in the room
        self.display = display     # Display name (just for fun)

        self.room = None      # Room
        self.room_id = 0      # deal later
        self.webrtc_started = False  # webrtc peerconnection is up or not

        self.sdp = ''              # The SDP this publisher negotiated, if any
        self.codec = ''            # Audio codec this publisher is using
        self.audiolevel_ext = False # Audio level RTP extension enabled or not 
        self.talking = False       # Whether this participant is currently talking (uses audio levels extension)
        self.muted = False         # Whether this participant is muted
        self.spatial_position = 50 # Panning of this participant in the mix
        self.mjr_active = False    # Whether this participant has to be recorded to an mjr file or not
        self.plainrtp = False      # Whether this is a WebRTC participant, or a plain RTP one

        self.group = ''       # Forwarding group id, if enabled in the room

        self.user_audio_active_packets = 0  # Participant's number of audio packets to evaluate
        self.user_audio_level_average = 0  # Participant's average level of dBov value

        self.rtp = None
        self.generate_offer = False  # 

        self._frontend_handle = handle

        # backend handle info
        self._backend_handle = None

        self._has_destroyed = False

        self.utime = time.time()
        self.ctime = time.time()

    def destroy(self):
        if self._has_destroyed:
            return
        self._has_destroyed = True

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
            if self._frontend_handle and not self.plainrtp:
                self._frontend_handle.push_event(method='hangup', transaction=None, reason='Close PC')

        if self._frontend_handle:
            self._frontend_handle.on_participant_detach(self)
            self._frontend_handle = None

        log.info('Audiobridge Participant "{0}"({1}) is destroyed'.format(self.user_id, self.display))

    def __str__(self):
        return 'Audiobridge Participant "{0}"({1})'.format(self.user_id, self.display)

    def _assert_valid(self):
        if self._has_destroyed:
            raise JanusCloudError('Audiobridge Participant Already destroyed {} ({})'.format(
                                  self.user_id, self.display),
                                  JANUS_AUDIOBRIDGE_ERROR_ALREADY_DESTROYED)
        
    def join(self, room, server_url, jsep=None, group='', muted=False, codec='opus',
             prebuffer=-1, bitrate=0, quality=DEFAULT_COMPLEXITY, expected_loss=-1,
             volume=100, spatial_position=50, audio_level_average=0, audio_active_packets=0,
             record=False, filename='', rtp=None, generate_offer=False,
             **kwargs):
        self._assert_valid()

        if self._backend_handle is not None:
            raise JanusCloudError('Already construct backend handle {} ({})'.format(self.user_id, self.display),
                                  JANUS_AUDIOBRIDGE_ERROR_ALREADY_BACKEND)
        if self.room is not None:
            raise JanusCloudError('Already in as a participant on this handle',
                                    JANUS_AUDIOBRIDGE_ERROR_ALREADY_JOINED)
        if jsep:
            jsep_type = jsep.get('type')
            if jsep_type == 'offer':
                if generate_offer or self.generate_offer:
                    raise JanusCloudError('Received an offer on a plugin-offered session',
                                    JANUS_AUDIOBRIDGE_ERROR_INVALID_SDP)
            elif jsep_type == 'answer':
                if not self.generate_offer:
                    raise JanusCloudError('Received an answer when we didn\'t send an offer',
                                    JANUS_AUDIOBRIDGE_ERROR_INVALID_SDP)
            else:
                raise JanusCloudError('Unsupported SDP type \'{}\''.format(jsep_type),
                                    JANUS_AUDIOBRIDGE_ERROR_INVALID_SDP) 
        

        # backend session
        backend_session = get_backend_session(server_url, 
                                              auto_destroy=BACKEND_SESSION_AUTO_DESTROY_TIME)

        # attach backend handle
        backend_handle = backend_session.attach_handle(JANUS_AUDIOBRIDGE_PACKAGE, handle_listener=self)
        self._backend_handle = backend_handle
        try:
            # send the join request to backend
            body = {
                'request':  'join',
                'room': room.room_id,
                'id': self.user_id,
            }
            if self.display:
                body['display'] = self.display
            if group:
                body['group'] = group
            if muted:
                body['muted'] = True
            if codec != 'opus':
                body['codec'] = codec
            if prebuffer >= 0:
                body['prebuffer'] = prebuffer
            if bitrate:
                body['bitrate'] = bitrate
            if quality != DEFAULT_COMPLEXITY:
                body['quality'] = quality
            if expected_loss >= 0:
                body['expected_loss'] = expected_loss
            if volume != 100:
                body['volume'] = volume   
            if spatial_position != 50:
                body['spatial_position'] = spatial_position
            if audio_level_average:
                body['audio_level_average'] = audio_level_average
            if audio_active_packets:
                body['audio_active_packets'] = audio_active_packets
            if record:
                body['record'] = True
            if filename:
                body['filename'] = filename
            if rtp is not None:
                body['rtp'] = rtp
            if generate_offer:
                body['generate_offer'] = True

            # other future parameters
            if len(kwargs) > 0:
                for k, v in kwargs.items():
                    if k not in body:
                        body[k] = v

            reply_data, reply_jsep = _send_backend_message(backend_handle, body=body, jsep=jsep)

        except Exception:
            self._backend_handle = None
            backend_handle.detach()
            raise

        # update self properties if success
        self.room = room
        self.room_id = room.room_id

        self.codec = codec
        self.muted = muted
        if room.spatial_audio:
            self.spatial_position = spatial_position
        if rtp is not None:
            self.plainrtp = True
            self.rtp = reply_data.get('rtp')
        self.group = group
        if record or room.mjrs:
            self.mjr_active = True
            log.debug('Starting recording of participant\'s audio  (room {}, user {})'.format(
                      room.room_id, self.user_id))
        self.user_audio_active_packets = audio_active_packets
        self.user_audio_level_average = audio_level_average
        if generate_offer:
            self.generate_offer = True

        if (not self.generate_offer) and reply_jsep and reply_jsep.get('type') == 'answer':
            self.sdp = reply_jsep.get('sdp', '')
            log.debug('Setting sdp property len={} (room {}, user {})'.format(
                len(self.sdp), room.room_id, self.user_id))
            if self.sdp:
                if room.audiolevel_ext and JANUS_RTP_EXTMAP_AUDIO_LEVEL in self.sdp:
                    self.audiolevel_ext = True
                else:
                    self.audiolevel_ext = False

        return reply_jsep

    def configure(self, jsep=None, group='', muted=None, display='', prebuffer=-1,
                  bitrate=0, quality=0, expected_loss=-1, volume=-1, spatial_position=-1,
                  record=None, filename='', generate_offer=False,
                  **kwargs):

        # check param conflict
        self._assert_valid()
        if self._backend_handle is None:
            raise JanusCloudError('Backend handle invalid for publisher {}({})'.format(self.user_id, self.display),
                                  JANUS_AUDIOBRIDGE_ERROR_INVALID_ELEMENT)
        room = self.room
        if room is None:
            raise JanusCloudError('Can\'t configure (not in a room)',
                                  JANUS_AUDIOBRIDGE_ERROR_NOT_JOINED)
        if jsep:
            jsep_type = jsep.get('type')
            if jsep_type == 'offer':
                if generate_offer or self.generate_offer:
                    raise JanusCloudError('Received an offer on a plugin-offered session',
                                    JANUS_AUDIOBRIDGE_ERROR_INVALID_SDP)
            elif jsep_type == 'answer':
                if not self.generate_offer:
                    raise JanusCloudError('Received an answer when we didn\'t send an offer',
                                    JANUS_AUDIOBRIDGE_ERROR_INVALID_SDP)
            else:
                raise JanusCloudError('Unsupported SDP type \'{}\''.format(jsep_type),
                                    JANUS_AUDIOBRIDGE_ERROR_INVALID_SDP)  

        if group and len(room.groups) > 0:
            if group not in room.groups:
                raise JanusCloudError('No such group ({})'.format(group),
                                  JANUS_AUDIOBRIDGE_ERROR_NO_SUCH_GROUP)

        # send the configure request to backend
        body = {
                'request':  'configure',
        }
        if muted is not None:
            body['muted'] = muted
        if self.display:
            body['display'] = self.display
        if group:
            body['group'] = group
        if prebuffer >= 0:
            body['prebuffer'] = prebuffer
        if bitrate:
            body['bitrate'] = bitrate
        if quality:
            body['quality'] = quality
        if expected_loss >= 0:
            body['expected_loss'] = expected_loss
        if volume >= 0:
            body['volume'] = volume   
        if spatial_position >= 0:
            body['spatial_position'] = spatial_position

        if record is not None:
            body['record'] = record
        if filename:
            body['filename'] = filename
 
        if generate_offer:
            body['generate_offer'] = True

        # other future parameters
        if len(kwargs) > 0:
            for k, v in kwargs.items():
                if k not in body:
                    body[k] = v
        reply_data, reply_jsep = _send_backend_message(self._backend_handle, body=body, jsep=jsep)

        # update self's properties if success
        if muted is not None:
            self.muted = muted
            log.info('Setting muted property: {} (room {}, user {})'.format(
                    self.muted, self.room_id, self.user_id
            ))         

        if record is not None:
            if record:
                if self.mjr_active:
                    log.warning('Already recording participant\'s audio (room {}, user {})'.format(
                        self.room_id, self.user_id
                    ))
                else:
                    self.mjr_active = True
                    log.info('Starting recording of participant\'s audio  (room {}, user {})'.format(
                        self.room_id, self.user_id
                    ))                    
            else:
                self.mjr_active = False

        if display:
            self.display = display
            log.info('Setting display property: {} (room {}, user {})'.format(
                    self.display, self.room_id, self.user_id
            ))              
        if room.spatial_audio and spatial_position >= 0:
            self.spatial_position = spatial_position

        if generate_offer:
            self.generate_offer = True

        if group:
            self.group = group
        
        if self.generate_offer:
            if jsep and jsep.get('type') == 'answer':
                self.sdp = jsep.get('sdp', '')
                log.debug('Setting sdp property len={} (room {}, user {})'.format(
                    len(self.sdp), room.room_id, self.user_id))
                if self.sdp:
                    if room.audiolevel_ext and JANUS_RTP_EXTMAP_AUDIO_LEVEL in self.sdp:
                        self.audiolevel_ext = True
                    else:
                        self.audiolevel_ext = False
        else:
            if reply_jsep and reply_jsep.get('type') == 'answer':
                self.sdp = jsep.get('sdp', '')
                log.debug('Setting sdp property len={} (room {}, user {})'.format(
                    len(self.sdp), room.room_id, self.user_id))
                if self.sdp:
                    if room.audiolevel_ext and JANUS_RTP_EXTMAP_AUDIO_LEVEL in self.sdp:
                        self.audiolevel_ext = True
                    else:
                        self.audiolevel_ext = False             

        # notify others this changement
        if muted is not None or display or (room.spatial_audio and spatial_position >= 0):
            if self.room is not None:
                self.room.notify_about_participant(self)
        
        return reply_jsep

    def push_audiobridge_event(self, data):
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


    def resetdecoder(self):
        if self._has_destroyed:
            return

        # send request to backend
        body = {
            'request': 'resetdecoder',
        }
        _send_backend_message(self._backend_handle, body=body)       

    def mute(self, muted):

        if self._has_destroyed:
            return

        if self.muted == muted:
            # If someone trying to mute an already muted user, or trying to unmute a user that is not mute),
			# then we should do nothing */
            return

        # send request to backend
        if muted:
            body = {
                'request':  'mute',
                'room': self.room_id,
                'id': self.user_id,
            }
        else:
            body = {
                'request':  'unmute',
                'room': self.room_id,
                'id': self.user_id,
            }            
        _send_backend_message(self._backend_handle, body=body)    

        self.muted = muted
        log.debug('Setting muted property: {} (room {}, user {})'.format(
                muted, self.room_id, self.user_id))

        if self.room:
            self.room.notify_about_participant(self, notify_source_participant=True)


    def on_async_event(self, handle, event_msg):
        if self._has_destroyed:
            return
    

        if event_msg['janus'] == 'event':
            data = event_msg['plugindata']['data']
            jsep = event_msg.get('jsep')
            op = data.get('audiobridge', '')

            if op == 'talking' or op == 'stopped-talking':

                if self.user_id == data.get('id', 0):
                    # update talking property
                    if op == 'talking':
                        self.talking = True
                    else:
                        self.talking = False
                # pass through the talking event to front handle
                talk_event = data.copy()
                talk_event['id'] = self.user_id
                talk_event['room'] = self.room_id 
                if self._frontend_handle:
                    self._frontend_handle.push_plugin_event(talk_event, jsep)                      
    
            elif op == 'announcement-stopped' or op == 'announcement-started':
                # pass through file announcement event to front handle
                announcement = data.copy()
                announcement['room'] = self.room_id
                if self._frontend_handle:
                    self._frontend_handle.push_plugin_event(announcement, jsep)
            else:
                # ignore other operations
                return
        
        elif event_msg['janus'] == 'hangup':
            log.info('Participant (id:{}, display:{}) PeerConnection hangup'.format(
                    self.user_id, self.display))

            # when media is hangup, destroy self
            self.destroy()

        else:
            # pass through other webrtc notificaiton to frontend handle
            if event_msg['janus'] == 'webrtcup':
                # webrtc pc is up
                self.webrtc_started = True
                log.info('Participant (id:{}, display:{}) PeerConnection startup'.format(
                        self.user_id, self.display))
                if self.room:
                    self.room.notify_about_participant(self)

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
            self.push_audiobridge_event(kick_event)

        self.destroy()

class AudioBridgeRoom(object):

    def __init__(self, room_id, backend_admin_key='',
                 description='', secret='', pin='', is_private=False, allowed=None,
                 sampling_rate=16000, spatial_audio=False,
                 audiolevel_ext=True, audiolevel_event=False, 
                 audio_active_packets=100, audio_level_average=25,
                 default_prebuffering=DEFAULT_PREBUFFERING, default_expectedloss=0, default_bitrate=0,
                 record=False, record_file='', record_dir='',
                 mjrs=False, mjrs_dir='',
                 allow_rtp_participants=False, groups=[],
                 utime=None, ctime=None):

        # public property
        self.room_id = room_id                   # Unique room ID
        
        self.description = description           # Room description
        if self.description == '':
            self.description = 'Room {}'.format(room_id)
        self.secret = secret                     # Secret needed to manipulate (e.g., destroy) this room
        self.pin = pin                           # Password needed to join this room, if any
        self.is_private = is_private             # Whether this room is 'private' (as in hidden) or not

        self.check_allowed = False               # Whether to check tokens when participants join, default is False
        if allowed is None:
            self.allowed = set()                 # Map of participants (as tokens) allowed to join
        else:
            self.allowed = set(allowed)
            self.check_allowed = True            # if allowed is given in params, enable this room check allow by default

        self.sampling_rate = sampling_rate       # Sampling rate of the mix (e.g., 16000 for wideband; can be 8, 12, 16, 24 or 48kHz)
        self.spatial_audio = spatial_audio       # Whether the mix will use spatial audio, using stereo

        self.audiolevel_ext = audiolevel_ext     # Whether the ssrc-audio-level extension must be negotiated or not for new joins
        self.audiolevel_event = audiolevel_event            # Whether to emit event to other users about audiolevel
        self.audio_active_packets = audio_active_packets    # Amount of packets with audio level for checkup
        self.audio_level_average = audio_level_average      # Average audio level

        self.default_prebuffering = default_prebuffering    # Number of packets to buffer before decoding each participant
        self.default_expectedloss = default_expectedloss    # Percent of packets we expect participants may miss, to help with FEC: can be overridden per-participant
        self.default_bitrate = default_bitrate   # Default bitrate to use for all Opus streams when encoding

        self.record = record                     # Whether this room has to be recorded or not
        self.record_file = record_file           # Path of the recording file (absolute or relative, depending on record_dir)
        self.record_dir = record_dir             # Folder to save the recording file to

        self.mjrs = mjrs                         # Whether all participants in the room should be individually recorded to mjr files or not
        self.mjrs_dir = mjrs_dir                 # Folder to save the mjrs file to

        self.allow_rtp_participants = \
            allow_rtp_participants               # Whether plain RTP participants are allowed
        
        self.groups = groups[:JANUS_AUDIOBRIDGE_MAX_GROUPS] # Forwarding groups supported in this room

        self.muted = False

        #internal properties
        self._participants = {}                  # Map of potential publishers (we get subscribers from them)
        self._creating_user_id = set()           # user_id which are creating
        self._backend_handle = None              # handle to control the backend room
        self._backend_server_url = ''
        self._rtp_forwarders = {}

        self._has_destroyed = False

        self._backend_admin_key = backend_admin_key
        self._lock = BoundedSemaphore()

        self.idle_ts = get_monotonic_time()

        if utime is None:
            self.utime = time.time()
        else:
            self.utime = utime

        if ctime is None:
            self.ctime = time.time()
        else:
            self.ctime = ctime

    def __str__(self):
        return 'Audiobridge Room-"{0}"({1})'.format(self.room_id, self.description)

    def _assert_valid(self):
        if self._has_destroyed:
            raise JanusCloudError('No such room ({})'.format(self.room_id),
                                  JANUS_AUDIOBRIDGE_ERROR_NO_SUCH_ROOM)

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

        self._participants.clear()
        self._creating_user_id.clear()

        # Notify all participants that the fun is over, and that they'll be kicked
        log.debug("Audiobridge Room {} is destroyed, Notifying all participants".format(
            self.room_id)
        )
        destroyed_event = {
            'audiobridge': 'destroyed',
            'room': self.room_id,
        }
        # log.debug("after clear, len of participants is {}".format(len(participants)))
        for participant in participants:
            participant.room = None    # already removed from room, no need to call back room's on_participant destroy()
            participant.room_id = 0
            # log.debug('destroy publisher user_id {}'.format(publisher.user_id))
            participant.push_audiobridge_event(destroyed_event)

        # destroy the backend room and handle
        if self._backend_handle:
            backend_handle = self._backend_handle
            self._backend_handle = None
            self._backend_server_url = ''
            try:
                _send_backend_message(backend_handle, {
                    'request':  'destroy',
                    'room': self.room_id,
                })
            except Exception as e:
                log.exception('Backend room "{}"({}) failed to destroyed: {}, ignore'.
                        format(self.backend_room_id, self.server_url, str(e)))             
                pass   # ignore destroy failed
            backend_handle.detach()
            
        log.info("Audiobridge room {} is destroyed".format(self.room_id))

    def update(self):
        self.utime = time.time()

    def edit(self, new_description=None, new_secret=None, new_pin=None, new_is_private=None,
             new_record_dir=None, new_mjrs_dir=None):

        # check and recreate the backend room if needed
        backend_handle = self.activate_backend_room()

        if new_record_dir is not None or new_mjrs_dir is not None:
            # update the backend room
            body = {
                'request':  'edit',
                'room': self.room_id,
                'permanent': False     
            }
            if new_record_dir is not None:
                body['new_record_dir'] = new_record_dir
            if new_mjrs_dir is not None:
                body['new_mjrs_dir'] = new_mjrs_dir
            _send_backend_message(backend_handle, body)

        if new_description is not None and len(new_description) > 0:
            self.description = new_description
        if new_secret is not None:
            self.secret = new_secret
        if new_pin is not None:
            self.pin = new_pin
        if new_is_private is not None:
            self.is_private = new_is_private
        if new_record_dir is not None:
            self.record_dir = new_record_dir

        if new_mjrs_dir is not None:
            self.mjrs_dir = new_mjrs_dir

        self.update()

    def activate_backend_room(self):
        self._assert_valid()
        # check the handle ative
        if self._backend_handle:
            return self._backend_handle # already active

        if _backend_server_mgr is None:
            raise JanusCloudError('backend_server_mgr not configured',
                                  JANUS_ERROR_BAD_GATEWAY)      
        
        if self._lock.acquire(timeout=10.0) == False:
            raise JanusCloudError('backend audiobridge room timout',
                                  JANUS_ERROR_GATEWAY_TIMEOUT)    
        backend_handle = None              
        try:
            if self._backend_handle:
                return self._backend_handle # other greenlet active  

            # choose backend server
            backend_server = _backend_server_mgr.choose_server()
            if backend_server is None:
                raise JanusCloudError('No backend server available', JANUS_ERROR_BAD_GATEWAY)   
            
            # 1. create the backend handle
            backend_session = get_backend_session(backend_server.url, 
                                                auto_destroy=BACKEND_SESSION_AUTO_DESTROY_TIME)
            backend_handle = backend_session.attach_handle(JANUS_AUDIOBRIDGE_PACKAGE, 
                opaque_id=backend_server.name,
                handle_listener=self)
            
            self._backend_handle = backend_handle
            self._backend_server_url = backend_server.url

            # 2. create the backend room
            while(True):
                try:
                    self._create_backend_room(backend_handle)
                except Exception as e:
                    if e.code == JANUS_AUDIOBRIDGE_ERROR_ROOM_EXISTS:
                        # the room already exist, destroy it and re-create
                        destroy = {
                            'request':  'destroy',
                            'room': self.room_id,
                        }
                        if self.room_id == 1234:
                            # May conflict with the default config of janus-gateway,
                            # work aroud with a hack secret
                            destroy['secret'] = 'adminpwd'
                        _send_backend_message(backend_handle, destroy)
                    else:
                        raise 
                else:
                    break  # create successfully

            # 3. join the backend room as a fake participant
            #    The fake participant is used to avoid the backend room automatic deletion
            _send_backend_message(backend_handle, {
                'request':  'join',
                'room': self.room_id,
                'display': 'januscloud control handle',
                'muted': True
            })

            return backend_handle
        except Exception as e:
            self._backend_handle = None
            self._backend_server_url = ''
            if backend_handle:
                backend_handle.detach()

            raise # re-raise exception

        finally:
            self._lock.release()

    def _create_backend_room(self, backend_handle):

        body = {
            'request':  'create',
            'room': self.room_id,
            'description': 'januscloud-{}'.format(self.room_id),
            'permanent': False,
            'is_private': False,
            'sampling_rate': self.sampling_rate,
            'spatial_audio': self.spatial_audio,
            'audiolevel_ext': self.audiolevel_ext,
            'audiolevel_event': self.audiolevel_event,
            'audio_active_packets': self.audio_active_packets,
            'audio_level_average': self.audio_level_average,
            'default_prebuffering': self.default_prebuffering,
            'default_expectedloss': self.default_expectedloss,
            'default_bitrate': self.default_bitrate,
            'record': self.record,
            'record_file': self.record_file,
            'record_dir': self.record_dir,
            'mjrs': self.mjrs,
            'mjrs_dir': self.mjrs_dir,
            'allow_rtp_participants': self.allow_rtp_participants,
                    
        }
        if self.description:
            body['description'] = 'januscloud-{}'.format(self.description)
        if self.groups:
            body['groups'] = self.groups
        if self._backend_admin_key:
            body['admin_key'] = self._backend_admin_key

        _send_backend_message(backend_handle, body)

    def new_participant(self, user_id, handle, jsep=None, group='', display='', 
                        secret='',
                        rtp=None, 
                        **other_join_params):
        if handle is None:
            raise JanusCloudError('handle invalid', JANUS_AUDIOBRIDGE_ERROR_INVALID_REQUEST)
        self._assert_valid()

        if rtp is not None and (not self.allow_rtp_participants):
            raise JanusCloudError('Plain RTP participants not allowed in this room',
                                  JANUS_AUDIOBRIDGE_ERROR_UNAUTHORIZED)    
        
        if len(self.groups) >0:
            if not group:
                raise JanusCloudError('group is mandatory ',
                                    JANUS_AUDIOBRIDGE_ERROR_INVALID_REQUEST)                   
            if group not in self.groups:
                raise JanusCloudError('No such group {}'.format(group),
                                    JANUS_AUDIOBRIDGE_ERROR_NO_SUCH_GROUP)      

        # get id
        if user_id == 0:
            user_id = random_uint64()
            while user_id in self._participants or user_id in self._creating_user_id:
                user_id = random_uint64()
        else:
            if user_id in self._participants or user_id in self._creating_user_id:
                raise JanusCloudError('User ID {} already exists'.format(user_id),
                                      JANUS_AUDIOBRIDGE_ERROR_ID_EXISTS)

        log.debug('  -- Participant ID:: {}'.format(user_id))


        new_participant = AudioBridgeParticipant(user_id=user_id, handle=handle,
                                               display=display)
        log.info('A new participant (id:{}, display:{}) is created on handle {}'.format(
            new_participant.user_id, new_participant.display, handle.handle_id))

        try:
            self._creating_user_id.add(user_id)

            # activate backend room
            self.activate_backend_room() 

            # join the room
            reply_jsep = new_participant.join(
                room=self, 
                server_url=self._backend_server_url,
                jsep=jsep,
                group=group,
                rtp=rtp,
                **other_join_params)

        except Exception:
            new_participant.room = None
            new_participant.room_id = 0    
            self._creating_user_id.discard(user_id)       
            new_participant.destroy()
            raise

        # add to the room
        self._participants[user_id] = new_participant
        self._creating_user_id.discard(user_id)

        self.check_idle()

        # notify other new participant join
        self.notify_about_participant(new_participant, audiobridge='joined') 

        return new_participant, reply_jsep

    def get_participant_by_user_id(self, user_id):
        return self._participants.get(user_id)

    def set_backend_admin_key(self, backend_admin_key):
        self._backend_admin_key =  backend_admin_key

    def user_id_exists(self, user_id):
        return user_id in self._participants

    def list_participants(self):
        return list(self._participants.values())

    def num_participants(self):
        return len(self._participants)

    def kick_participant(self, participant_id):
        self._assert_valid()

        participant = self._participants.get(participant_id, None)
        if participant is None:
            raise JanusCloudError('No such user {} in room {}'.format(participant_id, self.room_id),
                                  JANUS_AUDIOBRIDGE_ERROR_NO_SUCH_USER)

        # Notify all participants about the kick
        event = {
            'audiobridge': 'event',
            'room': self.room_id,
            'kicked': participant_id
        }
        self.notify_other_participants(None, event)

        participant.destroy()

        self.check_idle()

        log.debug('Kicked user {} from audiobridge room {}'.format(participant_id, self.room_id))

    def kick_all(self):
        self._assert_valid()

        participant_list = list(self._participants.values())
        for participant in participant_list:

            event = {
                'audiobridge': 'event',
                'room': self.room_id,
                'kicked_all': participant.user_id
            }            
            try:
                participant.push_audiobridge_event(event)
            except Exception as e:
                log.warning('Notify participant {} ({}) of audiobridge room {} Failed:{}'.format(
                    participant.user_id, participant.display, self.room_id, e))
                pass     # ignore errors during push event to each publisher

            participant.destroy()

            log.debug('Kicked user {} from audiobridge room {}'.format(
                participant.user_id, self.room_id))


    def on_participant_destroy(self, participant_id):
        participant = self._participants.pop(participant_id, None)
        if participant is None:
            return  # already removed

        event = {
            'audiobridge': 'event',
            'room': self.room_id,
            'leaving': participant_id
        }
        self.notify_other_participants(participant, event)

        self.check_idle()

    def notify_about_participant(self, participant, audiobridge='event', notify_source_participant=False):

        pl = {
            'id': participant.user_id,
            'setup': participant.webrtc_started,
            'muted': participant.muted,
        }
        if self.spatial_audio:
            pl['spatial_position'] = participant.spatial_position
        if participant.display:
            pl['display'] = participant.display
        event = {
            'audiobridge': audiobridge,
            'room': self.room_id,
            'participants': [pl]
        }
        if notify_source_participant:
            self.notify_other_participants(None, event)
        else:
            self.notify_other_participants(participant, event)

    def notify_other_participants(self, src_participant, event):
        
        if self._has_destroyed: # if destroyed, just return
            return

        participant_list = list(self._participants.values())
        for participant in participant_list:
            if participant != src_participant:
                try:
                    # log.debug('Notifying participant {} ({})'.format(participant.user_id, participant.display))
                    participant.push_audiobridge_event(event)
                except Exception as e:
                    log.warning('Notify participant {} ({}) of audiobridge room {} Failed:{}'.format(
                        participant.user_id, participant.display, self.room_id, e))
                    pass     # ignore errors during push event to each publisher

    def enable_allowed(self):
        log.debug('Enabling the check on allowed authorization tokens for audiobridge room {}'.format(self.room_id))
        self.check_allowed = True
        self.update()

    def disable_allowed(self):
        log.debug('Disabling the check on allowed authorization tokens for audiobridge room {}'.format(self.room_id))
        self.check_allowed = False
        self.update()

    def add_allowed(self, allowed=[]):
        self.allowed.update(allowed)
        self.update()

    def remove_allowed(self, allowed=[]):
        self.allowed.difference_update(allowed)
        self.update()

    def enable_recording(self, record, record_file=None, record_dir=None):
        
        # check and recreate the backend room if needed
        backend_handle = self.activate_backend_room()

        # send eanble_recording request to backend room
        body = {
            'request':  'enable_recording',
            'room': self.room_id,
            'record': record     
        }
        if record_file is not None:
            body['record_file'] = record_file
        if record_dir is not None:
            body['record_dir'] = record_dir
        _send_backend_message(backend_handle, body)
        
        # update self properties
        if self.record != record:
            log.debug('Room {} recording status changed: prev={}, curr={}'.format(
                self.room_id, self.record, record))

            self.record = record

            if record_dir is not None and record:
                self.record_dir = record_dir
                log.debug('Recording folder: {}'.format(record_dir))
            
            if record_file is not None and record:
                self.record_file = record_file
                log.debug('Recording file: {}'.format(record_file))


    def enable_mjrs(self, mjrs, mjrs_dir=None):
        
        # check and recreate the backend room if needed
        backend_handle = self.activate_backend_room()

        # send enable_mjrs request to backend room
        body = {
            'request':  'enable_mjrs',
            'room': self.room_id,
            'mjrs': mjrs     
        }
        if mjrs_dir is not None:
            body['mjrs_dir'] = mjrs_dir

        _send_backend_message(backend_handle, body)
        
        # update self properties
        self.mjrs = mjrs

        if mjrs_dir is not None and mjrs:
            self.mjrs_dir = mjrs_dir
        
        # Iterate over all participants 
        participant_list = list(self._participants.values())
        for participant in participant_list:
            participant.mjr_active = mjrs
            log.info('Setting MJR recording property {} (room {}, user {})'.format(
                     mjrs, self.room_id, participant.user_id
            ))   

    def mute_room(self, muted):

        # check and recreate the backend room if needed
        backend_handle = self.activate_backend_room()

        # send mute request to backend room
        if muted:
            body = {
                'request':  'mute_room',
                'room': self.room_id,
            }
        else:
            body = {
                'request':  'unmute_room',
                'room': self.room_id,
            }            
        _send_backend_message(backend_handle, body)
        
        # update self properties
        self.muted = muted

         # Notify all participants 
        event = {
            'audiobridge': 'event',
            'room': self.room_id,
            'muted': self.muted
        }
        self.notify_other_participants(None, event)       

    
    def play_file(self, filename, file_id='', group='', loop=False):
        # check and recreate the backend room if needed
        backend_handle = self.activate_backend_room()
        body = {
            'request':  'play_file',
            'room': self.room_id,
            'filename': filename
        }
        if file_id:
            body['file_id'] = file_id
        if group:
            body['group'] = group
        if loop:
            body['loop'] = loop

        data, reply_jsep = _send_backend_message(backend_handle, body)   

        return data.get('file_id', '')    

    def stop_file(self, file_id):
        # check and recreate the backend room if needed
        backend_handle = self.activate_backend_room()
        body = {
            'request':  'stop_file',
            'room': self.room_id,
            'file_id': file_id
        }
        _send_backend_message(backend_handle, body)   

    def is_playing(self, file_id):
        # check and recreate the backend room if needed
        backend_handle = self.activate_backend_room()

        body = {
            'request':  'is_playing',
            'room': self.room_id,
            'file_id': file_id
        }
        data, reply_jsep = _send_backend_message(backend_handle, body)   

        return data.get('playing', False)    


    def rtp_forward(self, host: str, port: int,
                    host_family='', group='',
                    ssrc=0, codec='opus', ptype=100, 
                    srtp_suite=0, srtp_crypto='',
                    always_on=False,
                    **kwargs):

        backend_handle = self.activate_backend_room()

        if len(self.groups) > 0 and group:
            if group not in self.groups:
                raise JanusCloudError('No such group {}'.format(group),
                                    JANUS_AUDIOBRIDGE_ERROR_NO_SUCH_GROUP)  

        # send request to backend
        body = {
            'request': 'rtp_forward',
            'room': self.room_id,
            'host': host,
            'port': port,
            'codec': codec,
            'ptype': ptype,
        }
        if self._backend_admin_key:
            body['admin_key'] = self._backend_admin_key
        if host_family:
            body['host_family'] = host_family
        if group:
            body['group'] = group
        if srtp_suite:
            body['srtp_suite'] = srtp_suite
        if srtp_crypto:
            body['srtp_crypto'] = srtp_crypto
        if always_on:
            body['always_on'] = True
        # other future parameters
        if len(kwargs) > 0:
            for k, v in kwargs.items():
                if k not in body:
                    body[k] = v

        reply_data, reply_jsep = _send_backend_message(backend_handle, body=body)
        stream_id = reply_data.get('stream_id', 0)
        forwarder = {
            'stream_id': stream_id,
            'ip': reply_data.get('host', ''),
            'port': reply_data.get('port', 0),
            'codec': codec,
            'ptype': ptype,
            'always_on': always_on
        }
        if ssrc:
            forwarder['ssrc'] = ssrc
        else:
            forwarder['ssrc'] = stream_id
        if group:
            forwarder['group'] = group
        if srtp_suite and srtp_crypto:
            forwarder['srtp'] = True
        self._rtp_forwarders[stream_id] = forwarder

        return forwarder    

    def stop_rtp_forward(self, stream_id):

        backend_handle = self.activate_backend_room()

        #if stream_id not in self._rtp_forwarders:
        #     raise JanusCloudError('No such stream ({})'.format(stream_id),
        #                           JANUS_AUDIOBRIDGE_ERROR_NO_SUCH_FEED)

        # send request to backend
        body = {
            'request': 'stop_rtp_forward',
            'room': self.room_id,
            'stream_id': stream_id
        }
        if self._backend_admin_key:
            body['admin_key'] = self._backend_admin_key

        _send_backend_message(backend_handle, body=body)

        self._rtp_forwarders.pop(stream_id, None)

    def rtp_forwarder_list(self):
        self._assert_valid()
        return list(self._rtp_forwarders.values())

    def check_modify(self, secret):
        if self.secret and self.secret != secret:
            raise JanusCloudError('Unauthorized (wrong {})'.format('secret'),
                                  JANUS_AUDIOBRIDGE_ERROR_UNAUTHORIZED)
        return self

    def check_join(self, pin):
        if self.pin and self.pin != pin:
            raise JanusCloudError('Unauthorized (wrong {})'.format('pin'),
                                  JANUS_AUDIOBRIDGE_ERROR_UNAUTHORIZED)
        return self

    def check_token(self, token):
        if self.check_allowed and token not in self.allowed:
            raise JanusCloudError('Unauthorized (not in the allowed list)',
                                  JANUS_AUDIOBRIDGE_ERROR_UNAUTHORIZED)
        return self

    def check_max_publishers(self):
        return self


    def on_async_event(self, handle, event_msg):
        pass # no async event need to process

        if self._has_destroyed:
            return

        if event_msg['janus'] == 'event':
            data = event_msg['plugindata']['data']
            jsep = event_msg.get('jsep')
            op = data.get('audiobridge', '')

            if op == 'event':
                if ('participants' in data) :
                    if self.allow_rtp_participants:
                        participants_list = data['participants']
                        for pl in participants_list:
                            participant = self._participants.get(pl.get('id', 0))
                            if participant is None or (not participant.plainrtp):
                                continue
                            participant.webrtc_started = pl.get('setup', False)


    def on_close(self, handle):
        if self._has_destroyed:
            return

        log.warning('Backend handle of audiobridge room {} is closed abnormally'.format(
            self.room_id))

        self._backend_handle = None # deactivate the backend room
        self._backend_server_url = ''

        self._rtp_forwarders.clear()  # all rtp forwarders would be gone

class AudioBridgeRoomManager(object):

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
                                  JANUS_AUDIOBRIDGE_ERROR_UNKNOWN_ERROR)
        if self._admin_key:
            if admin_key == '':
                raise JanusCloudError('Need admin key for creating room',
                                      JANUS_AUDIOBRIDGE_ERROR_MISSING_ELEMENT)
            if admin_key != self._admin_key:
                raise JanusCloudError('Unauthorized (wrong {})'.format('admin_key'),
                                      JANUS_AUDIOBRIDGE_ERROR_UNAUTHORIZED)

        if room_id == 0:
            log.warning('Desired room ID is empty, which is not allowed... picking random ID instead')
            room_id = random_uint64()
            while room_id in self._rooms_map:
                room_id = random_uint64()
        if room_id in self._rooms_map:
            raise JanusCloudError('Room {} already exists'.format(room_id),
                                  JANUS_AUDIOBRIDGE_ERROR_ROOM_EXISTS)

        try:
            self._rooms_map[room_id] = None   # avoid re-allocate room_id
            new_room = AudioBridgeRoom(room_id=room_id, 
                                 backend_admin_key=self._admin_key,
                                 **room_params)
            self._rooms_map[room_id] = new_room
        except Exception as e:
            self._rooms_map.pop(room_id, None)
            raise
        if not new_room.is_private:
            self._public_rooms_list.append(new_room)


        # debug print the new room info
        log.info('Created AudioBridge room: {0} ({1}, private: {2}, secret: {3}, pin: {4})'.format(
            new_room.room_id, new_room.description, new_room.is_private,
            new_room.secret, new_room.pin
        ))
        if new_room.record:
            log.info('  -- Room is going to be recorded in {}'.format(new_room.rec_dir))


        saved = False
        if permanent and self._room_dao is not None:
            try:

                log.debug('Saving room {} permanently to DB'.format(new_room.room_id))

                self._room_dao.add(new_room)
                saved = True

            except Exception as e:
                log.warning('Fail to add room to DB: {}'.format(e))

        return new_room, saved

    def update(self, room_id, secret='', permanent=False,
               new_description=None, new_secret=None, new_pin=None, new_is_private=None,
               new_record_dir=None, new_mjrs_dir=None):
        if permanent and self._room_dao is None:
            raise JanusCloudError('permanent not support',
                                  JANUS_AUDIOBRIDGE_ERROR_INVALID_REQUEST)

        room = self.get(room_id).check_modify(secret)

        room.edit(
            new_description=new_description, new_secret=new_secret, 
            new_pin=new_pin, new_is_private=new_is_private,    
            new_record_dir=new_record_dir, new_mjrs_dir=new_mjrs_dir)

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
                                  JANUS_AUDIOBRIDGE_ERROR_NO_SUCH_ROOM)
        return room

    def exists(self, room_id):
        return room_id in self._rooms_map

    def destroy(self, room_id, secret='', permanent=False):
        if permanent and self._room_dao is None:
            raise JanusCloudError('permanent not support',
                                  JANUS_AUDIOBRIDGE_ERROR_UNKNOWN_ERROR)
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
                                      JANUS_AUDIOBRIDGE_ERROR_UNAUTHORIZED)
            else:
                room_list = list(self._rooms_map.values())

        return room_list[offset:(offset+limit)]

    def load_from_config(self, rooms_config=[]):
        for room_config in rooms_config:
            room_id = room_config.get('room_id', 0)
            room_params = room_params_schema.validate(room_config)
            room = self._rooms_map.get(room_id)
            if room is None:
                self.create(room_id=room_id,
                            permanent=False,
                            admin_key=self._admin_key,
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
            room.set_backend_admin_key(self._admin_key)
            self._rooms_map[room.room_id] = room
            if not room.is_private:
                self._public_rooms_list.append(room)
        log.info('Audiobridge rooms are loaded from DB ({}) successfully, total {} rooms'.format(
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
                        log.info('Audiobridge room {} timeout for auto cleanup'.format(room.room_id))   
                        room.destroy()                                            
                    except Exception as e:
                        log.warning('Failed to destroy the empty Audiobridge room "{}": {}'.format(room.room_id, e))

                if self._room_dao is not None:
                    try:
                        self._room_dao.del_by_list(cleanup_rooms)
                    except Exception as e:
                        log.warning('Failed to delete the empty Audiobridge rooms from DB: {}'.format(e))

                cleanup_rooms.clear()
                delta_time = get_monotonic_time() - now
                if delta_time < ROOM_CLEANUP_CHECK_INTERVAL:
                    gevent.sleep(ROOM_CLEANUP_CHECK_INTERVAL - delta_time)
            else:
                # session timeout check is disable, just None loop
                gevent.sleep(ROOM_CLEANUP_CHECK_INTERVAL)

class AudioBridgeHandle(FrontendHandleBase):

    def __init__(self, handle_id, session, plugin, opaque_id=None, *args, **kwargs):
        super().__init__(handle_id, session, plugin, opaque_id, *args, **kwargs)

        self._pending_candidates = []

        self._room_mgr = plugin.room_mgr

        # self.webrtc_started = False

        self.participant_type = JANUS_AUDIOBRIDGE_P_TYPE_NONE
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
            self.participant_type = JANUS_AUDIOBRIDGE_P_TYPE_NONE
            participant.destroy()

        self._pending_candidates.clear()

    def handle_hangup(self):
        # log.debug('handle_hangup for videoroom Handle {}'.format(self.handle_id))

        if self.participant_type == JANUS_AUDIOBRIDGE_P_TYPE_PARTICIPANT:
            self.participant.hangup()
        elif self.participant_type == JANUS_AUDIOBRIDGE_P_TYPE_LISTENER:
            self.participant.hangup()

    def handle_trickle(self, candidate=None, candidates=None):
        # log.debug('handle_trickle for videoroom handle {}.candidate:{} candidates:{}'.
        #           format(self.handle_id, candidate, candidates))

        if self.participant_type == JANUS_AUDIOBRIDGE_P_TYPE_NONE:
            # not join yet, buffer candidates
            if candidates:
                self._pending_candidates.extend(candidates)
            if candidate:
                self._pending_candidates.append(candidate)

        elif self.participant_type == JANUS_AUDIOBRIDGE_P_TYPE_PARTICIPANT:
            self.participant.trickle(candidate=candidate, candidates=candidates)
        elif self.participant_type == JANUS_AUDIOBRIDGE_P_TYPE_LISTENER:
            self.participant.trickle(candidate=candidate, candidates=candidates)

    def handle_message(self, transaction, body, jsep=None):
        # log.debug('handle_message for videoroom handle {}. transaction:{} body:{} jsep:{}'.
        #         format(self.handle_id, transaction, body, jsep))
        result = None
        try:
            request = body.get('request')
            if request is None:
                raise JanusCloudError('Request {}  format invalid'.format(body), JANUS_AUDIOBRIDGE_ERROR_INVALID_ELEMENT)
            if request in ('create', 'edit', 'destroy', 'list',  'exists', 
                           'allowed', 'kick', 'kick_all', 'listparticipants', 
                           'listforwarders', 'rtp_forward', 'stop_rtp_forward', 
                           'enable_recording', 'enable_mjrs',
                           'mute', 'unmute', 'mute_room', 'unmute_room',
                           'resetdecoder',
                           'play_file', 'is_playing', 'stop_file'):

                result = self._handle_sync_message(transaction, body, jsep)

            elif request in ('join', 'configure',
                             'changeroom', 'leave', 'hangup'):

                self._enqueue_async_message(transaction, body, jsep)
                return JANUS_PLUGIN_OK_WAIT, None
            else:
                raise JanusCloudError('Unknown request {}'.format(body),
                                      JANUS_AUDIOBRIDGE_ERROR_INVALID_REQUEST)

        except JanusCloudError as e:
            log.exception('Fail to handle message ({}) for handle {}'.format(body, self.handle_id))
            type, dummy, tb = sys.exc_info()
            tb_list = traceback.format_list(traceback.extract_tb(tb)[-10:])
            result = {
                'audiobridge': 'event',
                'error_code': e.code,
                'error': str(e),
                'traceback': tb_list
            }
        except SchemaError as e:
            log.exception('invalid message format ({}) for handle {}'.format(body, self.handle_id))
            type, dummy, tb = sys.exc_info()
            tb_list = traceback.format_list(traceback.extract_tb(tb)[-10:])
            result = {
                'audiobridge': 'event',
                'error_code': JANUS_AUDIOBRIDGE_ERROR_INVALID_ELEMENT,
                'error': str(e),
                'traceback': tb_list
            }
        except Exception as e:
            log.exception('Fail to handle message ({}) for handle {}'.format(body, self.handle_id))
            type, dummy, tb = sys.exc_info()
            tb_list = traceback.format_list(traceback.extract_tb(tb)[-10:])
            result = {
                'audiobridge': 'event',
                'error_code': JANUS_AUDIOBRIDGE_ERROR_UNKNOWN_ERROR,
                'error': str(e),
                'traceback': tb_list
            }

        return JANUS_PLUGIN_OK, result

    def _handle_sync_message(self, transaction, body, jsep=None):
        result = None

        request = body.get('request')

        if request == 'create':
            log.debug('Creating a new AudioBridge room')
            room_base_info = room_base_schema.validate(body)
            admin_key = body.get('admin_key', '')
            room_params = room_params_schema.validate(body)
            new_room, saved = self._room_mgr.create(room_id=room_base_info['room'],
                                                    permanent=room_base_info['permanent'],
                                                    admin_key=admin_key,
                                                    room_params=room_params)
            result = {
                'audiobridge': 'created',
                'room': new_room.room_id,
                'permanent': saved
            }
        elif request == 'edit':
            log.debug('Attempt to edit an existing AudioBridge room')
            room_base_info = room_base_schema.validate(body)
            room_new_params = room_edit_schema.validate(body)
            room, saved = self._room_mgr.update(room_id=room_base_info['room'],
                                                secret=room_base_info['secret'],
                                                permanent=room_base_info['permanent'],
                                                **room_new_params)
            result = {
                'audiobridge': 'edited',
                'room': room_base_info['room'],
                'permanent': saved
            }
        elif request == 'destroy':
            log.debug('Attempt to destroy an existing AudioBridge room')
            room_base_info = room_base_schema.validate(body)
            saved = self._room_mgr.destroy(room_id=room_base_info['room'],
                                           secret=room_base_info['secret'],
                                           permanent=room_base_info['permanent'])
            result = {
                'audiobridge': 'destroyed',
                'room': room_base_info['room'],
                'permanent': saved
            }
        elif request == 'list':
            log.debug('Request for the list for all audiobridge rooms')
            room_list_params = room_list_schema.validate(body)
            room_list = self._room_mgr.list(**room_list_params)

            room_info_list = []
            for room in room_list:
                room_info = {
                    'room': room.room_id,
                    'description': room.description,
                    'record': room.record,
                    'num_participants': room.num_participants(),
                    'sampling_rate': room.sampling_rate,
                    'spatial_audio': room.spatial_audio,
                    'pin_required': bool(room.pin),
                    'muted': room.muted,
                }
                room_info_list.append(room_info)

            result = {
                'audiobridge': 'success',
                'list': room_info_list,
            }
        elif request == 'exists':
            room_base_info = room_base_schema.validate(body)
            result = {
                'audiobridge': 'success',
                'room': room_base_info['room'],
                'exists': self._room_mgr.exists(room_base_info['room'])
            }

        elif request == 'allowed':
            log.debug('Attempt to edit the list of allowed participants in an existing AudioBridge room')
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
                                      JANUS_AUDIOBRIDGE_ERROR_INVALID_ELEMENT)
            result = {
                'audiobridge': 'success',
                'room': room_base_info['room']
            }
            if allowed_params['action'] != 'disable':
                result['allowed'] = list(room.allowed)

            log.debug('AudioBridge room allowed list updated')

        elif request == 'kick':
            log.debug('Attempt to kick a participant from an existing AudioBridge room')
            room_base_info = room_base_schema.validate(body)
            kick_params = kick_schema.validate(body)
            room = self._room_mgr.get(room_base_info['room']). \
                check_modify(room_base_info['secret'])
            room.kick_participant(kick_params['id'])
            result = {
                'audiobridge': 'success'
            }

        elif request == 'kick_all':
            log.debug('Attempt to kick all participants from an existing AudioBridge room')
            room_base_info = room_base_schema.validate(body)
            kick_params = kick_schema.validate(body)
            room = self._room_mgr.get(room_base_info['room']). \
                check_modify(room_base_info['secret'])
            room.kick_all()
            result = {
                'audiobridge': 'success'
            }
        elif request == 'mute' or request == 'unmute':

            room_base_info = room_base_schema.validate(body)
            user_id = body.get('id', 0)
            room = self._room_mgr.get(room_base_info['room']).check_modify(room_base_info['secret'])            
            participant = room.get_participant_by_user_id(user_id)
            if participant is None:
                raise JanusCloudError("No such user {} in room {}".format(user_id, room.room_id),
                                      JANUS_AUDIOBRIDGE_ERROR_NO_SUCH_USER)
            muted = (request == 'mute')
            mute_str = 'mute' if muted else 'unmute'
            log.debug('Attempt to {} a participant {} in an existing AudioBridge room {}'.format(
                mute_str,
                user_id,
                room.room_id))
            participant.mute(muted)

            result = {
                'audiobridge': 'success',
                'room': room.room_id,
            }            

        elif request == 'mute_room' or request == 'unmute_room':

            room_base_info = room_base_schema.validate(body)
            room = self._room_mgr.get(room_base_info['room']).check_modify(room_base_info['secret'])
            muted = (request == 'mute_room')
            mute_str = 'mute' if muted else 'unmute'

            log.debug('Attempt to {} all participants in an existing AudioBridge room {}'.format(
                mute_str,
                room.room_id))
            room.mute_room(muted)
            result = {
                'audiobridge': 'success',
                'room': room_base_info['room'],
            }

        elif request == 'listparticipants':
            room_base_info = room_base_schema.validate(body)
            room = self._room_mgr.get(room_base_info['room'])
            participant_list = room.list_participants()
            part_info_list = []
            for participant in participant_list:
                part_info = {
                    'id': participant.user_id,
                    'setup': participant.webrtc_started,
                    'mute': participant.muted,
                }
                if participant.display:
                    part_info['display'] = participant.display

                if participant.audiolevel_ext:
                    part_info['talking'] = participant.talking
                
                if room.spatial_audio:
                    part_info['spatial_position'] = participant.spatial_position

                part_info_list.append(part_info)

            result = {
                'audiobridge': 'participants',
                'room': room_base_info['room'],
                'participants': part_info_list
            }

        elif request == 'listforwarders':
            log.debug('Attempt to list all forwarders in the audiobridge room')
            room_base_info = room_base_schema.validate(body)
            room = self._room_mgr.get(room_base_info['room']).check_modify(room_base_info['secret'])

            forwarder_list = room.rtp_forwarder_list()

            result = {
                'audiobridge': 'forwarders',
                'room': room_base_info['room'],
                'rtp_forwarders': forwarder_list
            }
            
        elif request == 'rtp_forward':
            log.debug('Attemp to start audiobridge rtp forwarder')

            # check admin_key
            if self._plugin.config['general']['lock_rtp_forward'] and \
               self._plugin.config['general']['admin_key']:
                admin_key = body.get('admin_key', '')
                if admin_key != self._plugin.config['general']['admin_key']:
                    raise JanusCloudError("Unauthorized (wrong {})".format('admin_key'),
                                          JANUS_AUDIOBRIDGE_ERROR_UNAUTHORIZED)

            room_base_info = room_base_schema.validate(body)
            room = self._room_mgr.get(room_base_info['room']).check_modify(room_base_info['secret'])


            forward_params = rtp_forward_schema.validate(body)
            forwarder = room.rtp_forward(**forward_params)

            result = {
                'audiobridge': 'success',
                'room': room_base_info['room'],
                'stream_id': forwarder['stream_id'],
                'host': forwarder['host'],
                'port': forwarder['port'],
            }
            if 'group' in forwarder:
                result['group'] = forwarder['group']

        elif request == 'stop_rtp_forward':
            log.debug('Attempt to stop one audiobridge rtp forwarder')

            # check admin_key
            if self._plugin.config['general']['lock_rtp_forward'] and \
               self._plugin.config['general']['admin_key']:
                admin_key = body.get('admin_key', '')
                if admin_key != self._plugin.config['general']['admin_key']:
                    raise JanusCloudError("Unauthorized (wrong {})".format('admin_key'),
                                          JANUS_AUDIOBRIDGE_ERROR_UNAUTHORIZED)

            room_base_info = room_base_schema.validate(body)
            room = self._room_mgr.get(room_base_info['room']).check_modify(room_base_info['secret'])
            stream_id = stop_rtp_forward_schema.validate(body)['stream_id']

            room.stop_rtp_forward(stream_id)

            result = {
                'videoroom': 'stop_rtp_forward',
                'room': room_base_info['room'],
                'stream_id': stream_id
            }

        elif request == 'enable_recording':

            room_base_info = room_base_schema.validate(body)
            room = self._room_mgr.get(room_base_info['room']).check_modify(room_base_info['secret'])
            record_params = record_schema.validate(body)

            log.debug('Enable Recording: {} for room {}'.format(record_params['record'], room.room_id))

            room.enable_recording(**record_params)

            result = {
                'audiobridge': 'success',
                'record': record_params['record'],
            }
        elif request == 'enable_mjrs':

            room_base_info = room_base_schema.validate(body)
            room = self._room_mgr.get(room_base_info['room']).check_modify(room_base_info['secret'])
            mjrs_params = mjrs_schema.validate(body)

            log.debug('Enable MJR recording: {} for room {}'.format(mjrs_params['mjrs'], room.room_id))

            room.enable_mjrs(**mjrs_params)

            result = {
                'audiobridge': 'success',
                'mjrs': record_params['record'],
            }

        elif request == 'resetdecoder':
            participant = self.participant
            if participant is None or participant.room is None:
                raise JanusCloudError('Can\'t reset (not in a room)',
                                      JANUS_AUDIOBRIDGE_ERROR_NOT_JOINED)
            
            if self.participant_type != JANUS_AUDIOBRIDGE_P_TYPE_PARTICIPANT:
                raise JanusCloudError('Can\'t reset (not a participant)',
                                      JANUS_AUDIOBRIDGE_ERROR_NOT_JOINED)

            participant.resetdecoder()
            result = {
                'audiobridge': 'success',
            } 

        elif request == 'play_file':
            # check admin_key
            if self._plugin.config['general']['lock_play_file'] and \
               self._plugin.config['general']['admin_key']:
                admin_key = body.get('admin_key', '')
                if admin_key != self._plugin.config['general']['admin_key']:
                    raise JanusCloudError("Unauthorized (wrong {})".format('admin_key'),
                                          JANUS_AUDIOBRIDGE_ERROR_UNAUTHORIZED)

            room_base_info = room_base_schema.validate(body)
            room = self._room_mgr.get(room_base_info['room']).check_modify(room_base_info['secret'])
            play_file_params = play_file_schema.validate(body)

            log.debug('Play file {} for room {}'.format(play_file_params['filename'], room.room_id))

            file_id = room.play_file(**play_file_params)

            result = {
                'audiobridge': 'success',
                'room': room_base_info['room'],
                'file_id': file_id
            }

        elif request == 'is_playing':
            room_base_info = room_base_schema.validate(body)
            room = self._room_mgr.get(room_base_info['room']).check_modify(room_base_info['secret'])
            file_id = stop_file_schema.validate(body)['file_id']

            log.debug('check file id {} is playing for room {}'.format(play_file_params['file_id'], room.room_id))

            playing = room.is_playing(file_id)

            result = {
                'audiobridge': 'success',
                'room': room_base_info['room'],
                'file_id': file_id,
                'playing': playing
            }  
            
        elif request == 'stop_file':
            room_base_info = room_base_schema.validate(body)
            room = self._room_mgr.get(room_base_info['room']).check_modify(room_base_info['secret'])
            file_id = stop_file_schema.validate(body)['file_id']

            log.debug('Stop file id {} for room {}'.format(play_file_params['file_id'], room.room_id))

            room.stop_file(file_id)

            result = {
                'audiobridge': 'success',
                'room': room_base_info['room'],
                'file_id': file_id
            }   
        else:
            raise JanusCloudError('Unknown request {}'.format(body),
                                  JANUS_AUDIOBRIDGE_ERROR_INVALID_REQUEST)

        if result is None:
            raise JanusCloudError('Invalid response',
                                  JANUS_AUDIOBRIDGE_ERROR_UNKNOWN_ERROR)
        return result

    def _handle_async_message(self, transaction, body, jsep):
        try:
            request = body.get('request')
            if request is None:
                raise JanusCloudError('Request {}  format invalid'.format(body), JANUS_AUDIOBRIDGE_ERROR_INVALID_ELEMENT)

            # check jsep type is offer or answer
            # TODO Does jsep cantains type field? or janus add to it ?
            if jsep:
                jsep_type = jsep.get('type')
                if jsep_type != 'offer' and jsep_type != 'answer':
                    raise JanusCloudError('Unsupported SDP type \'{}\''.format(jsep_type),
                                          JANUS_AUDIOBRIDGE_ERROR_INVALID_SDP)                       

            reply_event = None
            reply_jsep = None
            if request == 'join':
                log.debug("Configuring new participant")
                if self.participant_type != JANUS_AUDIOBRIDGE_P_TYPE_NONE or self.participant is not None:
                    raise JanusCloudError('Already in a room (use changeroom to join another one)',
                                          JANUS_AUDIOBRIDGE_ERROR_ALREADY_JOINED)                    
                join_base_info = join_base_schema.validate(body)
                room = self._room_mgr.get(join_base_info['room']). \
                        check_join(join_base_info['pin']).check_token(join_base_info.get('token', ''))
                
                join_params = join_params_schema.validate(body)

                new_participant, reply_jsep = room.new_participant(
                    user_id=join_base_info.get('id', 0),
                    handle=self,
                    jsep=jsep,
                    **join_params)

                # attach publisher to self
                self.participant_type = JANUS_AUDIOBRIDGE_P_TYPE_PARTICIPANT
                self.participant = new_participant

                # flush candidates if pending
                if len(self._pending_candidates) > 0:
                    new_participant.trickle(candidates=self._pending_candidates)
                    self._pending_candidates.clear()

                participants = []
                participant_list = room.list_participants()
                for participant in participant_list:
                    if participant != new_participant:
                        pl = {
                            'id': participant.user_id,
                            'setup': participant.webrtc_started,
                            'muted': participant.muted
                        }
                        if participant.display:
                            pl['display'] = participant.display
                        if participant.audiolevel_ext:
                            pl['talking'] = participant.talking
                        if room.spatial_audio:
                            pl['spatial_position'] = participant.spatial_position
                        
                        participants.append(pl)
                
                reply_event = {
                    'audiobridge': 'joined',
                    'room': room.room_id,
                    'id': new_participant.user_id,
                    'participants': participants
                }
                if new_participant.plainrtp and new_participant.rtp is not None:
                    reply_event['rtp'] = new_participant.rtp

            elif request == 'configure':
                participant = self.participant
                if participant is None or participant.room is None:
                    raise JanusCloudError('Can\'t configure (not in a room)',
                                          JANUS_AUDIOBRIDGE_ERROR_NOT_JOINED)

                configure_params = participant_configure_schema.validate(body)
                reply_jsep = participant.configure(jsep=jsep, **configure_params)

                reply_event = {
                    'audiobridge': 'event',
                    'result': 'ok',
                }

            elif request == 'changeroom':
                raise JanusCloudError('unsupported request {}'.format(body),
                                      JANUS_AUDIOBRIDGE_ERROR_INVALID_REQUEST)
            elif request == 'hangup':
                if self.participant is not None:
                    if self.participant_type == JANUS_AUDIOBRIDGE_P_TYPE_PARTICIPANT:
                        self.participant.hangup()
                    elif self.participant_type == JANUS_AUDIOBRIDGE_P_TYPE_LISTENER:
                        self.participant.hangup()
                reply_event = {
                    'audiobridge': 'hangingup',
                }                
            elif request == 'leave':
                participant = self.participant
                if participant is None or participant.room is None:
                    raise JanusCloudError('Can\'t leave (not in a room)',
                                          JANUS_AUDIOBRIDGE_ERROR_NOT_JOINED)
                room_id = participant.room_id
                user_id = participant.user_id
                participant.destroy()
                reply_event = {
                    'audiobridge': 'left',
                    'room': room_id,
                    'id': user_id,
                }
            else:
                raise JanusCloudError('Unknown request \'{}\''.format(request),
                                      JANUS_AUDIOBRIDGE_ERROR_INVALID_REQUEST)                

            # Process successfully
            if reply_event:
                self._push_plugin_event(data=reply_event, jsep=reply_jsep, transaction=transaction)

        except JanusCloudError as e:
            log.exception('Fail to handle async message ({}) for handle {}'.format(body, self.handle_id))
            type, dummy, tb = sys.exc_info()
            tb_list = traceback.format_list(traceback.extract_tb(tb)[-10:])
            self._push_plugin_event({
                'audiobridge': 'event',
                'error_code': e.code,
                'error': str(e),
                'traceback': tb_list,
            }, transaction=transaction)
        except SchemaError as e:
            log.exception('invalid message format ({}) for handle {}'.format(body, self.handle_id))
            type, dummy, tb = sys.exc_info()
            tb_list = traceback.format_list(traceback.extract_tb(tb)[-10:])
            self._push_plugin_event({
                'audiobridge': 'event',
                'error_code': JANUS_AUDIOBRIDGE_ERROR_INVALID_ELEMENT,
                'error': str(e),
                'traceback': tb_list,
            }, transaction=transaction)
        except Exception as e:
            log.exception('Fail to handle async message ({}) for handle {}'.format(body, self.handle_id))
            type, dummy, tb = sys.exc_info()
            tb_list = traceback.format_list(traceback.extract_tb(tb)[-10:])
            self._push_plugin_event({
                'audiobridge': 'event',
                'error_code': JANUS_ERROR_BAD_GATEWAY,
                'error': str(e),
                'traceback': tb_list,
            }, transaction=transaction)

    def on_participant_detach(self, participant):
        if self.participant == participant:
            self.participant_type = JANUS_AUDIOBRIDGE_P_TYPE_NONE
            self.participant = None

    def push_plugin_event(self, data, jsep=None, transaction=None):
        self._push_plugin_event(data=data, jsep=jsep, transaction=transaction)

    def push_event(self, method, transaction=None, **kwargs):
        self._push_event(method=method, transaction=transaction, **kwargs)


    def choose_server(self, transport=None):
        if transport is None:
            transport = self._session.ts
        return self._plugin.backend_server_mgr.choose_server(self._session.ts)

class AudioBridgePlugin(PluginBase):
    """ This video room plugin """

    def __init__(self, proxy_config, backend_server_mgr, pyramid_config):
        super().__init__(proxy_config, backend_server_mgr, pyramid_config)
        self.handles = set()
        self.config = self.read_config(
            os.path.join(proxy_config['general']['configs_folder'], 'janus-proxy.plugin.audiobridge.yml')
        )
        global _backend_server_mgr
        _backend_server_mgr = backend_server_mgr
        self.backend_server_mgr = backend_server_mgr
        room_dao = None
        if self.config['general']['room_db'].startswith('memory'):
            room_dao = None
        elif self.config['general']['room_db'].startswith('redis://'):
            import redis
            from januscloud.proxy.dao.rd_audiobridge_room_dao import RDAudioBridgeRoomDao
            connection_pool = redis.BlockingConnectionPool.from_url(
                url=self.config['general']['room_db'],
                decode_responses=True,
                health_check_interval=30,
                timeout=10)
            redis_client = redis.Redis(connection_pool=connection_pool)
            room_dao = RDAudioBridgeRoomDao(redis_client)
        else:
            raise JanusCloudError(
                'room_db \'{}\' not support by audiobridge plugin'.format(self.config['general']['room_db']),
                JANUS_ERROR_NOT_IMPLEMENTED)

        self.room_mgr = AudioBridgeRoomManager(
            room_db=self.config['general']['room_db'],
            room_dao=room_dao,
            auto_cleanup_sec=self.config['general']['room_auto_destroy_timeout'],
            admin_key=self.config['general']['admin_key']
        )

        self.room_mgr.load_from_config(self.config['rooms'])

        includeme(pyramid_config)
        pyramid_config.registry.audiobridge_plugin = self

        log.info('{} initialized!'.format(JANUS_AUDIOBRIDGE_NAME))

    def get_version(self):
        return JANUS_AUDIOBRIDGE_VERSION

    def get_version_string(self):
        return JANUS_AUDIOBRIDGE_VERSION_STRING

    def get_description(self):
        return JANUS_AUDIOBRIDGE_DESCRIPTION

    def get_name(self):
        return JANUS_AUDIOBRIDGE_NAME

    def get_author(self):
        return JANUS_AUDIOBRIDGE_AUTHOR

    def get_package(self):
        return JANUS_AUDIOBRIDGE_PACKAGE

    def create_handle(self, handle_id, session, opaque_id=None, *args, **kwargs):
        return AudioBridgeHandle(handle_id, session, self, opaque_id, *args, **kwargs)

    @staticmethod
    def read_config(config_file):

        audiobridge_config_schema = Schema({
            Optional("general"): Default({
                Optional("room_db"): Default(StrVal(), default='memory'),
                Optional("room_auto_destroy_timeout"): Default(IntVal(min=0, max=86400), default=0),
                Optional("admin_key"): Default(StrVal(), default=''),
                Optional("lock_rtp_forward"): Default(BoolVal(), default=False),
                Optional("lock_play_file"): Default(BoolVal(), default=False),
                AutoDel(str): object  # for all other key we don't care
            }, default={}),
            Optional("rooms"): Default([{
                'room_id': IntVal(),
                DoNotCare(str): object  # for all other key we don't care
            }], default=[]),
            
#            Optional("rooms"): Default([{
#                'room_id': IntVal(),
#                Optional('description'): StrVal(),
#                Optional('is_private'): BoolVal(),
#                Optional('secret'): StrVal(),
#                Optional('pin'): StrVal(),
#                Optional('sampling_rate'): IntVal(min=0),
#                Optional('spatial_audio'): BoolVal(),
#                Optional('audiolevel_ext'): BoolVal(),
#                Optional('audiolevel_event'): BoolVal(),
#                Optional('audio_active_packets'): IntVal(min=1),
#                Optional('audio_level_average'): IntVal(min=1, max=127),
#                Optional('default_prebuffering'): IntVal(min=0, max=MAX_PREBUFFERING),
#                Optional('default_expectedloss'): IntVal(min=0, max=20),
#                Optional('default_bitrate'): IntVal(min=500, max=512000),
#                Optional('record'): BoolVal(),
#                Optional('record_file'): StrVal(),
#                Optional('record_dir'): StrVal(),
#                Optional('mjrs'): BoolVal(),
#                Optional('mjrs_dir'): StrVal(),
#                Optional('allow_rtp_participants'): BoolVal(),
#                Optional('groups'): ListVal(StrVal()),

#                Optional('rtp_forward_id'): IntVal(min=0),
#                Optional('rtp_forward_host'): StrVal(),
#                Optional('rtp_forward_host_family'): EnumVal(['ipv4', 'ipv6']),
#                Optional('rtp_forward_port'): IntVal(min=0, max=65535),
#                Optional('rtp_forward_ssrc'): IntVal(min=0),
#                Optional('rtp_forward_codec'): EnumVal(['opus', 'pcma', 'pcmu']),
#                Optional('rtp_forward_group'): StrVal(),
#                Optional('rtp_forward_srtp_suite'): IntVal(values=(32, 80)),
#                Optional('rtp_forward_srtp_crypto'): StrVal(),
#                Optional('rtp_forward_always_on'): BoolVal(),

#                AutoDel(str): object  # for all other key we don't care
#            }], default=[]),
            DoNotCare(str): object  # for all other key we don't care

        })
        #print('config file:', config_file)
        if config_file is None or config_file == '':
            config = audiobridge_config_schema.validate({})
        else:
            log.info('Audiobridge plugin loads the config file: {}'.format(os.path.abspath(config_file)))
            config = parse_config(config_file, audiobridge_config_schema)

        # check other configure option is valid or not

        return config


def includeme(config):
    config.add_route('audiobridge_info', JANUS_AUDIOBRIDGE_API_BASE_PATH)
    config.add_route('audiobridge_room_list', JANUS_AUDIOBRIDGE_API_BASE_PATH + '/rooms')
    config.add_route('audiobridge_room', JANUS_AUDIOBRIDGE_API_BASE_PATH + '/rooms/{room_id}')
    config.add_route('audiobridge_participant_list', JANUS_AUDIOBRIDGE_API_BASE_PATH + '/rooms/{room_id}/participants')
    config.add_route('audiobridge_participant', JANUS_AUDIOBRIDGE_API_BASE_PATH + '/rooms/{room_id}/participants/{user_id}')
    config.add_route('audiobridge_tokens', JANUS_AUDIOBRIDGE_API_BASE_PATH + '/rooms/{room_id}/tokens')
    config.add_route('audiobridge_forwarder_list', JANUS_AUDIOBRIDGE_API_BASE_PATH + '/rooms/{room_id}/rtp_forwarders')
    config.scan('januscloud.proxy.plugin.audiobridge')


@get_view(route_name='audiobridge_info')
def get_audiobridge_info(request):
    plugin = request.registry.audiobridge_plugin
    room_mgr = plugin.room_mgr

    audiobridge_info = {
        'package': plugin.get_package(),
        'version': plugin.get_version(),
        'version_str': plugin.get_version_string(),
        'name': plugin.get_name(),
        'author': plugin.get_author(),
        'description': plugin.get_description(),
        'handles': len(plugin.handles),
        'rooms': len(room_mgr)
    }
    return audiobridge_info


@get_view(route_name='audiobridge_room_list')
def get_audiobridge_room_list(request):
    plugin = request.registry.audiobridge_plugin
    room_mgr = plugin.room_mgr

    room_list_params = get_params_from_request(request, room_list_schema)
    room_list = room_mgr.list(**room_list_params)

    room_info_list = []
    for room in room_list:

        room_info = {
            'room': room.room_id,
            'description': room.description,
            'record': room.record,
            'num_participants': room.num_participants(),
            'sampling_rate': room.sampling_rate,
            'spatial_audio': room.spatial_audio,
            'pin_required': bool(room.pin),
            'muted': room.muted,
        }
        room_info_list.append(room_info)

    return room_info_list


@post_view(route_name='audiobridge_room_list')
def post_audiobridge_room_list(request):
    plugin = request.registry.audiobridge_plugin
    room_mgr = plugin.room_mgr

    log.debug('Creating a new audiobridge room')
    params = get_params_from_request(request)
    room_base_info = room_base_schema.validate(params)
    admin_key = params.get('admin_key', '')
    room_params = room_params_schema.validate(params)
    new_room, saved = room_mgr.create(room_id=room_base_info['room'],
                               permanent=room_base_info['permanent'],
                               admin_key=admin_key,
                               room_params=room_params)
    reply = {
        'audiobridge': 'created',
        'room': new_room.room_id,
        'permanent': saved
    }    
    
    return reply

@get_view(route_name='audiobridge_room')
def get_audiobridge_room(request):
    plugin = request.registry.audiobridge_plugin
    room_mgr = plugin.room_mgr
    room_id = int(request.matchdict['room_id'])
    room = room_mgr.get(room_id)

    room_info = {
        'room': room.room_id,
        'description': room.description,
        'record': room.record,
        'num_participants': room.num_participants(),
        'sampling_rate': room.sampling_rate,
        'spatial_audio': room.spatial_audio,
        'pin_required': bool(room.pin),
        'muted': room.muted,
    }

    return room_info


@delete_view(route_name='audiobridge_room')
def delete_audiobridge_room(request):
    plugin = request.registry.audiobridge_plugin
    room_mgr = plugin.room_mgr
    room_id = int(request.matchdict['room_id'])
    room_base_info = get_params_from_request(request, room_base_schema)

    room_mgr.destroy(room_id=room_id,
                     secret=room_base_info['secret'],
                     permanent=room_base_info['permanent'])

    return Response(status=200)


@post_view(route_name='audiobridge_tokens')
def post_audiobridge_tokens(request):
    plugin = request.registry.audiobridge_plugin
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
                              JANUS_AUDIOBRIDGE_ERROR_INVALID_ELEMENT)
    reply = {
        'audiobridge': 'success',
        'room': room_id
    }
    if allowed_params['action'] != 'disable':
        reply['allowed'] = list(room.allowed)

    return reply


@get_view(route_name='audiobridge_participant_list')
def get_audiobridge_participant_list(request):
    plugin = request.registry.audiobridge_plugin
    room_mgr = plugin.room_mgr
    room_id = int(request.matchdict['room_id'])
    room = room_mgr.get(room_id)
    participant_list = room.list_participants()

    part_info_list = []
    for participant in participant_list:
        part_info = {
            'id': participant.user_id,
            'setup': participant.webrtc_started,
            'mute': participant.muted,
        }
        if participant.display:
            part_info['display'] = participant.display

        if participant.audiolevel_ext:
            part_info['talking'] = participant.talking
                
        if room.spatial_audio:
            part_info['spatial_position'] = participant.spatial_position

        part_info_list.append(part_info)

    return part_info_list


@delete_view(route_name='audiobridge_participant')
def delete_audiobridge_participant(request):
    plugin = request.registry.audiobridge_plugin
    room_mgr = plugin.room_mgr
    room_id = int(request.matchdict['room_id'])
    user_id = int(request.matchdict['user_id'])
    room_base_info = get_params_from_request(request, room_base_schema)
    room = room_mgr.get(room_id).check_modify(room_base_info['secret'])
    room.kick_participant(user_id)

    return Response(status=200)

@get_view(route_name='audiobridge_forwarder_list')
def get_audiobridge_forwarder_list(request):
    plugin = request.registry.audiobridge_plugin
    room_mgr = plugin.room_mgr
    room_id = int(request.matchdict['room_id'])
    params = get_params_from_request(request)
    room_base_info = room_base_schema.validate(params)
    room = room_mgr.get(room_id).check_modify(room_base_info['secret'])

    forwarder_list = room.rtp_forwarder_list()

    return forwarder_list


@post_view(route_name='audiobridge_forwarder_list')
def post_audiobridge_forwarder_list(request):
    plugin = request.registry.audiobridge_plugin
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
                                  JANUS_AUDIOBRIDGE_ERROR_UNAUTHORIZED)

    room_base_info = room_base_schema.validate(params)
    room = room_mgr.get(room_id).check_modify(room_base_info['secret'])

    forward_params = rtp_forward_schema.validate(params)
    forwarder = room.rtp_forward(**forward_params)

    return forwarder


@delete_view(route_name='audiobridge_forwarder_list')
def delete_audiobridge_forwarder_list(request):
    plugin = request.registry.audiobridge_plugin
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
                                  JANUS_AUDIOBRIDGE_ERROR_UNAUTHORIZED)

    room_base_info = room_base_schema.validate(params)
    room = room_mgr.get(room_id).check_modify(room_base_info['secret'])
    stream_id = stop_rtp_forward_schema.validate(params)['stream_id']

    room.stop_rtp_forward(stream_id)

    return Response(status=200)
