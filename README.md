Janus-cloud
=============

Janus-cloud is an Janus API proxy to create the Janus WebRTC server cluster, which is based on Python3 so that it can be deployed on any platform. A Back-to-Back API proxy would be deployed between the client and the original Janus servers. In one hand, the WebRTC client communicates with Janus-cloud proxy through Janus' original  API, just like with the real Janus server. In the other hand, Janus-cloud proxy would forwards the requests to the back-end Janus server in the cluster on behave of the client. Janus-cloud proxy is only responsible for the API (signalling) processing, while media streams is still left to Janus server to relay, so that the clients would establish the PeerConnections with the back-end Janus server directly, without Janus-cloud involvement. In this case, Janus-cloud proxy can be considered as a WebRTC signal server, while the original Janus server would be downgraded to work as a WebRTC media server.

1 Why Janus-cloud
-----------------

Janus is an excellent WebRTC server, simple and well-structured. Its pluggable design and sophisticated API is impressive and amazing. But it also comes with some disadvantage.

First, Janus is design to be a standalone server, which cannot be scale to support the huge RTC workload. But in the cloud-based environment, scalability is essential. With the help of Janus-cloud, engineers can easily build a large and scalable cluster system of WebRTC server.

Second, Janus processes the WebRTC signalling, as well as the media data. But in the real communication world, signalling and media are usually divided into two plane, so that more flexibility can be provided. Janus-cloud fulfills this requirement by handling the signalling only and leaving media to Janus-server. Janus-cloud is developed by Python3 language which is more suitable to deal with the signalling, in the other hand, C language, which is used by Janus server, is more suitable to transmit media data in an efficient way .


2 Features
-----------------

- Scalable, Janus media servers can be added/removed to/from the cluster dynamically
- Support Janus media service self-register, service monitor, circuit breaker
- Pluggable, support the new features through developing the new plugin
- Consistent API with the Janus server (since 1.0.2, until 1.0.2 (2022-05-23)), which is compatible with the original client of the Janus server


3 Components
-------------------

Janus-cloud has two main components, Janus-proxy and Janus-sentinel.

### Janus-proxy

Janus-proxy is responsible for signal handling, which communicates with the WebRTC client and relay the signal from client to the backend Janus servers. It conceals the detail of the backend Janus server cluster and output the same interface with the original Janus server. Janus-proxy is usually running on a standalone machine which is between the WebRTC client and the backend Janus servers. The WebRTC client interact with Janus-proxy for signal, but transfer to/from the real Janus Server for media. Janus-proxy has the following features/limitation:

- Only provide the WebSocket(s) API, not provide RESTful, RabbitMQ, MQTT, Nanomsg and UnixSockets like Janus
- Communicate with the backend Janus server by WebSocket
- Pluggable. Its business functionality is implemented by the various plugins
- Support RESTful admin interface
- Scalable. The backend Janus server can be added/removed to/from Janus-proxy dynamically
- Support multi algorithm to choose which backend server to relay signal

### Janus-sentinel

Janus-sentinel is responsible to care for the Janus server, normally, it runs at the same (virtual) machine with the Janus server. it has the following capabilities:

- Supervise the process of the Janus server, and keep it running
- Monitor the status of the Janus server, and report it to Janus-proxy through HTTP
- Calculate the workload of the Janus server
- Support post the status/workload statistic to multi HTTP URL
- Auto destroy for the idle video room

Note: the process of the Janus server can be started and maintained by the other system tools or system administrator manual, instead of Janus-sentinel. In this case, Janus-sentinel is only responsible for monitoring Janus server's status by its WebSocket API. But this approach is not a good idea.

4 Plugins of Janus-proxy
------------------------------

Janus-proxy is composed of many plugins, and the business logic of Janus-proxy is implemented by these plugins. The following plugins are provided within Janus-proxy by now.

### echotest

This is a trivial EchoTest plugin which is only used for test and show plugin interface of Janus-proxy. It provide developers a skeleton for the new plugin development

### videocall

This is a simple video call plugin which allow two WebRTC peer communicate with each other through the medium Janus server. It achieves the same function and outputs the same APIs with the videocall plugin of Janus server, as well as it can distribute the workload among the backend Janus servers.

Moreover, Janus-proxy also can be scaled out for videocall plugin to handle much more video calls. Different WebRTC peers may be assigned to different Janus-proxies which is able to communicates with each other through admin interface.

Its APIs is compatible with the videocall plugin of Janus-gateway util v1.0.3(2022-06-20).

### p2pcall

This is an other video call plugin, very similar to the videocall plugin, except that two WebRTC peer communicate with each other in p2p mode. It outputs same APIs like the videocall plugin, and also make Janus-proxy be able to scaled out to handle more video call. However no backend Janus servers is need to handle the media stream, because the WebRTC peers transmit the media data with each other directly.

Its APIs is compatible with the videocall plugin of Janus-gateway util v1.0.3(2022-6-20).

### videoroom

