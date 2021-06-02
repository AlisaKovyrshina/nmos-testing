import time
import json
import socket
import uuid
import requests
import inspect
import threading
import random
from time import sleep
from copy import deepcopy
from urllib.parse import parse_qs, urlparse
from dnslib import QTYPE
from OpenSSL import crypto
from requests.sessions import Request
from zeroconf_monkey import ServiceBrowser, ServiceInfo, Zeroconf

from ..GenericTest import GenericTest, NMOSTestException, NMOSInitException
from ..IS04Utils import IS04Utils
from .. import Config as CONFIG
from zeroconf_monkey import ServiceBrowser, Zeroconf
from ..MdnsListener import MdnsListener
from ..TestHelper import get_default_ip, load_resolved_schema
from ..TestResult import Test

from flask import Flask, render_template, make_response, abort, Blueprint, flash, request, Response, session
import random

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
    test_list = {}
    
    def __init__(self, apis, registries, dns_server):
        # JRT: overwrite the spec_path parameter to prevent GenericTest from attempting to download RAML from repo
        apis["client-testing"]["spec_path"] = None
        GenericTest.__init__(self, apis)
        self.authorization = False  # System API doesn't use auth, so don't send tokens in every request
        self.primary_registry = registries[1]
        self.registries = registries[1:]
        self.dns_server = dns_server
        self.jtnm_url = self.apis[JTNM_API_KEY]["url"]
        self.registry_basics_done = False
        self.registry_basics_data = []
        self.registry_primary_data = None
        self.registry_invalid_data = None
        self.node_basics_data = {
            "self": None, "devices": None, "sources": None,
            "flows": None, "senders": None, "receivers": None
        }
        self.zc = None
        self.zc_listener = None
        self.is04_utils = IS04Utils(self.jtnm_url)
        self.registry_location = ''
        self.question_timeout = 30 # seconds
        self.test_data = self.load_resource_data()

    def set_up_tests(self):
        print('Setting up tests')
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

        if self.registry_basics_done:
            return

        if CONFIG.DNS_SD_MODE == "multicast":
            registry_mdns = []
            priority = 100

            # Add advertisement for primary and failover registries
            for registry in self.registries[0:-1]:
                info = self._registry_mdns_info(registry.get_data().port, priority)
                registry_mdns.append(info)
                priority += 10

            # Add the final real registry advertisement
            info = self._registry_mdns_info(self.registries[-1].get_data().port, priority)
            registry_mdns.append(info)

        # Reset all registries to clear previous heartbeats, etc.
        for registry in self.registries:
            registry.reset()

        self.primary_registry.enable()
        self.registry_location = 'http://' + get_default_ip() + ':' + str(self.primary_registry.get_data().port) + '/'

        if CONFIG.DNS_SD_MODE == "multicast":
            self.zc.register_service(registry_mdns[0])
            self.zc.register_service(registry_mdns[1])
            self.zc.register_service(registry_mdns[2])

        # Once registered, advertise all other registries at different (ascending) priorities
        for index, registry in enumerate(self.registries[1:]):
            registry.enable()

        if CONFIG.DNS_SD_MODE == "multicast":
            for info in registry_mdns[3:]:
                self.zc.register_service(info)

        print('Registry should be available at ' + self.registry_location)

        # Add mock data to the resistry
        # self.primary_registry.add_resource("device", "device0", "{id: 'device0'}")
        # self.primary_registry.add_resource("device", "device1", "{id: 'device1'}")
        # self.primary_registry.add_resource("device", "device2", "{id: 'device2'}")

    def tear_down_tests(self):
        print('Tearing down tests')
        # Clean up mDNS advertisements and disable registries
        # if CONFIG.DNS_SD_MODE == "multicast":
        #     for info in registry_mdns:
        #         self.zc.unregister_service(info)

        # Reset the state of the client testing façade
        self.do_request("POST", self.apis[JTNM_API_KEY]["url"], json={"clear": "True"})

        for index, registry in enumerate(self.registries):
            registry.disable()

        self.registry_basics_done = True
        for registry in self.registries:
            self.registry_basics_data.append(registry.get_data())

        self.registry_primary_data = self.registry_basics_data[0]

        if self.zc:
            self.zc.close()
            self.zc = None
        if self.dns_server:
            self.dns_server.reset()

        self.registry_location = ''
        JTNMTest.test_list = {}

    def execute_tests(self, test_names):
        """Perform tests defined within this class"""

        for test_name in test_names:
            self.primary_registry.reset()
            self.primary_registry.enable()
            self.execute_test(test_name)
    
    def invoke_client_facade(self, test, question, answers, timeout=None):

        global clientfacade_answer_json

        method = getattr(self, test)

        question_timeout = timeout if timeout else self.question_timeout

        json_out = {
            "name": test,
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

    def get_jtnm_json(self):
        """
        GET request to jtnm test suite to check for answer in JSON
        """
        valid, response = self.do_request("GET", self.apis[JTNM_API_KEY]["url"])
        return response.json()

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
    
    def do_registry_basics_prereqs(self):
        """Advertise a registry and collect data from any Nodes which discover it"""

        if self.registry_basics_done:
            return

        if CONFIG.DNS_SD_MODE == "multicast":
            registry_mdns = []
            priority = 100

            # Add advertisement for primary and failover registries
            for registry in self.registries[0:-1]:
                info = self._registry_mdns_info(registry.get_data().port, priority)
                registry_mdns.append(info)
                priority += 10

            # Add the final real registry advertisement
            info = self._registry_mdns_info(self.registries[-1].get_data().port, priority)
            registry_mdns.append(info)

        # Reset all registries to clear previous heartbeats, etc.
        for registry in self.registries:
            registry.reset()

        self.primary_registry.enable()

        if CONFIG.DNS_SD_MODE == "multicast":
            self.zc.register_service(registry_mdns[0])
            self.zc.register_service(registry_mdns[1])
            self.zc.register_service(registry_mdns[2])

        # Once registered, advertise all other registries at different (ascending) priorities
        for index, registry in enumerate(self.registries[1:]):
            registry.enable()

        if CONFIG.DNS_SD_MODE == "multicast":
            for info in registry_mdns[3:]:
                self.zc.register_service(info)

    def load_resource_data(self):
        """Loads test data from files"""
        api = self.apis[JTNM_API_KEY]
        result_data = dict()
        resources = ["node", "device", "source", "flow", "sender", "receiver"]
        for resource in resources:
            with open("test_data/IS0402/v1.3_{}.json".format(resource)) as resource_data:
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
            # TODO check why resource/{}s/{} in ISO402 version. Fails here, works there
            path = "{}resource/{}/{}".format(urlparse(reg_url).path, type, data["id"])
            location = r.headers["Location"]
            if path not in location:
                raise NMOSTestException(fail(test, "Registration API 'Location' response header is incorrect: "
                                             "Location: {}".format(location)))
            if not location.startswith("/") and not location.startswith(self.protocol + "://"):
                raise NMOSTestException(fail(test, "Registration API 'Location' response header is invalid for the "
                                             "current protocol: Location: {}".format(location)))

        return location, timestamp

    def post_super_resources_and_resource(self, test, type, description, fail=Test.FAIL):
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
            node = self.post_super_resources_and_resource(test, "node", description, fail=Test.UNCLEAR)
            data["node_id"] = node["id"]
            data["senders"] = []  # or add an id here, and use it when posting the sender?
            data["receivers"] = []  # or add an id here, and use it when posting the receiver?
        elif type == "source":
            device = self.post_super_resources_and_resource(test, "device", description, fail=Test.UNCLEAR)
            data["device_id"] = device["id"]
        elif type == "flow":
            source = self.post_super_resources_and_resource(test, "source", description, fail=Test.UNCLEAR)
            data["device_id"] = source["device_id"]
            data["source_id"] = source["id"]
            # since device_id is v1.1, downgrade
            data = self.downgrade_resource(type, data, self.apis[REG_API_KEY]["version"])
        elif type == "sender":
            device = self.post_super_resources_and_resource(test, "device", description, fail=Test.UNCLEAR)
            data["device_id"] = device["id"]
            data["flow_id"] = str(uuid.uuid4())  # or post a flow first and use its id here?
        elif type == "receiver":
            device = self.post_super_resources_and_resource(test, "device", description, fail=Test.UNCLEAR)
            data["device_id"] = device["id"]

        self.post_resource(test, type, data, codes=[201], fail=fail)

        return data

    def test_01(self, test):
        """
        Example test 1
        """
        try:
            question = 'What is your name?'
            possible_answers = ['Sir Robin of Camelot', 'Sir Galahad of Camelot', 'Arthur, King of the Britons']

            actual_answer = self.invoke_client_facade("test_01", question, possible_answers)

            if actual_answer == possible_answers[2]:
                return test.PASS('I didn\'t vote for him')
            else:
                return test.FAIL('Knight of the round table')
        except ClientFacadeException as e:
                return test.UNCLEAR(e.args[0])

    def test_02(self, test):
        """
        Example test 2
        """
        try:
            question = 'What is your Quest?'
            possible_answers = ['To find a shrubbery', 'To seek the Holy Grail']

            actual_answer = self.invoke_client_facade("test_02", question, possible_answers)

            if actual_answer == possible_answers[1]:
                return test.PASS('The Grail awaits')
            else:
                return test.FAIL('Ni')
        except ClientFacadeException as e:
            return test.UNCLEAR(e.args[0])

    def test_03(self, test):
        """
        Example test 3
        """
        try:
            question = 'What is your favourite colour?'
            possible_answers = ['Blue', 'Yellow']

            actual_answer = self.invoke_client_facade("test_03", question, possible_answers)

            if actual_answer == possible_answers[1]:
                return test.PASS('Off you go then')
            else:
                return test.FAIL('Ahhhhhhhhh')
        except ClientFacadeException as e:
            return test.UNCLEAR(e.args[0])


    def test_04(self, test):
        """
        Connect controller to mock registry and verify nodes
        """
        try:
            question = "Connect your controller to the Query API at " + self.registry_location + \
                       "x-nmos/query/v1.3 How many nodes are connected?"
            possible_answers = ['0', '1', '2', '3']

            actual_answer = self.invoke_client_facade("test_04", question, possible_answers, timeout=120)

            if actual_answer == possible_answers[0]:
                pass
            else:
                return test.FAIL('Incorrect number of nodes found')

            self.post_resource(test, "node")

            question = "How many nodes are connected now?"
            possible_answers = ['0', '1', '2', '3']
    
            actual_answer = self.invoke_client_facade("test_04", question, possible_answers, timeout=90)

            if actual_answer == possible_answers[1]:
                return test.PASS('Nodes and Devices in mock registry correctly identified')
            else:
                return test.FAIL('Incorrect number of nodes found')
        except ClientFacadeException as e:
            return test.UNCLEAR(e.args[0])

    def test_05(self, test):
        """
        Identify devices in registry
        """
        # Send randomly chosen device each test
        labels = ['Test device 1', 'Test device 2', 'Test device 3']
        device_data = self.post_super_resources_and_resource(test, "device", "test_05")
        device_data['label'] = random.choice(labels)
        self.post_resource(test, "device", device_data, codes=[200])

        try:
            question = "Connect your controller to the Query API at " + self.registry_location + \
                       "x-nmos/query/v1.3 How many devices are available?"
            possible_answers = ['0', '1', '2']

            actual_answer = self.invoke_client_facade("test_05", question, possible_answers, timeout=90)

            if actual_answer == possible_answers[1]:
                pass
            else:
                return test.FAIL('Incorrect number of devices found')

            question = "Which of the following devices are available?"
            # TODO turn this one into checkbox question
            possible_answers = labels

            actual_answer = self.invoke_client_facade("test_05", question, possible_answers, timeout=90)

            if actual_answer == device_data['label']:
                return test.PASS('Correctly identified device in registry')
            else:
                return test.FAIL('Incorrect device identified')
        except ClientFacadeException as e:
            return test.UNCLEAR(e.args[0])
