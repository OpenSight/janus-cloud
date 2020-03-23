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


Overview
-------------------

Janus-cloud has two main components, Janus-proxy and Janus-sentinel, which are responsible for two different functions.

### Janus-proxy

Janus-proxy is responsible for signal handling, which communicates with the WebRTC client and relay the signal to the backend Janus servers. It conceals the detail of the backend Janus server cluster and output the same interface with the original Janus server. Janus-proxy is usually running on a standalone machine which is between the WebRTC client and the backend Janus servers. The WebRTC client interact with Janus-proxy for signal, but transfer to/from the real Janus Server for media. Janus-proxy has the following features/limitation:

* Only provide the WebSocket(s) API, but not provide RESTful, RabbitMQ, MQTT, Nanomsg and UnixSockets like Janus
* Pluggable. Its functionality is implemented by the various plugins
* Support RESTful admin interface
* Scalable. Janus server can be added/removed to/from the cluster dynamically


### Janus-sentinel

Janus-sentinel is responsible for caring for the Janus server, normally, it runs at the same (virtual) machine with the Janus server. it has the following capabilities:

* Supervise the process of the Janus server, and keep it running
* Monitor the status of the Janus server, and report it to Janus-proxy through HTTP
* Calculate the workload of the Janus server
* Support post the status/workload stat to multi HTTP URL

Note: the process of the Janus server can be started and maintained by the other system tools or system administrator manul, instead of Janus-sentinel. In this case, Janus-sentinel is only responsible for monitoring Janus server's status by its WebSocket API. But this approach is not a good idea.

Plugins for Janus-proxy
------------------------------

The business logic of Janus-proxy is implemented by the plugins. By now, the folloing plugins is provided in box. 

### echotest

### videocall

### p2pcall

### videoroom


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
Janus-proxy is often deployed on a standalone machine between WebRTC client(like Browser) and Janus server. All signal infomation from would be transfer to Janus-proxy by WebSocket first , then relayed to one of the backend Janus servers.

Janus-sentinel is often deployed along with the Janus server on the same machine. Janus-sentinel keep Janus process running and moniter its status, and report to Janus-proxy. 


Installation
----------------

### install from source

Janus-cloud supports python 3.5 and up. To install 

``` {.sourceCode .bash}
$ python setup.py install
```


Configure and Start
---------------------------

### janus-sentinel

### janus-proxy

To start

``` {.sourceCode .bash}
$ janus-proxy <config file path>
```


Requirements of the backend Janus server configuration
--------------------------------------

By now, Janus-proxy / Janus-sentinel communicates with the backend Janus server only by WebSocket, not support other transport. And consider that the API of the backend Janus server is only used by Janus-proxy / Janus-sentinel internel, and not directly output to outside, so no secret/token mechanism is applied in the communication between them now. 

In summary, there are some requirements below on the configuration of the backend Janus sever when deploy with Janux-cloud:

* WebSocket transport must be enabled
* api_secret and token_auth which are used for api security must be disabled 
* admin_secret must be disabled

