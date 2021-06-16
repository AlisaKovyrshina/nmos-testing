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
            return 'OK'
        answer_available.set()

    return 'OK'

class JTNMTest(GenericTest):
    """
    Testing initial set up of new test suite for controller testing
    """
    def __init__(self, apis, registries, dns_server):
        # JRT: remove the spec_path parameter to prevent GenericTest from attempting to download RAML from repo
        apis[JTNM_API_KEY].pop("spec_path", None)
        GenericTest.__init__(self, apis)
        self.authorization = False  # System API doesn't use auth, so don't send tokens in every request
        self.primary_registry = registries[1]
        self.dns_server = dns_server
        self.registry_mdns = []
        self.zc = None
        self.zc_listener = None
        self.registry_location = ''
        self.question_timeout = 600 # seconds
        self.test_data = self.load_resource_data()
        self.test_resources = [] # Reference store for new resources created within a test

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
            priority = 100

            # Add advertisement for primary registry
            info = self._registry_mdns_info(self.primary_registry.get_data().port, priority)
            self.registry_mdns.append(info)

        # Reset registry to clear previous heartbeats, etc.
        self.primary_registry.reset()
        self.primary_registry.enable()
        self.registry_location = 'http://' + get_default_ip() + ':' + str(self.primary_registry.get_data().port) + '/'

        if CONFIG.DNS_SD_MODE == "multicast":
            self.zc.register_service(self.registry_mdns[0])

        print('Registry should be available at ' + self.registry_location)

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

        self.registry_location = ''
        self.registry_mdns = []
    
    def set_up_test(self):
        """Setup performed before EACH test"""
        self.test_resources.clear()
        self.primary_registry.reset()
        self.primary_registry.enable()

    def execute_tests(self, test_names):
        """Perform tests defined within this class"""
        self.pre_tests_message()

        for test_name in test_names:
            self.execute_test(test_name)

        self.post_tests_message()

    def execute_test(self, test_name):
        """Perform a test defined within this class"""
        self.test_individual = (test_name != "all")

        # Run automatically defined tests
        if test_name in ["auto", "all"] and not self.disable_auto:
            print(" * Running basic API tests")
            self.result += self.basics()

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

    def _invoke_client_facade(self, question, answers, test_type, timeout=None):

        global clientfacade_answer_json

        # Get the name of the calling test method to use as an identifier
        test_method_name = inspect.currentframe().f_back.f_code.co_name

        method = getattr(self, test_method_name)

        question_timeout = timeout if timeout else self.question_timeout

        json_out = {
            "test_type": test_type,
            "name": test_method_name,
            "description": inspect.getdoc(method),
            "question": question,
            "answers": answers,
            "time_sent": time.time(),
            "timeout": question_timeout,
            "url_for_response": "http://" + request.headers.get("Host") + CALLBACK_ENDPOINT,
            "answer_response": "",
            "time_answered": ""
        }
        # Send questions to jtnm testing API endpoint then wait
        valid, response = self.do_request("POST", self.apis[JTNM_API_KEY]["url"], json=json_out)

        if not valid:
            raise ClientFacadeException("Problem contacting Client Façade: " + response)

        # Wait for answer available signal or 120s then move on
        answer_available.clear()
        get_json = answer_available.wait(timeout=question_timeout)
        
        if get_json == False:
            raise ClientFacadeException("Test timed out")

        # JSON reponse to question is set in in clientfacade_answer_json global variable (Hmmm)
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

        # TODO: Add another test which checks support for parsing CSV string in api_ver
        txt = {'api_ver': api_ver, 'api_proto': api_proto, 'pri': str(priority), 'api_auth': str(api_auth).lower()}

        service_type = "_nmos-register._tcp.local."

        info = ServiceInfo(service_type,
                           "NMOSTestSuite{}{}.{}".format(port, api_proto, service_type),
                           addresses=[socket.inet_aton(ip)], port=port,
                           properties=txt, server=hostname)
        return info
    
    def load_resource_data(self):
        """Loads test data from files"""
        api = self.apis[JTNM_API_KEY]
        result_data = dict()
        resources = ["node", "device", "source", "flow", "sender", "receiver", "device_nocontrol"]
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
            reg_url = self.registry_location + 'x-nmos/registration/v1.3/'

        if not codes:
            codes = [200, 201]

        valid, r = self.do_request("POST", reg_url + "resource", json={"type": type, "data": data}, headers=headers)
        if not valid:
            raise NMOSTestException(fail(test, "Registration API returned an unexpected response: {}".format(r)))

        self.test_resources.append(data)

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

    def post_super_resources_and_resource(self, test, type, description, sender_id=None, receiver_id=None, nocontrol=False, fail=Test.FAIL):
        """
        Perform POST requests on the Registration API to create the super-resource registrations
        for the requested type, before performing a POST request to create that resource registration
        """

        # use the test data as a template for creating new resources
        data = deepcopy(self.test_data[type])
        data["id"] = str(uuid.uuid4())
        data["description"] = description

        device_type = "device_nocontrol" if nocontrol else "device"
        if type == "device_nocontrol":
           type = "device"

        if type == "node":
            pass
        elif type == "device":
            node = self.post_super_resources_and_resource(test, "node", description, sender_id, receiver_id, nocontrol, fail=Test.UNCLEAR)
            data["node_id"] = node["id"]
            data["senders"] = [ sender_id ] if sender_id else [] 
            data["receivers"] = [ receiver_id ] if receiver_id else [] 
        elif type == "source":
            device = self.post_super_resources_and_resource(test, device_type, description, sender_id, receiver_id, nocontrol, fail=Test.UNCLEAR)
            data["device_id"] = device["id"]
        elif type == "flow":
            source = self.post_super_resources_and_resource(test, "source", description, sender_id, receiver_id, nocontrol, fail=Test.UNCLEAR)
            data["device_id"] = source["device_id"]
            data["source_id"] = source["id"]
            # since device_id is v1.1, downgrade
            data = self.downgrade_resource(type, data, self.apis[REG_API_KEY]["version"])
        elif type == "sender":
            sender_id = str(uuid.uuid4())
            data["id"] = sender_id
            device = self.post_super_resources_and_resource(test, device_type, description, sender_id, receiver_id, nocontrol, fail=Test.UNCLEAR)
            data["device_id"] = device["id"]
            data["flow_id"] = str(uuid.uuid4())  # or post a flow first and use its id here?
        elif type == "receiver":
            receiver_id = str(uuid.uuid4())
            data["id"] = receiver_id
            device = self.post_super_resources_and_resource(test, device_type, description, sender_id, receiver_id, nocontrol, fail=Test.UNCLEAR)
            data["device_id"] = device["id"]

        self.post_resource(test, type, data, codes=[201], fail=fail)

        return data

    def randomise_answers(self, no_of_answers, max_choices=1):
        """
        no_of_answers: Length of possible_answers list
        max_choices: default 1 for radio answers. No. of list indices to be returned
        max_choices > 1 will return list of random number of list indices up to the value given
        """
        if max_choices == 1:
            return [random.randint(0, no_of_answers-1)]
        elif max_choices > 1:
            choices = random.randint(1, max_choices)
            return random.sample(list(range(0, no_of_answers)), k=choices)

    def pre_tests_message(self):
        """
        Introduction to JT-NM Tested Test Suite
        """
        question =  'This set of tests validates a Broadcast Controller under Test’s (BCuT) ability to query an IS-04 ' \
        'Registry with the IS-04 Query API and to control a Media Node using the IS-05 Connection ' \
        'Management API.\r\n\r\nA Test AMWA IS-04 v1.2/1.3 reference registration is available on the network, ' \
        'and advertised in the DNS server via unicast DNS-SD\r\n\r\n' \
        'Although the test AMWA IS-04 registration service should be discoverable via DNS-SD, for the purposes of developing this testing framework ' \
        'it is also possible to reach the service via the following URL:\r\n\r\n' + self.registry_location + 'x-nmos/query/v1.3\r\n\r\n' \
        'Once the BCuT has located the test AMWA IS-04 registration service, please click \'Next\''
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
            # pre_test_introducton timed out
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
        # Devices to be added to registry
        sender_labels = ['Test-node-1/sender/a1', 'Test-node-1/sender/b0', 'Test-node-1/sender/b1']
        receiver_labels = ['Test-node-2/receiver/s0', 'Test-node-2/receiver/s1', 'Test-node-2/receiver/t0']

        # Populate the registry with senders and recievers
        for sender_label in sender_labels:
            device_data = self.post_super_resources_and_resource(test, "sender", "test_02")
            device_data['label'] = sender_label
            self.post_resource(test, "sender", device_data, codes=[200])

        for receiver_label in receiver_labels:
            device_data = self.post_super_resources_and_resource(test, "receiver", "test_02")
            device_data['label'] = receiver_label
            self.post_resource(test, "receiver", device_data, codes=[200])
            
        try:
            # Question 1 connection
            question = 'Use the BCuT to browse the senders and receivers on the discovered via the selected IS-04 Query API.\n' \
            'Once you have finished browsing click \'Next\'. Successful browsing of the registration service will be logged by the test framework.\n'
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
        MAX_SENDERS = 3

        # Potential senders to be added to registry
        sender_labels = ['Test-node-1/sender/gilmour', 'Test-node-1/sender/waters', 'Test-node-1/sender/wright', 'Test-node-1/sender/mason', 'Test-node-1/sender/barrett']
        # Pick up to MAX_DEVICES labels
        sender_answer_index = self.randomise_answers(len(sender_labels), MAX_SENDERS)
        sender_answer_list = [sender_labels[i] for i in sender_answer_index]

        # Post new resources to registry
        for i in sender_answer_index:
            device_data = self.post_super_resources_and_resource(test, "sender", "test_03")
            device_data['label'] = sender_labels[i]
            self.post_resource(test, "sender", device_data, codes=[200])

        try:
            # Check senders 
            question = 'The Query API should be able to discover all the senders that are registered in the Registry.\n' \
            'Refresh the BCuT\'s view of the registration service and carefully select the senders that are available from the following list.' 
            possible_answers = sender_labels

            actual_answer = self._invoke_client_facade(question, possible_answers, test_type="checkbox")

            if len(actual_answer) != len(sender_answer_list):
                return test.FAIL('Incorrect sender identified')
            else:
                for answer in actual_answer:
                    if answer not in sender_answer_list:
                        return test.FAIL('Incorrect sender identified')

            return test.PASS('All devices correctly identified')
        except ClientFacadeException as e:
            return test.UNCLEAR(e.args[0])


    def test_04(self, test):
        """
        Query API should be able to discover all the receivers that are registered in the Registry
        """
        MAX_RECEIVERS = 3

        # Potential devices to be added to registry
        receiver_labels = ['Test-node-2/receiver/palin', 'Test-node-2/receiver/cleese', 'Test-node-2/receiver/jones', 'Test-node-2/receiver/chapman', 'Test-node-2/receiver/idle', 'Test-node-2/receiver/gilliam']
        # Pick up to MAX_DEVICES labels
        receiver_answer_index = self.randomise_answers(len(receiver_labels), MAX_RECEIVERS)
        receiver_answer_list = [receiver_labels[i] for i in receiver_answer_index]  

        # Post new resources to registry
        for i in receiver_answer_index:
            device_data = self.post_super_resources_and_resource(test, "receiver", "test_04")
            device_data['label'] = receiver_labels[i]
            self.post_resource(test, "receiver", device_data, codes=[200])

        try:
            # Check receivers 
            question = 'The Query API should be able to discover all the receivers that are registered in the Registry.\n' \
            'Refresh the BCuT\'s view of the registration service and carefully select the senders that are available from the following list.'
            possible_answers = receiver_labels

            actual_answer = self._invoke_client_facade(question, possible_answers, test_type="checkbox")

            if len(actual_answer) != len(receiver_answer_list):
                return test.FAIL('Incorrect receiver identified')
            else:
                for answer in actual_answer:
                    if answer not in receiver_answer_list:
                        return test.FAIL('Incorrect receiver identified')

            return test.PASS('All devices correctly identified')
        except ClientFacadeException as e:
            return test.UNCLEAR(e.args[0])

    def test_05(self, test):
        """
        Reference Sender is put offline; Reference Sender is put back online
        """

        return test.DISABLED("Test not yet implemented")

    def test_06(self, test):
        """
        Identify which Receiver devices are controllable via IS-05
        """

        return test.DISABLED("Test not yet implemented")

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
  