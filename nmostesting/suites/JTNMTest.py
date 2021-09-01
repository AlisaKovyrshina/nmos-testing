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

import asyncio
import time
import json
import socket
import uuid
import inspect
import random
from copy import deepcopy
from urllib.parse import urlparse
from dnslib import QTYPE
from git.objects.base import IndexObject
from threading import Event
from zeroconf_monkey import ServiceBrowser, ServiceInfo, Zeroconf

from ..GenericTest import GenericTest, NMOSTestException, NMOSInitException
from .. import Config as CONFIG
from ..MdnsListener import MdnsListener
from ..TestHelper import get_default_ip
from ..TestResult import Test
from ..NMOSUtils import NMOSUtils

from flask import Flask, render_template, make_response, abort, Blueprint, flash, request, Response, session

JTNM_API_KEY = "client-testing"
REG_API_KEY = "registration"
CALLBACK_ENDPOINT = "/clientfacade_response"
CACHEBUSTER = random.randint(1, 10000)

# asyncio queue for passing Testing Façade answer responses back to tests
_event_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_event_loop)
_answer_response_queue = asyncio.Queue()

# use exit Event to quit tests early that involve waiting for senders/connections 
exit = Event()

app = Flask(__name__)
TEST_API = Blueprint('test_api', __name__)

class TestingFacadeException(Exception):
    """Exception thrown due to comms or data errors between NMOS Testing and Testing Façade"""
    pass

