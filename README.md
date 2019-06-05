Janus-cloud
=============

Janus-cloud is an JANUS API proxy to set up the Janus WebRTC server cluster, which is based on Python3 so can be deployed on any platform. It provides a Back-to-Back API proxy working between the client and the Janus servers. In one hand, the WebRTC client communicates with Janus-cloud proxy through Janus' original  API, just like with the real Janus server. In the other hand, Janus-cloud proxy forwards the requests to the back-end Janus server instance in the cluster to serve. Janus-cloud proxy is only responsible for the API (signalling) processing, while media streams is still left to Janus to relay, so that the clients establish the PeerConnctions with the back-end Janus server directly, without Janus-cloud involvement. In this case, Janus-cloud proxy is considered as a WebRTC signal server, while Janus would be downgraded to work as a WebRTC media server.

**NOTE: !!! in-developing, don't fork me !!!  **

Why Janus-cloud
-----------------

Janus is an excellent WebRTC server, simple and well-structured. Its pluggable design and sophisticated API is impressive and amazing. But it also comes with some disadvantage.
First, Janus is design to be a standalone server, which cannot be scale to support the increasing RTC workload. But in the cloud-based environment, scalability is essential. With the help of Janus-cloud, engineers can easily build a large and scalable cluster system of WebRTC server. 
Second, Janus processes the WebRTC signalling, as well as the media data. But in the real Communication world, signalling and media are usually divided into two plane, so that more flexibility can be provided. Janus-cloud fulfills this requirement by handling the signalling only and leaving media to Janus-server. Janus-cloud is developed by Python language which is more suitable to deal with the business signalling, and C language, which is used by Janus server, is more suitable to transmit media data in an efficient way . 


Features
-----------------

* Scalable, Janus media servers can be added/removed to/from the cluster dynamically
* Support Janus media service self-register, service monitor, circuit breaker
* Pluggable, support the new features through developing the new plugin



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


