# Copyright (C) 2018 British Broadcasting Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import uuid
import json
import time

from flask import Blueprint, make_response, abort, Response, url_for, request
from ..Config import ENABLE_HTTPS, DNS_DOMAIN, PORT_BASE, DNS_SD_MODE
from ..TestHelper import get_default_ip


class Node(object):
    def __init__(self, port_increment):
        self.port = PORT_BASE + 200 + port_increment

    def get_sender(self, stream_type="video"):
        protocol = "http"
        host = get_default_ip()
        if ENABLE_HTTPS:
            protocol = "https"
            if DNS_SD_MODE == "multicast":
                host = "nmos-mocks.local"
            else:
                host = "mocks.{}".format(DNS_DOMAIN)
        # TODO: Provide the means to downgrade this to a <v1.2 JSON representation
        sender = {
            "id": str(uuid.uuid4()),
            "label": "Dummy Sender",
            "description": "Dummy Sender",
            "version": "50:50",
            "caps": {},
            "tags": {},
            "manifest_href": "{}://{}:{}/{}.sdp".format(protocol, host, self.port, stream_type),
            "flow_id": str(uuid.uuid4()),
            "transport": "urn:x-nmos:transport:rtp.mcast",
            "device_id": str(uuid.uuid4()),
            "interface_bindings": ["eth0"],
            "subscription": {
                "receiver_id": None,
                "active": True
            }
        }
        return sender




NODE = Node(1)
NODE_API = Blueprint('node_api', __name__)

