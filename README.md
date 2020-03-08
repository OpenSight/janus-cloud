Janus-cloud
=============

Janus-cloud is an JANUS API proxy to construct the Janus WebRTC server cluster, which is based on Python3 so that it can be deployed on any platform. A Back-to-Back API proxy would be deployed between the client and the original Janus servers. In one hand, the WebRTC client communicates with Janus-cloud proxy through Janus' original  API, just like with the real Janus server. In the other hand, Janus-cloud proxy would forwards the requests to the back-end Janus server in the cluster on behave of the client. Janus-cloud proxy is only responsible for the API (signalling) processing, while media streams is still left to Janus server to relay, so that the clients would establish the PeerConnctions with the back-end Janus server directly, without Janus-cloud involvement. In this case, Janus-cloud proxy can be considered as a WebRTC signal server, while the original Janus server would be downgraded to work as a WebRTC media server.

**NOTE: !!! in-developing, don't fork me !!!  **

Why Janus-cloud
-----------------

Janus is an excellent WebRTC server, simple and well-structured. Its pluggable design and sophisticated API is impressive and amazing. But it also comes with some disadvantage.
First, Janus is design to be a standalone server, which cannot be scale to support the huge RTC workload. But in the cloud-based environment, scalability is essential. With the help of Janus-cloud, engineers can easily build a large and scalable cluster system of WebRTC server. 
Second, Janus processes the WebRTC signalling, as well as the media data. But in the real communication world, signalling and media are usually divided into two plane, so that more flexibility can be provided. Janus-cloud fulfills this requirement by handling the signalling only and leaving media to Janus-server. Janus-cloud is developed by Python3 language which is more suitable to deal with the signalling, in the other hand, C language, which is used by Janus server, is more suitable to transmit media data in an efficient way . 


Features
-----------------

* Scalable, Janus media servers can be added/removed to/from the cluster dynamically
* Support Janus media service self-register, service monitor, circuit breaker
* Pluggable, support the new features through developing the new plugin
* the Same API with the Janus server, which is compatible with the original client of the Janus server

Components
-------------------

Janus-cloud contains two main components, Janus-proxy and Janus-sentinel, which are responsible for two different functions.

### Janus-proxy

Janus-proxy is responsible for signal handling, which 

### Janus-sentinel




Topology
-----------------
The deployment of Janus-cloud would be similar with the below topology. 


```
                                                                                     +------------------------------+
                                                                                     |Virtual Machine 2             |
                                                                                     |      +----------+            |
                                                                              +------------>+  Janus   |            |
                                                                              |      |      +----------+            |
                                                                              |      |                              |
                                       +------------------------------+       |      |      +-------------------+   |
                                       |Virtual Machine 1             |       |      |      |  Janus Sentinel   |   |
+-----------------------+              |                              |       |      |      +-------------------+   |
| Web Browser           |   WebSocket  |      +----------------+      |       |      +------------------------------+
| (e.g. Chrome/Firefox) +-------------------->+  Janus Proxy   +--------------+
+-----------------------+              |      +----------------+      |       |
                                       |                              |       |      +------------------------------+
                                       +------------------------------+       |      |Virtual Machine 3             |
                                                                              |      |       +---------+            |
                                                                              +------------->+ Janus   |            |
                                                                                     |       +---------+            |
                                                                                     |                              |
                                                                                     |       +------------------+   |
                                                                                     |       | Janus Sentinel   |   |
                                                                                     |       +------------------+   |
                                                                                     +------------------------------+


```




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


