import time
import json
import socket
import requests
import inspect
import threading
from time import sleep
from urllib.parse import parse_qs
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
CACHEBUSTER = random.randint(1, 10000)

answer_available = threading.Event()


app = Flask(__name__)
TEST_API = Blueprint('test_api', __name__)


@TEST_API.route('/clientfacade_response', methods=['POST'])
def retrieve_answer():
    # Hmmmm, there must be a more elegant way to pass data between threads in a Flask application
    global clientfacade_answer_json

    if request.method == 'POST':
        clientfacade_answer_json = request.json
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
        self.registry_location = get_default_ip() + ':' + str(self.primary_registry.get_data().port)

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

        print('Registry should be available at http://' + get_default_ip() + ':' + str(self.primary_registry.get_data().port))

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
    
    def invoke_client_facade(self, test, question, answers):

        global clientfacade_answer_json

        method = getattr(self, test)

        json_out = {
            "name": test,
            "description": inspect.getdoc(method),
            "question": question,
            "answers": answers,
            "time_sent": time.time(),
            "timeout": self.question_timeout,
            "url_for_response": "http://" + request.headers.get("Host") + "/clientfacade_response",
            "answer_response": "",
            "time_answered": ""
        }
        # Send questions to jtnm testing API endpoint then wait
        valid, response = self.do_request("POST", self.apis[JTNM_API_KEY]["url"], json=json_out)

        # Wait for answer available signal or 120s then move on
        answer_available.clear()
        get_json = answer_available.wait(timeout=self.question_timeout)
        
        if get_json == False:
            return "Test timed out"    

        # JSON reponse to question is set in in clientfacade_answer_json global variable (Hmmm)
        # Basic integrity check for response json
        if clientfacade_answer_json['name'] is None:
            return "Integrity check failed: result format error: " +jsnon.dump(clientfacade_answer_json)

        if clientfacade_answer_json['name'] != json_out['name']:
            return "Integrity check failed: cannot compare result of " + json_out['name'] + " with expected result for " + clientfacade_answer_json['name']
            
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

    def test_01(self, test):
        """
        Example test 1
        """
        question = 'What is your name?'
        possible_answers = ['Sir Robin of Camelot', 'Sir Galahad of Camelot', 'Arthur, King of the Britons']

        actual_answer = self.invoke_client_facade("test_01", question, possible_answers)

        if actual_answer == possible_answers[2]:
            return test.PASS('I didn\'t vote for him')
        elif actual_answer == 'Test timed out':
            return test.UNCLEAR('Test timed out')
        else:
            return test.FAIL('Knight of the round table')

    def test_02(self, test):
        """
        Example test 2
        """
        question = 'What is your Quest?'
        possible_answers = ['To find a shrubbery', 'To seek the Holy Grail']

        actual_answer = self.invoke_client_facade("test_02", question, possible_answers)

        if actual_answer == possible_answers[1]:
            return test.PASS('The Grail awaits')
        elif actual_answer == 'Test timed out':
            return test.UNCLEAR('Test timed out')
        else:
            return test.FAIL('Ni')

    def test_03(self, test):
        """
        Example test 3
        """
        question = 'What is your favourite colour?'
        possible_answers = ['Blue', 'Yellow']

        actual_answer = self.invoke_client_facade("test_03", question, possible_answers)

        if actual_answer == possible_answers[1]:
            return test.PASS('Off you go then')
        elif actual_answer == 'Test timed out':
            return test.UNCLEAR('Test timed out')
        else:
            return test.FAIL('Ahhhhhhhhh')