This is a plugin implementing a videoconferencing SFU, just like videoroom plugin of the Janus server. It tries to keep almost the same API with the videoroom plugin of Janus server, and scale it out by distributing different publishers to different backend Janus server, so that Janus-proxy can support more publishers in one videoconferencing room than single Janus server. Contrast to the videoroom plugin of Janus server, there are some limitations below on this plugin to simplify the code.

- subscriber switch not support
- string_ids not support
- dummy publishers not support

The videoroom plugin of Janus-gateway has refactored with the new multistream API primitive since v1.0.0, so this plugin also need to refactored to support it.
The new APIs of this plugin supports multistream API primitive,and is compatible with the videoroom plugin of Janus-gateway since 1.0.2 util v1.0.3(2022-06-20). 
If you want to deploy the Janus-proxy with the old Janus-gateway which version is lower than v1.0.0 and make use of the old API (without multistream), please use the 0.x version (from 0.x branch) of Janus-cloud 

5 Topology
-----------------
The structure of Janus-cloud would be similar with the below topology. 


```
                                                                               +-----------------------------+
                                                                               |Virtual Machine 2            |
                                                                               |      +----------+           |
                                                                          +---------->+  Janus   |           |
                                                                          |    |      +-----+----+           |
                                                                          |    |            |                |
                                     +------------------------------+     |    |      +-----+-------------+  |
                                     |Virtual Machine 1             |     |    |      |  Janus-sentinel   |  |
+-----------------------+            |                              |     |    |      +-------------------+  |
| Web Browser           |  WebSocket |      +----------------+      |     |    +-----------------------------+
| (e.g. Chrome/Firefox) +------------------>+  Janus-proxy   +------------+
+-----------------------+            |      +----------------+      |     |
                                     |                              |     |    +-----------------------------+
                                     +------------------------------+     |    |Virtual Machine 3            |
                                                                          |    |     +---------+             |
                                                                          +--------->+ Janus   |             |
                                                                               |     +-----+---+             |
                                                                               |           |                 |
                                                                               |     +-----+------------+    |
                                                                               |     | Janus-sentinel   |    |
                                                                               |     +------------------+    |
                                                                               +-----------------------------+


```
Janus-proxy is often deployed on a standalone machine between WebRTC client(like Browser) and Janus server. All signal  from WebRTC client would be received by Janus-proxy first , then relayed to one of the backend Janus servers.

Janus-sentinel is often deployed along with the Janus server on the same machine. Janus-sentinel keep Janus process running and monitor its status, then report to Janus-proxy at intervals.


6 Installation
----------------

Before installation, the following requirements must be satisfied.

- Python >= 3.5
- pip
- setuptools


Janus-cloud supports python 3.5 and up. It's strongly recommended to install Janus-cloud in a python virtual environment, like "venv".

### Install from PyPi 

To install Janus-cloud from PyPi, 

``` {.sourceCode .bash}
$ pip install janus-cloud
```

### Install from source

To install Janus-cloud from project source

``` {.sourceCode .bash}
$ pip install <project_root>
```

Where <project_root> is the root directory of project source, which contains the setup.py file

For developer, who want to debug the Janus-cloud, and install it for develop mode:

``` {.sourceCode .bash}
$ pip install -e <project_root>
```

7 Configure and Start
---------------------------

Some resource files, like sample configuration(with explanations), html test scripts, and etc, are shipped within the project source. 
After installation, these resource would be installed under **<sys.prefix>/opt/janus-cloud** where <sys.prefix> is the root directory of your filesystem. or the the root directory of virtual environment if install in a virtual environment.

### janus-sentinel

Edit the configuration file of Janus-sentinel, then type the following commands to start.

``` {.sourceCode .bash}
$ janus-sentinel <janus-sentinel config file path>
```

### janus-proxy

Edit the configuration file of Janus-proxy, then type the following commands to start.

``` {.sourceCode .bash}
$ janus-proxy <janus-proxy config file path>
```

8 Requirements for the backend Janus server
-----------------------------------------------------------

By now, Janus-proxy / Janus-sentinel only support corresponding with the backend Janus server by WebSocket, not support other transport.

there are the following requirements on the backend Janus server when deploying with Janus-cloud:

* WebSocket transport must be enabled to correspond with Janus-proxy/Janus-sentinel
* token_auth must be disabled


9 Directory structure of project source
---------------------------------

Keep It Simple, and Stupid

```
janus-cloud/
    |
    +----conf/            Sample configuration files
    |
    +----html/            Html & js page code for test client
    |
    +----doc/             some extra documents for janus-cloud
    |
    +----januscloud/      Python code package of janus-cloud
    |
    +----CHANGES.md       change log for each release
    |
    +----README.md        This readme
    |
    +----LICENSE          AGPL 3.0 license 
    |
    +----MANIFEST.in      Manifest file describing the static resource file
    |
    +----pyproject.toml   python project building description file (compatible with PEP 518 / PEP 517)
    |
    +----setup.py         Python setup script
```


10 Contact us
---------------------------------

Any thought, feedback or insult is welcome!

Developed by OpenSight(public@opensight.cn)
