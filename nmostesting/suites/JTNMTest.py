# Copyright (C) 2021 Advanced Media Workflow Association
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

import time
import json
import socket
import uuid
import inspect
import threading
import random
from copy import deepcopy
from urllib.parse import urlparse
from dnslib import QTYPE
from git.objects.base import IndexObject
from zeroconf_monkey import ServiceBrowser, ServiceInfo, Zeroconf

from ..GenericTest import GenericTest, NMOSTestException, NMOSInitException
from .. import Config as CONFIG
from ..MdnsListener import MdnsListener
from ..TestHelper import get_default_ip
from ..TestResult import Test

from flask import Flask, render_template, make_response, abort, Blueprint, flash, request, Response, session

JTNM_API_KEY = "client-testing"
REG_API_KEY = "registration"
CALLBACK_ENDPOINT = "/clientfacade_response"
CACHEBUSTER = random.randint(1, 10000)

answer_available = threading.Event()


app = Flask(__name__)
TEST_API = Blueprint('test_api', __name__)

class ClientFacadeException(Exception):
    """Provides a way to exit a single test, by providing the TestResult return statement as the first exception
       parameter"""
    pass

@TEST_API.route(CALLBACK_ENDPOINT, methods=['POST'])
def retrieve_answer():
    # Hmmmm, there must be a more elegant way to pass data between threads in a Flask application
    global clientfacade_answer_json

    if request.method == 'POST':
        clientfacade_answer_json = request.json
        if 'name' not in clientfacade_answer_json:
            return 'Invalid JSON received'
        answer_available.set()

    return 'OK'