@TEST_API.route(CALLBACK_ENDPOINT, methods=['POST'])
def retrieve_answer():

    if request.method == 'POST':
        if 'name' not in request.json:
            return 'Invalid JSON received'

        _event_loop.call_soon_threadsafe(_answer_response_queue.put_nowait, request.json)

        # Interupt any 'sleeps' that are still active 
        exit.set()

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
        self.question_timeout = 600 # default timeout in seconds
        self.test_data = self.load_resource_data()
        self.senders = [] # sender list containing: {'label': '', 'description': '', 'id': '', 'registered': True/False, 'answer_str': ''}
        self.sinks = []
        self.receivers = [] # receiver list containing: {'label': '', 'description': '', 'id': '', 'registered': True/False, 'connectable': True/False, 'answer_str': ''}
        self.senders_ip_base = '239.3.14.' # Random multicast IP to assign to senders

    def set_up_tests(self):
        self.zc = Zeroconf()
        self.zc_listener = MdnsListener(self.zc)
        if self.dns_server:
            self.dns_server.load_zone(self.apis[JTNM_API_KEY]["version"], self.protocol, self.authorization,
                                      "test_data/IS0401/dns_records.zone", CONFIG.PORT_BASE+100)
            self.dns_server.set_expected_query(
                QTYPE.PTR,
                [
                    "_nmos-query._tcp.{}.".format(CONFIG.DNS_DOMAIN)
                ]
            )
        # Reset registry to clear previous heartbeats, etc.
        self.primary_registry.reset()
        self.primary_registry.enable()
        self.mock_registry_base_url = 'http://' + get_default_ip() + ':' + str(self.primary_registry.get_data().port) + '/'
        self.mock_node_base_url = 'http://' + get_default_ip() + ':' + str(self.node.port) + '/'

        # Populate mock registry with senders and receivers and store the results
        self._populate_registry()

        # Set up mock node
        self.node.registry_url = self.mock_registry_base_url

        print('Registry should be available at ' + self.mock_registry_base_url)


    def tear_down_tests(self):

        self.primary_registry.disable()
        
        # Reset the state of the Testing Façade
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

    async def getAnswerResponse(self, timeout):
        return await asyncio.wait_for(_answer_response_queue.get(), timeout=timeout)

    def _send_testing_facade_questions(self, test_method_name, question, answers, test_type, timeout=None, multipart_test=None, metadata=None):
        """ 
        Send question and answers to Testing Façade
        question:   text to be presented to Test User
        answers:    list of all possible answers
        test_type:  "radio" - one and only one answer
                    "checkbox" - multiple answers
                    "action" - Test User asked to click button, defaults to self.question_timeout
        timeout:    number of seconds before Testing Façade times out test
        multipart_test: indicates test uses multiple questions. Default None, should be increasing
                    integers with each subsequent call within the same test
        metadata: Test details to assist fully automated testing
        """

        method = getattr(self, test_method_name)

        question_timeout = timeout if timeout else self.question_timeout
        question_id = test_method_name if not multipart_test else test_method_name + '_' + str(multipart_test)

        json_out = {
            "test_type": test_type,
            "question_id": question_id,
            "name": test_method_name,
            "description": inspect.getdoc(method),
            "question": question,
            "answers": answers,
            "time_sent": time.time(),
            "timeout": question_timeout,
            "url_for_response": "http://" + request.headers.get("Host") + CALLBACK_ENDPOINT,
            "answer_response": "",
            "time_answered": "",
            "metadata": metadata
        }
        # Send questions to Testing Façade API endpoint then wait
        valid, response = self.do_request("POST", self.apis[JTNM_API_KEY]["url"], json=json_out)

        if not valid:
            raise TestingFacadeException("Problem contacting Testing Façade: " + response)

        return json_out

    def _wait_for_testing_facade(self, test_name, timeout=None):

        question_timeout = timeout if timeout else self.question_timeout

        # Wait for answer response or question timeout in seconds
        try:
            answer_response = _event_loop.run_until_complete(self.getAnswerResponse(timeout=question_timeout))
        except asyncio.TimeoutError:
            raise TestingFacadeException("Test timed out")

        # Basic integrity check for response json
        if answer_response['name'] is None:
            raise TestingFacadeException("Integrity check failed: result format error: " +json.dump(answer_response))

        if answer_response['name'] != test_name:
            raise TestingFacadeException("Integrity check failed: cannot compare result of " + test_name + " with expected result for " + answer_response['name'])
            
        return answer_response

    def _invoke_testing_facade(self, question, answers, test_type, timeout=None, multipart_test=None, metadata=None):
        
        # Get the name of the calling test method to use as an identifier
        test_method_name = inspect.currentframe().f_back.f_code.co_name

        json_out = self._send_testing_facade_questions(test_method_name, question, answers, test_type, timeout, multipart_test, metadata)

        return self._wait_for_testing_facade(json_out['name'], timeout)    

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

    def _populate_registry(self):
        """This data is baseline data for all tests in the test suite"""

        # Sink initial details
        self.sinks = [{'label': 'Test-node-3/sink/mary', 'description': 'Mock sink 1'},
                          {'label': 'Test-node-3/sink/max', 'description': 'Mock sink 2'},
                          {'label': 'Test-node-3/sink/emi', 'description': 'Mock sink 3'},
                          {'label': 'Test-node-3/sink/rob', 'description': 'Mock sink 4'},
                          {'label': 'Test-node-3/sink/perry', 'description': 'Mock sink 5'},
                          {'label': 'Test-node-3/sink/jonny', 'description': 'Mock sink 6'}]

        for sink in self.sinks:
            sink["id"] = str(uuid.uuid4()) + '/'
            sink["answer_str"] = sink['id']

        # Generate indices of self.receivers to be registered and some of those to be non connectable
        sink_indices = self._generate_random_indices(len(self.sinks)) #indices for /receivers/<receiver_id>/sinks/
        indices = list(range(len(self.sinks))) #indices for /sinks

        # Register randomly chosen resources, with some excluding connection api and generate answer strings
        for i in indices:
            self._register_sink(self.sinks[i])

        self.receiver_sinks = self.sinks

        for i in sink_indices:
            self._register_receiver_sink(self.sinks[i])

        # Receiver initial details
        self.receivers = [{'label': 'Test-node-2/receiver/palin', 'description': 'Mock receiver 1'},
                          {'label': 'Test-node-2/receiver/cleese', 'description': 'Mock receiver 2'},
                          {'label': 'Test-node-2/receiver/jones', 'description': 'Mock receiver 3'},
                          {'label': 'Test-node-2/receiver/chapman', 'description': 'Mock receiver 4'},
                          {'label': 'Test-node-2/receiver/idle', 'description': 'Mock receiver 5'},
                          {'label': 'Test-node-2/receiver/gilliam', 'description': 'Mock receiver 6'}]

        for receiver in self.receivers:
            receiver["id"] = str(uuid.uuid4()) + '/'
            

        # Generate indices of self.receivers to be registered and some of those to be non connectable
        receiver_indices = self._generate_random_indices(len(self.receivers))

        # Register randomly chosen resources, with some excluding connection api and generate answer strings
        for i in receiver_indices:
            self._register_receiver(self.receivers[i])

    def load_resource_data(self):
        """Loads test data from files"""
        result_data = dict()
        resources = ["sender", "receiver", "sink", "id-list", "properties"]
        for resource in resources:
            with open("test_data/JTNM/{}.json".format(resource)) as resource_data:
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

    def _create_receiver_json(self, receiver):
        # Register receiver
        receiver_data = deepcopy(self.test_data["receiver"])
        receiver_data["id"] = receiver["id"]

        return receiver_data

    def _register_receiver(self, receiver, codes=[201], fail=Test.FAIL):
        """
        Perform POST requests on the Registration API to create receiver registration
        Assume that Node has already been registered
        Use to create receiver [code=201] or to update existing receiver [code=200]
        """
        # use the test data as a template for creating new resources

        # Register receiver
        receiver_data = deepcopy(self.test_data["receiver"])
        receiver_data["id"] = receiver["id"]
        self.post_resource(self, "receiver", receiver_data, codes=codes, fail=fail)

    def _delete_receiver(self, receiver):
        
        del_url = self.mock_registry_base_url + 'x-nmos/registration/v1.3/resource/receivers/' + receiver['id']
        
        valid, r = self.do_request("DELETE", del_url)
        if not valid:
            # Hmm - do we need these exceptions as the registry is our own mock registry?
            raise NMOSTestException(fail(test, "Registration API returned an unexpected response: {}".format(r)))


    def _register_sink(self, sink, codes=[201], fail=Test.FAIL):
        """
        Perform POST requests on the Registration API to create sink registration
        Assume that Node has already been registered
        Use to create sink [code=201] or to update existing sink [code=200]
        """
        # use the test data as a template for creating new resources

        # Register sink
        sink_data = deepcopy(self.test_data["sink"])
        sink_data["id"] = sink["id"]
        self.post_resource(self, "sink", sink_data, codes=codes, fail=fail)
        
    def _delete_sink(self, sink):
        
        del_url = self.mock_registry_base_url + 'x-nmos/registration/v1.0/resource/sinks/' + sink['id']
        
        valid, r = self.do_request("DELETE", del_url)
        if not valid:
            # Hmm - do we need these exceptions as the registry is our own mock registry?
            raise NMOSTestException(fail(test, "Registration API returned an unexpected response: {}".format(r)))
    
    def _register_receiver_sink(self, receiver_sink, codes=[201], fail=Test.FAIL):

        #Register sinks for receiver
        receiver_sink_data = deepcopy(self.test_data["id-list"])
        receiver_sink_data["id"] = receiver_sink["id"]
        self.post_resource(self, "id-list", receiver_sink_data, codes=codes, fail=fail)


    def pre_tests_message(self):
        """
        Introduction to IS-11 Test Suite
        """
        question = 'These tests validate a NMOS Controller’s ability to query a Node’s IS-11 \
        endpoints and display correct information about the NMOS Sink Metadata Processing. \
        These tests should help users to receive EDID information and monitor characteristics.\n \
        it is also possible to reach the Registry via the following URL:\n\n' + self.mock_registry_base_url + 'x-nmos/query/v1.0\n\n \
        Please click \'Next\' \n'

        try:
            self._invoke_testing_facade(question, [], test_type="action", timeout=600)

        except TestingFacadeException as e:
            # pre_test_introducton timed out
            pass

    def post_tests_message(self):
        """
        IS-11 Test Suite testing complete!
        """
        question =  'IS-11 Test Suite testing complete!\r\n\r\nPlease press \'Next\' to exit the tests'

        try:
            self._invoke_testing_facade(question, [], test_type="action", timeout=10)

        except TestingFacadeException as e:
            # post_test_introducton timed out
            pass
    
    def test_01(self, test):
        """
        When the user runs this test the Controller must retrieve all the sinks for the mock node
        """

        try:
            # Question - 1: Sink ids
            question = 'Select which of the following sink ids are registred.\n\n' \
            'Once you have choose sink ids click the \'Next\' button. \n ' \

            possible_answers = [{'answer_id': 'answer_'+str(i), 'label': s['label'], 'description': s['description'], 'id': s['id'], 'answer_str': s['answer_str']} for i, s in enumerate(self.sinks)]
            expected_answers = ['answer_'+str(i) for i, s in enumerate(self.sinks) if len(s['id']) != 0]

            actual_answers = self._invoke_testing_facade(question, possible_answers, test_type="checkbox")['answer_response']

            if len(actual_answers) != len(expected_answers):
                return test.FAIL('Incorrect sink identified')
            else:
                for answer in actual_answers:
                    if answer not in expected_answers:
                        return test.FAIL('Incorrect sink identified')

            return test.PASS('All devices correctly identified')
        except TestingFacadeException as e:
            return test.UNCLEAR(e.args[0])



    def test_02(self, test):
        """
        When the user runs this test the Controller must reetrieve the binary EDID file for the specified sink and open it with a program that displays the parsed EDID
        """

        # Question - 2: Manufacturer year
        question = 'Go to http://192.168.59.64:5102/x-nmos/query/v1.0/sinks/<sink_id>/edid to open edid file for specified sink \n\n' \
        'Although the test AMWA IS-04 Registry should be discoverable via DNS-SD, for the purposes of developing this testing framework ' \
        'it is also possible to reach the Registry via the following URL:\n\n' + self.mock_registry_base_url + 'x-nmos/query/v1.0\n\n ' \

        #####LOGIC FUNCTION

        try:
            self._invoke_testing_facade(question, [], test_type="action", timeout=600)

        except TestingFacadeException as e:
            # pre_test_introducton timed out
            pass

    def test_03(self, test):
        """
        When the user runs this test the Controller must retrieve the EDID properties for the specified sink 
        """
        try:
            # Question - 3: Manufacturer
            question = 'What is the Manufacturer.\n\n' \
            'Select manufacturer and click the \'Next\' button. \n ' \
            'Although the test AMWA IS-04 Registry should be discoverable via DNS-SD, for the purposes of developing this testing framework ' \
            'it is also possible to reach the Registry via the following URL:\n\n' + self.mock_registry_base_url + 'x-nmos/query/v1.0\n\n ' \

            #####LOGIC FUNCTION


        except TestingFacadeException as e:
            return test.UNCLEAR(e.args[0]) 