def createCORSResponse(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    return response


@NODE_API.route('/<stream_type>.sdp', methods=["GET"])
def node_video_sdp(stream_type):
    # TODO: Should we check for an auth token here? May depend on the URL?
    response = None
    if stream_type == "video":
        with open("test_data/IS0401/video.sdp") as f:
            response = make_response(f.read())
    elif stream_type == "audio":
        with open("test_data/IS0401/audio.sdp") as f:
            response = make_response(f.read())
    elif stream_type == "data":
        with open("test_data/IS0401/data.sdp") as f:
            response = make_response(f.read())
    elif stream_type == "mux":
        with open("test_data/IS0401/mux.sdp") as f:
            response = make_response(f.read())
    else:
        abort(404)

    response.headers["Content-Type"] = "application/sdp"
    return response


@NODE_API.route('/x-nmos/connection/<version>/single/<resource>/', methods=["GET"], strict_slashes=False)
def resources(version, resource):
    # TODO get list of actual ids
    resource_ids = ["This should be a list of linked sender/receiver ids"]
    base_data = []
    for id in resource_ids:
        base_data.append(str(id)+ '/')

    return make_response(createCORSResponse(Response(json.dumps(base_data), mimetype='application/json')))


@NODE_API.route('/x-nmos/connection/<version>/single/<resource>/<resource_id>', methods=["GET"], strict_slashes=False)
def connection(version, resource, resource_id):
    base_data = ["constraints/", "staged/", "active/", "transportfile/", "transporttype/"]

    return make_response(createCORSResponse(Response(json.dumps(base_data), mimetype='application/json')))


@NODE_API.route('/x-nmos/connection/<version>/single/<resource>/<resource_id>/constraints', methods=["GET"], strict_slashes=False)
def constraints(version, resource, resource_id):
    base_data = json.loads("""[{"connection_authorization":{"enum":[false]},"connection_uri":{},"ext_is_07_rest_api_url":{},"ext_is_07_source_id":{}}]""")

    return make_response(createCORSResponse(Response(json.dumps(base_data), mimetype='application/json')))

@NODE_API.route('/x-nmos/connection/<version>/single/<resource>/<resource_id>/staged', methods=["GET", "PATCH"], strict_slashes=False)
def staged(version, resource, resource_id):
    """
    TODO Once node has sender and receiver details, sort out the missing data for the responses
    TODO Update the actual registry data for the receiver after PATCH requests?
    """
    if request.method == 'PATCH':
        base_data = {"activation": {"activation_time": "", "mode": "", "requested_time": ""}}
        
        if "sender_id" in request.json:
            # Either patching to staged or directly to activated
            base_data['master_enable'] = request.json['master_enable']
            base_data['sender_id'] = request.json['sender_id']
            base_data['transport_file'] = json.loads('{"data":null,"type":null}') # Hmmmmmmm
            base_data['transport_params'] = request.json['transport_params']
            base_data['transport_params']['connection_authorisation'] = 'false'
            
            if "activation" in request.json:
                # Patching straight to activated
                base_data["activation"]["activation_time"] = time.now()
                base_data['activation']['mode'] = request.json['activation']['mode']

        elif "activation" in request.json:
            # Either patching to activate after staging or deactivating
            base_data["activation"]["activation_time"] = time.now()
            base_data['activation']['mode'] = request.json['activation']['mode']
            
            if request.json['master_enable'] == "false":
                # deactivating
                base_data['master_enable'] = request.json['master_enable']
                # Need to fetch sender_id, transport_file, transport_params from 'activated' data set somewhere
                # base_data['sender_id'] = 
                # base_data['transport_file'] = 
                # base_data['transport_params'] = 
                # base_data['transport_params']['connection_authorisation'] = 'false'
            
            else:
                # Activating after staging
                base_data['master_enable'] = 'true' #this should be json true not str true
                # Need to fetch sender_id, transport_file, transport_params from 'activated' data set somewhere
                # base_data['sender_id'] = 
                # base_data['transport_file'] = 
                # base_data['transport_params'] = 
                # base_data['transport_params']['connection_authorisation'] = 'false'

    elif request.method == 'GET':
        # Need to fetch json of actual current 'staged' info
            base_data = json.loads("""{
                                        "activation": {
                                            "activation_time": null,
                                            "mode": null,
                                            "requested_time": null
                                        },
                                        "master_enable": false,
                                        "sender_id": null,
                                        "transport_file": {
                                            "data": null,
                                            "type": null
                                        },
                                        "transport_params": [
                                            {
                                                "destination_port": "auto",
                                                "interface_ip": "auto",
                                                "multicast_ip": null,
                                                "rtp_enabled": true,
                                                "source_ip": null
                                            },
                                            {
                                                "destination_port": "auto",
                                                "interface_ip": "auto",
                                                "multicast_ip": null,
                                                "rtp_enabled": true,
                                                "source_ip": null
                                            }
                                        ]
                                    }""")

    return make_response(createCORSResponse(Response(json.dumps(base_data), mimetype='application/json')))

@NODE_API.route('/x-nmos/connection/<version>/single/<resource>/<resource_id>/active', methods=["GET"], strict_slashes=False)
def active(version, resource, resource_id):
    base_data = json.loads("""[{"activation":{"activation_time":null,"mode":null,"requested_time":null},"master_enable":false,"sender_id":null,"transport_file":{"data":null,"type":null},"transport_params":[{"connection_authorization":false,"connection_uri":null,"ext_is_07_rest_api_url":null,"ext_is_07_source_id":null}]}]""")

    return make_response(createCORSResponse(Response(json.dumps(base_data), mimetype='application/json')))

@NODE_API.route('/x-nmos/connection/<version>/single/<resource>/<resource_id>/transporttype', methods=["GET"], strict_slashes=False)
def transport_type(version, resource, resource_id):
    # TODO fetch from resource info
    base_data = "urn:x-nmos:transport:websocket"
    # alternatively "urn:x-nmos:transport:rtp.mcast"

    return make_response(createCORSResponse(Response(json.dumps(base_data), mimetype='application/json')))

@NODE_API.route('/x-nmos/connection/<version>/single/<resource>/<resource_id>/transportfile', methods=["GET"], strict_slashes=False)
def transport_file(version, resource, resource_id):
    # GET should either redirect to the location of the transport file or return it directly (easy-nmos requests to this endpoint return 404)
    base_data = []
    
    return make_response(createCORSResponse(Response(json.dumps(base_data), mimetype='application/json')))