class JTNMTest(GenericTest):
    """
    Testing initial set up of new test suite for controller testing
    """
    def __init__(self, apis, registries, node, dns_server):
        # JRT: remove the spec_path parameter to prevent GenericTest from attempting to download RAML from repo
        apis[JTNM_API_KEY].pop("spec_path", None)
        GenericTest.__init__(self, apis)
        self.authorization = False  # System API doesn't use auth, so don't send tokens in every request
        self.primary_registry = registries[1]
        self.node = node
        self.dns_server = dns_server
        self.registry_mdns = []
        self.zc = None
        self.zc_listener = None
        self.mock_registry_base_url = ''
        self.mock_node_base_url = ''
        self.question_timeout = 600 # seconds
        self.test_data = self.load_resource_data()
        self.sender_possible_answers = [] # Reference for all possible senders
        self.sender_expected_answers = [] # Actual senders registered to mock registry
        self.receiver_possible_answers = [] # Reference for all possible receivers
        self.receiver_expected_answers = [] # Actual receivers registered to mock registry
        self.connectable_receiver_expected_answers = [] # Receivers with connection api registered to mock registry (subset of receiver_expected_answers)

    def set_up_tests(self):
        self.zc = Zeroconf()
        self.zc_listener = MdnsListener(self.zc)
        if self.dns_server:
            self.dns_server.load_zone(self.apis[JTNM_API_KEY]["version"], self.protocol, self.authorization,
                                      "test_data/IS0401/dns_records.zone", CONFIG.PORT_BASE+100)
            print(" * Waiting for up to {} seconds for a DNS query before executing tests"
                  .format(CONFIG.DNS_SD_ADVERT_TIMEOUT))
            self.dns_server.wait_for_query(
                QTYPE.PTR,
                [
                    "_nmos-register._tcp.{}.".format(CONFIG.DNS_DOMAIN),
                    "_nmos-registration._tcp.{}.".format(CONFIG.DNS_DOMAIN)
                ],
                CONFIG.DNS_SD_ADVERT_TIMEOUT
            )
            # Wait for a short time to allow the device to react after performing the query
            time.sleep(CONFIG.API_PROCESSING_TIMEOUT)

        if CONFIG.DNS_SD_MODE == "multicast":
            priority = 0

            # Add advertisement for primary registry
            info = self._registry_mdns_info(self.primary_registry.get_data().port, priority)
            self.registry_mdns.append(info)

        # Reset registry to clear previous heartbeats, etc.
        self.primary_registry.reset()
        self.primary_registry.enable()
        self.mock_registry_base_url = 'http://' + get_default_ip() + ':' + str(self.primary_registry.get_data().port) + '/'
        self.mock_node_base_url = 'http://' + get_default_ip() + ':' + str(self.node.port) + '/'

        if CONFIG.DNS_SD_MODE == "multicast":
            self.zc.register_service(self.registry_mdns[0])

        # Populate mock registry with senders and receivers and store the results
        MAX_SENDERS = 3
        MAX_RECEIVERS = 3

        self._populate_registry(MAX_SENDERS, MAX_RECEIVERS)

        print('Registry should be available at ' + self.mock_registry_base_url)


    def tear_down_tests(self):
        # Clean up mDNS advertisements and disable registries
        if CONFIG.DNS_SD_MODE == "multicast":
            for info in self.registry_mdns:
                self.zc.unregister_service(info)
        self.primary_registry.disable()
        
        # Reset the state of the client testing façade
        self.do_request("POST", self.apis[JTNM_API_KEY]["url"], json={"clear": "True"})

        if self.zc:
            self.zc.close()
            self.zc = None
        if self.dns_server:
            self.dns_server.reset()

        self.mock_registry_base_url = ''
        self.registry_mdns = []
    
    def set_up_test(self):
        """Setup performed before EACH test"""
        self.primary_registry.query_api_called = False

    def execute_tests(self, test_names):
        """Perform tests defined within this class"""
        self.pre_tests_message()

        for test_name in test_names:
            self.execute_test(test_name)

        self.post_tests_message()

    def execute_test(self, test_name):
        """Perform a test defined within this class"""
        self.test_individual = (test_name != "all")

        # Run manually defined tests
        if test_name == "all":
            for method_name in dir(self):
                if method_name.startswith("test_"):
                    method = getattr(self, method_name)
                    if callable(method):
                        print(" * Running " + method_name)
                        test = Test(inspect.getdoc(method), method_name)
                        try:
                            self.set_up_test()
                            self.result.append(method(test))
                        except NMOSTestException as e:
                            self.result.append(e.args[0])
                        except Exception as e:
                            self.result.append(self.uncaught_exception(method_name, e))

        # Run a single test
        if test_name != "auto" and test_name != "all":
            method = getattr(self, test_name)
            if callable(method):
                print(" * Running " + test_name)
                test = Test(inspect.getdoc(method), test_name)
                try:
                    self.set_up_test()
                    self.result.append(method(test))
                except NMOSTestException as e:
                    self.result.append(e.args[0])
                except Exception as e:
                    self.result.append(self.uncaught_exception(test_name, e))

    def _invoke_client_facade(self, question, answers, test_type, timeout=None, multipart_test=None):
        """ 
        Send question and answers to Client Façade
        question:   text to be presented to Test User
        answers:    list of all possible answers
        test_type:  "radio" - one and only one answer
                    "checkbox" - multiple answers
                    "action" - Test User asked to click button, defaults to self.question_timeout
        timeout:    number of seconds before Client Façade times out test
        multipart_test: indicates test uses multiple questions. Default None, should be increasing
                    integers with each subsequent call within the same test
        """
        global clientfacade_answer_json
        answer_available.clear()

        # Get the name of the calling test method to use as an identifier
        test_method_name = inspect.currentframe().f_back.f_code.co_name
        method = getattr(self, test_method_name)

        question_timeout = timeout if timeout else self.question_timeout
        test_name = test_method_name if not multipart_test else test_method_name + '_' + str(multipart_test)

        json_out = {
            "test_type": test_type,
            "name": test_name,
            "description": inspect.getdoc(method),
            "question": question,
            "answers": answers,
            "time_sent": time.time(),
            "timeout": question_timeout,
            "url_for_response": "http://" + request.headers.get("Host") + CALLBACK_ENDPOINT,
            "answer_response": "",
            "time_answered": ""
        }
        # Send questions to Client Façade API endpoint then wait
        valid, response = self.do_request("POST", self.apis[JTNM_API_KEY]["url"], json=json_out)

        if not valid:
            raise ClientFacadeException("Problem contacting Client Façade: " + response)

        # Wait for answer available signal or question timeout in seconds
        # JSON response to question is set in in clientfacade_answer_json global variable (Hmmm)        
        get_json = answer_available.wait(timeout=question_timeout)

        if get_json == False:
            raise ClientFacadeException("Test timed out")

        # Basic integrity check for response json
        if clientfacade_answer_json['name'] is None:
            raise ClientFacadeException("Integrity check failed: result format error: " +json.dump(clientfacade_answer_json))

        if clientfacade_answer_json['name'] != json_out['name']:
            raise ClientFacadeException("Integrity check failed: cannot compare result of " + json_out['name'] + " with expected result for " + clientfacade_answer_json['name'])
            
        return clientfacade_answer_json['answer_response']

    def _registry_mdns_info(self, port, priority=0, api_ver=None, api_proto=None, api_auth=None, ip=None):
        """Get an mDNS ServiceInfo object in order to create an advertisement"""
        if api_ver is None:
            api_ver = self.apis[JTNM_API_KEY]["version"]
        if api_proto is None:
            api_proto = self.protocol
        if api_auth is None:
            api_auth = self.authorization

        if ip is None:
            ip = get_default_ip()
            hostname = "nmos-mocks.local."
        else:
            hostname = ip.replace(".", "-") + ".local."

        txt = {'api_ver': api_ver, 'api_proto': api_proto, 'pri': str(priority), 'api_auth': str(api_auth).lower()}

        service_type = "_nmos-register._tcp.local."

        info = ServiceInfo(service_type,
                           "NMOSTestSuite{}{}.{}".format(port, api_proto, service_type),
                           addresses=[socket.inet_aton(ip)], port=port,
                           properties=txt, server=hostname)
        return info

    def _generate_random_indices(self, index_range, min_index_count=2, max_index_count=4):
        """
        index_range: number of possible indices
        min_index_count, max_index_count: Minimum, maximum number of indices to be returned. 
        """
        indices = list(range(index_range))
        index_count = random.randint(min_index_count, max_index_count)

        return random.sample(indices, index_count)

    def _format_device_metadata(self, label, description, id):
        """ Used to format answers based on device metadata """
        return label + ' (' + description + ', ' + id + ')'
    
    def _generate_answers(self, labels, descriptions, resource_ids, indices):
        """ 
        labels: list of labels
        descriptions: list of descriptions
        resource_ids: list of ids
        indices: list of the indices of labels/descriptions/resource_ids
        """
        answers = []
        
        for i in indices:
            answers.append(self._format_device_metadata(labels[i], descriptions[i], resource_ids[i]))

        return answers

    def _register_resources(self, type, labels, descriptions, resource_ids, indices, include_connection_api=True):
        """
        type: of the resource e.g. sender, reciever
        labels: a list of resource labels
        descriptions: a list of resource descriptions
        resource_ids: a list of uuids - note that this will be modified by the function
        indices: list of the indices of the resources to be registered
        """
        # Post resources to registry
        for i in indices:
            device_data = self.post_super_resources_and_resource(self, type, descriptions[i], include_connection_api)
            device_data['label'] = labels[i]
            resource_ids[i] = device_data['id'] # overwrite default UUID with actual one from the Registry
            self.post_resource(self, type, device_data, codes=[200])

        return resource_ids # resource_ids have been modified

    def _populate_registry(self, max_sender_count, max_receiver_count):
        """This data is baseline data for all tests in the test suite"""
        
        # Potential senders to be added to registry
        sender_labels = ['Test-node-1/sender/gilmour', 'Test-node-1/sender/waters', 'Test-node-1/sender/wright', 'Test-node-1/sender/mason', 'Test-node-1/sender/barrett']
        sender_descriptions = ['Mock sender 1', 'Mock sender 2', 'Mock sender 3', 'Mock sender 4', 'Mock sender 5']
        sender_ids = [str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())]
        
        # Pick indices of up to max_sender_count senders to register
        random_indices = self._generate_random_indices(len(sender_labels), min_index_count=2, max_index_count=4)
        sender_ids = self._register_resources("sender", sender_labels, sender_descriptions, sender_ids, random_indices)

        self.sender_expected_answers = self._generate_answers(sender_labels, sender_descriptions, sender_ids, random_indices)
        self.sender_possible_answers = self._generate_answers(sender_labels, sender_descriptions, sender_ids, range(len(sender_labels)))

        # Pick up to max_sender_count to register
        receiver_labels = ['Test-node-2/receiver/palin', 'Test-node-2/receiver/cleese', 'Test-node-2/receiver/jones', 'Test-node-2/receiver/chapman', 'Test-node-2/receiver/idle', 'Test-node-2/receiver/gilliam']
        receiver_descriptions = ['Mock receiver 1', 'Mock receiver 2', 'Mock receiver 3', 'Mock receiver 4', 'Mock receiver 5', 'Mock receiver 6']
        receiver_ids = [str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())]
        
        # Pick random receivers to register
        receiver_indices = self._generate_random_indices(len(receiver_labels), min_index_count=2, max_index_count=5)

        # Split receiver_indices into those that have connection api and those that don't
        connectable_receiver_count = random.randint(1, len(receiver_indices) - 1)
        connectable_receiver_indices = receiver_indices[0:connectable_receiver_count]
        not_connectable_receiver_indices = receiver_indices[connectable_receiver_count:len(receiver_indices)]
        
        # Register 'connectable' receivers
        receiver_ids = self._register_resources("receiver", receiver_labels, receiver_descriptions, receiver_ids, connectable_receiver_indices, include_connection_api=True)
        # Register 'not connectable receviers
        receiver_ids = self._register_resources("receiver", receiver_labels, receiver_descriptions, receiver_ids, not_connectable_receiver_indices, include_connection_api=False)

        self.receiver_expected_answers = self._generate_answers(receiver_labels, receiver_descriptions, receiver_ids, receiver_indices)
        self.receiver_possible_answers = self._generate_answers(receiver_labels, receiver_descriptions, receiver_ids, range(len(receiver_labels)))
        self.connectable_receiver_expected_answers = self._generate_answers(receiver_labels, receiver_descriptions, receiver_ids, connectable_receiver_indices)

    def load_resource_data(self):
        """Loads test data from files"""
        api = self.apis[JTNM_API_KEY]
        result_data = dict()
        resources = ["node", "device", "source", "flow", "sender", "receiver"]
        for resource in resources:
            with open("test_data/JTNM/v1.3_{}.json".format(resource)) as resource_data:
                resource_json = json.load(resource_data)
                result_data[resource] = resource_json

        return result_data

    def post_resource(self, test, type, data=None, reg_url=None, codes=None, fail=Test.FAIL, headers=None):
        """
        Perform a POST request on the Registration API to create or update a resource registration.
        Raises an NMOSTestException when the response is not as expected.
        Otherwise, on success, returns values of the Location header and X-Paging-Timestamp debugging header.
        """
        if not data:
            data = self.test_data[type]

        if not reg_url:
            reg_url = self.mock_registry_base_url + 'x-nmos/registration/v1.3/'

        if not codes:
            codes = [200, 201]

        valid, r = self.do_request("POST", reg_url + "resource", json={"type": type, "data": data}, headers=headers)
        if not valid:
            # Hmm - do we need these exceptions as the registry is our own mock registry?
            raise NMOSTestException(fail(test, "Registration API returned an unexpected response: {}".format(r)))

        location = None
        timestamp = None

        wrong_codes = [_ for _ in [200, 201] if _ not in codes]

        if r.status_code in wrong_codes:
            raise NMOSTestException(fail(test, "Registration API returned wrong HTTP code: {}".format(r.status_code)))
        elif r.status_code not in codes:
            raise NMOSTestException(fail(test, "Registration API returned an unexpected response: "
                                               "{} {}".format(r.status_code, r.text)))
        elif r.status_code in [200, 201]:
            # X-Paging-Timestamp is a response header that implementations may include to aid debugging
            if "X-Paging-Timestamp" in r.headers:
                timestamp = r.headers["X-Paging-Timestamp"]
            if "Location" not in r.headers:
                raise NMOSTestException(fail(test, "Registration API failed to return a 'Location' response header"))
            path = "{}resource/{}s/{}".format(urlparse(reg_url).path, type, data["id"])
            location = r.headers["Location"]
            if path not in location:
                raise NMOSTestException(fail(test, "Registration API 'Location' response header is incorrect: "
                                             "Location: {}".format(location)))
            if not location.startswith("/") and not location.startswith(self.protocol + "://"):
                raise NMOSTestException(fail(test, "Registration API 'Location' response header is invalid for the "
                                             "current protocol: Location: {}".format(location)))

        return location, timestamp

    def post_super_resources_and_resource(self, test, type, description, include_connection_api=True, sender_id=None, receiver_id=None, fail=Test.FAIL):
        """
        Perform POST requests on the Registration API to create the super-resource registrations
        for the requested type, before performing a POST request to create that resource registration
        """
        # use the test data as a template for creating new resources
        data = deepcopy(self.test_data[type])
        data["id"] = str(uuid.uuid4())
        data["description"] = description

        if type == "node":
            pass
        elif type == "device":
            node = self.post_super_resources_and_resource(test, "node", description, include_connection_api, sender_id, receiver_id, fail=Test.UNCLEAR)
            data["node_id"] = node["id"]
            if include_connection_api:
                # Update the controls data with the URL of the mock node
                controls = data["controls"][0]["href"] = self.mock_node_base_url + '/x-nmos/connection/v1.0/'
            else:
                data["controls"] = [] # Remove controls data
            data["senders"] = [ sender_id ] if sender_id else [] 
            data["receivers"] = [ receiver_id ] if receiver_id else [] 
        elif type == "source":
            device = self.post_super_resources_and_resource(test, "device", description, include_connection_api, sender_id, receiver_id, fail=Test.UNCLEAR)
            data["device_id"] = device["id"]
        elif type == "flow":
            source = self.post_super_resources_and_resource(test, "source", description, include_connection_api, sender_id, receiver_id, fail=Test.UNCLEAR)
            data["device_id"] = source["device_id"]
            data["source_id"] = source["id"]
            # since device_id is v1.1, downgrade
            # Hmm We need to specify the registry version to ensure we downgrade to the right version
            #data = NMOSUtils.downgrade_resource(type, data, self.apis[REG_API_KEY]["version"])
        elif type == "sender":
            sender_id = str(uuid.uuid4())
            data["id"] = sender_id
            flow = self.post_super_resources_and_resource(test, "flow", description, include_connection_api, sender_id, receiver_id, fail=Test.UNCLEAR)
            data["device_id"] = flow["device_id"]
            data["flow_id"] = flow["id"]  # or post a flow first and use its id here?
        elif type == "receiver":
            receiver_id = str(uuid.uuid4())
            data["id"] = receiver_id
            device = self.post_super_resources_and_resource(test, "device", description, include_connection_api, sender_id, receiver_id, fail=Test.UNCLEAR)
            data["device_id"] = device["id"]

        self.post_resource(test, type, data, codes=[201], fail=fail)

        return data

    def pre_tests_message(self):
        """
        Introduction to JT-NM Tested Test Suite
        """
        question =  'These tests validate a Broadcast Controller under Test’s (BCuT) ability to query an IS-04 ' \
        'Registry with the IS-04 Query API and to control a Media Node using the IS-05 Connection ' \
        'Management API.\n\nA Test AMWA IS-04 v1.2/1.3 reference Registry is available on the network, ' \
        'and advertised in the DNS server via unicast DNS-SD\n\n' \
        'Although the test AMWA IS-04 Registry should be discoverable via DNS-SD, for the purposes of developing this testing framework ' \
        'it is also possible to reach the Registry via the following URL:\n\n' + self.mock_registry_base_url + 'x-nmos/query/v1.3\n\n' \
        'Once the BCuT has located the test AMWA IS-04 Registry, please click \'Next\''
        possible_answers=[]

        try:
            actual_answer = self._invoke_client_facade(question, possible_answers, test_type="action", timeout=600)

        except ClientFacadeException as e:
            # pre_test_introducton timed out
            pass

    def post_tests_message(self):
        """
        JT-NM Tested Test Suite testing complete!
        """
        question =  'JT-NM Tested Test Suite testing complete!\r\n\r\nPlease press \'Next\' to exit the tests'
        possible_answers=[]

        try:
            actual_answer = self._invoke_client_facade(question, possible_answers, test_type="action", timeout=10)

        except ClientFacadeException as e:
            # post_test_introducton timed out
            pass
    
    def test_01(self, test):
        """
        Ensure BCuT uses DNS-SD to find registry
        """
        if not CONFIG.ENABLE_DNS_SD or CONFIG.DNS_SD_MODE != "multicast":
            return test.DISABLED("This test cannot be performed when ENABLE_DNS_SD is False or DNS_SD_MODE is not "
                                 "'multicast'")

        return test.DISABLED("Test not yet implemented")


    def test_02(self, test):
        """
        Ensure BCuT can access the IS-04 Query API
        """
        # Mock registry already populated with data, see _populate_registry
            
        try:
            # Question 1 connection
            question = 'Use the BCuT to browse the Senders and Receivers on the discovered Registry via the selected IS-04 Query API.\n' \
            'Once you have finished browsing click \'Next\'. Successful browsing of the Registry will be automatically logged by the test framework.\n'
            possible_answers = []

            actual_answer = self._invoke_client_facade(question, possible_answers, test_type="action")

            # The registry will log calls to the Query API endpoints
            if not self.primary_registry.query_api_called:
                return test.FAIL('IS-04 Query API not reached')
            
            return test.PASS('IS-04 Query API reached successfully')

        except ClientFacadeException as e:
            return test.UNCLEAR(e.args[0])

    def test_03(self, test):
        """
        Query API should be able to discover all the senders that are registered in the Registry
        """
        # Mock registry populated, sender_possible_answers and sender_actual_answers initialized in  _populate_registry

        try:
            # Check senders 
            question = 'The Query API should be able to discover all the Senders that are registered in the Registry.\n' \
            'Refresh the BCuT\'s view of the Registry and carefully select the Senders that are available from the following list.' 
            possible_answers = self.sender_possible_answers

            actual_answers = self._invoke_client_facade(question, possible_answers, test_type="checkbox")

            if len(actual_answers) != len(self.sender_expected_answers):
                return test.FAIL('Incorrect sender identified')
            else:
                for answer in actual_answers:
                    if answer not in self.sender_expected_answers:
                        return test.FAIL('Incorrect sender identified')

            return test.PASS('All devices correctly identified')
        except ClientFacadeException as e:
            return test.UNCLEAR(e.args[0])


    def test_04(self, test):
        """
        Query API should be able to discover all the receivers that are registered in the Registry
        """
        # Mock registry populated, receiver_possible_answers and receiver_actual_answers initialized in  _populate_registry

        try:
            # Check receivers 
            question = 'The Query API should be able to discover all the Receivers that are registered in the Registry.\n' \
            'Refresh the BCuT\'s view of the Registry and carefully select the Receivers that are available from the following list.'
            possible_answers = self.receiver_possible_answers

            actual_answers = self._invoke_client_facade(question, possible_answers, test_type="checkbox")

            if len(actual_answers) != len(self.receiver_expected_answers):
                return test.FAIL('Incorrect receiver identified')
            else:
                for answer in actual_answers:
                    if answer not in self.receiver_expected_answers:
                        return test.FAIL('Incorrect receiver identified')

            return test.PASS('All devices correctly identified')
        except ClientFacadeException as e:
            return test.UNCLEAR(e.args[0])

    def test_05a(self, test):
        """
        Reference Sender is put offline
        """
        try:
            # Check senders 
            question = 'The Query API should be able to discover and dynamically update all the Senders that are registered in the Registry.\n' \
            'Use the BCuT to browse and take note of the Senders that are available.'
            possible_answers = []

            self._invoke_client_facade(question, possible_answers, test_type="action")

            # Take one of the senders offline
            possible_answers = self.sender_expected_answers.copy()
            offline_sender = random.choice(list(range(0, len(self.sender_expected_answers))))
            offline_sender_id = possible_answers[offline_sender].split(',')[1].strip(' )')
            offline_sender_data = deepcopy(self.test_data['sender'])
            offline_sender_data['id'] = offline_sender_id

            del_url = self.mock_registry_base_url + 'x-nmos/registration/v1.3/resource/senders/' + offline_sender_id
            valid, r = self.do_request("DELETE", del_url)

            # Remove the offline sender from the expected answers for future tests
            self.sender_expected_answers.remove(possible_answers[offline_sender])

            # Recheck senders
            question = "When your BCuT updates, select which sender has gone offline"

            actual_answer = self._invoke_client_facade(question, possible_answers, test_type="radio", multipart_test="1")

            if actual_answer != possible_answers[offline_sender]:
                return test.FAIL('Incorrect sender identified')

            return test.PASS('Sender correctly identified')
        except ClientFacadeException as e:
            return test.UNCLEAR(e.args[0])

    def test_05b(self, test):
        """
        Reference Sender is put online
        """
        try:
            # Check senders
            question = 'The Query API should be able to discover and dynamically update all the Senders that are registered in the Registry.\n' \
            'Use the BCuT to browse and take note of the Senders that are available.'
            possible_answers = []

            self._invoke_client_facade(question, possible_answers, test_type="action")

            # Put an extra sender online
            # Get list of currently offline senders
            possible_answers = list(set(self.sender_possible_answers) - set(self.sender_expected_answers))
            online_sender = random.choice(list(range(0, len(possible_answers))))
            online_sender_label = possible_answers[online_sender].split(' ')[0].strip("'")
            online_sender_description = possible_answers[online_sender].split('(')[1].split(',')[0]

            # Post new sender
            sender_data = self.post_super_resources_and_resource(self, 'sender', online_sender_description)
            sender_data['label'] = online_sender_label
            sender_id = sender_data['id']
            self.post_resource(self, 'sender', sender_data, codes=[200])

            # Update sender lists for rest of tests
            new_sender_answer = self._format_device_metadata(online_sender_label, online_sender_description, sender_id)
            self.sender_expected_answers.append(new_sender_answer)
            self.sender_possible_answers.remove(possible_answers[online_sender])
            self.sender_possible_answers.append(new_sender_answer)

            # id has changed so need to update possible answers
            possible_answers[online_sender] = new_sender_answer

            # Recheck senders
            question = "When your BCuT updates, select which sender has come online"

            actual_answer = self._invoke_client_facade(question, possible_answers, test_type="radio", multipart_test="1")

            if actual_answer != new_sender_answer:
                return test.FAIL('Incorrect sender identified')

            return test.PASS('Sender correctly identified')
        except ClientFacadeException as e:
            return test.UNCLEAR(e.args[0])

    def test_06(self, test):
        """
        Identify which Receiver devices are controllable via IS-05
        """
        try:
            # Check receivers 
            question = 'Some of the discovered Receivers are controllable via IS-05, for instance, allowing Senders to be connected.\n' \
            'Carefully select the Receivers that have connection APIs from the following list.'
            possible_answers = self.receiver_possible_answers

            actual_answers = self._invoke_client_facade(question, possible_answers, test_type="checkbox")

            if len(actual_answers) != len(self.connectable_receiver_expected_answers):
                return test.FAIL('Incorrect Receiver identified')
            else:
                for answer in actual_answers:
                    if answer not in self.connectable_receiver_expected_answers:
                        return test.FAIL('Incorrect Receiver identified')

            return test.PASS('All Receivers correctly identified')
        except ClientFacadeException as e:
            return test.UNCLEAR(e.args[0])


    def test_07(self, test):
        """
        Instruct Receiver to subscribe to a Sender’s Flow via IS-05
        """

        return test.DISABLED("Test not yet implemented")

    def test_08(self, test):
        """
        Disconnecting a Receiver from a connected Flow via IS-05
        """

        return test.DISABLED("Test not yet implemented")

    def test_09(self, test):
        """
        Indicating the state of connections via updates received from the IS-04 Query API
        """

        return test.DISABLED("Test not yet implemented")
  