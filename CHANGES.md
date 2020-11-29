Changelog
==============

All notable changes to this project will be documented in this file.

 [v0.4.0]  - 2020-11-29
---------------------------------
* Added support for audio level feature for videoroom
* Added support for h265, av1 codec support for videoroom
* Support for require end-to-end encryption (require_e2ee) on videoroom
* Added simulcast support for rtp_forward of videoroom
* The APIs of Videoroom, Videocall, P2pcall is compatible with Janus-gateway of v0.10.7
* The gevent is updated to 20.9.0, which support python 3.7 or higher

 [v0.3.0]  - 2020-06-21
---------------------------------

* add rtp_forward operations to the admin API of videoroom plugin
* Add support for VP9 and H.264 profile negotiation for videoroom and echotest plugin
* Added support for multichannel Opus audio (surround) for videoroom
* Added VideoRoom option to only allow admins to change the recording state
* Enable / disable recording while conference is in progress for videoroom
* support redis to store the backend server info

 [v0.2.0]  - 2020-05-10
---------------------------------

* Janus-proxy support api_secret authorization
* Janus-sentinel support admin_secret for sending admin API request
* The APIs of Videoroom, Videocall, P2pcall is compatible with Janus-gateway of v0.9.2
* support rtp_forward feature for videoroom


 [v0.1.0]  - 2020-03-29
---------------------------------

* initial version released
* janus-proxy and janus-sentinel are finished
* echotest, videocall, p2pcall, videoroom plugins of janus-proxy are ready
