Janus-cloud
=============

Janus-cloud is an JANUS API proxy to set up the Janus WebRTC server cluster, which is based on Python3 so can be deployed on any platform. It provides a Back-to-Back API proxy working between the client and the Janus servers. In one hand, the Janus client communicates with Janus-cloud proxy through Janus' websocket API, just like with the real Janus server. In the other hand, Janus-cloud proxy forwards the requests to some back-end Janus server instance in the cluster to serve the client's request. Janus-cloud proxy is only responsible for the API (signal) processing, while media streams is still left to Janus to relay, so that the clients establish the PeerConnctions with the back-end Janus server directly, without Janus-cloud involvement. In this case, Janus-cloud proxy is considered as a WebRTC signal server, while Janus would be downgraded to work as a WebRTC media server.

**NOTE: !!! in-developing, don't fork me !!!  **

Why Janus-cloud
-----------------


Features
-----------------

* Scale-out, can add/remove Janus media servers to the cluster dynamicly 
* Support Janus service register, service monitor, curcuit breaker
* Pluggable, obtains the new features through developing the new plugin



installation
----------------

Janus-cloud supports python 3.5 and up. To install 

``` {.sourceCode .bash}
$ python setup.py install
```

To start

``` {.sourceCode .bash}
$ janus-proxy <config file path>
```


