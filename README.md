Janus Cloud
=============

Janus-cloud is an API proxy for Janus WebRTC server to realize the WebRTC server cluster， which is based on Python3 so can be deployed on any platform. It provides a Back-to-Back API proxy working between the client and the Janus servers. In one hand, the web client communicates with Janus-cloud proxy through Janus' websocket API, just like with the real Janus server.  In the other hand, Janus-cloud proxy forwards the requests to some back-end Janus server instance in the cluster to serve the client's request. Janus-cloud proxy is only responsible for the API (signal) processing, while media streams is still left to Janus to handle. Clients would establish the PeerConnction with the back-end Janus server directly, without Janus-cloud involvement. 

**NOTE：！！！！in-developing, don't fork me！！！！**

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


