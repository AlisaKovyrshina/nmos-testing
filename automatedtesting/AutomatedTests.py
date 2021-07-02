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

import random
import requests
import json
from threading import Thread
from flask import Flask, request, Response
from DataStore import data
from JTNMAutoTests import tests


app = Flask(__name__)

@app.route('/x-nmos/client-testing/<version>', methods=['POST'], strict_slashes=False)
def jtnm_tests_post(version):
    # Should be json from Test Suite with questions
    json_list = ['test_type', 'name', 'description', 'question', 'answers', 'time_sent', 'url_for_response']

    if 'clear' in request.json and request.json['clear'] == 'True':
        # End of current tests, clear data store
        data.clear()
    else:
        # Should be a new question
        for entry in json_list:
            if entry not in request.json:
                return False, "Missing {}".format(entry)
        # All required entries are present so update data
        data.setJson(request.json)
        # Run test in new thread
        thread = Thread(target=execute_test)
        thread.start()
    return 'OK'

@app.route('/x-nmos/client-testing/', methods=['GET'], strict_slashes=False)
def jtnm_tests_get():
    return Response(data.getJson(), mimetype='application/json')


def do_request(method, url, **kwargs):
    """Perform a basic HTTP request with appropriate error handling"""
    try:
        s = requests.Session()
        # The only place we add headers is auto OPTIONS for CORS, which should not check Auth
        if "headers" in kwargs and kwargs["headers"] is None:
            del kwargs["headers"]

        req = requests.Request(method, url, **kwargs)
        prepped = s.prepare_request(req)
        settings = s.merge_environment_settings(prepped.url, {}, None, None, None)
        response = s.send(prepped, timeout=1, **settings)
        if prepped.url.startswith("https://"):
            if not response.url.startswith("https://"):
                return False, "Redirect changed protocol"
            if response.history is not None:
                for res in response.history:
                    if not res.url.startswith("https://"):
                        return False, "Redirect changed protocol"
        return True, response
    except requests.exceptions.Timeout:
        return False, "Connection timeout"
    except requests.exceptions.TooManyRedirects:
        return False, "Too many redirects"
    except requests.exceptions.ConnectionError as e:
        return False, str(e)
    except requests.exceptions.RequestException as e:
        return False, str(e)

def execute_test():
    """
    After test data has been sent to x-nmos/client-testing figure out which test was sent.
    Call relevant test method and retrieve answers
    Update json and send back to test suite
    """
    test_name = data.getName()
    test_type = data.getTest()
    answers = data.getAnswers()

    if test_name.startswith("test_"):
        method = getattr(tests, test_name)
        if callable(method):
            print(" * Running " + test_name)
            test_result = method()

            # Verify answer given matches one of those sent
            if test_type == 'action':
                pass
            elif test_type == 'radio':
                # Should be single answer matching one of the given answers
                if test_result not in answers:
                    print("* Invalid answer returned for ", test_name)
                    test_result = 'invalid answer'
            elif test_type == 'checkbox':
                # Verify multiple answers in answers and return list
                for answer in test_result:
                    if answer not in answers:
                        print(" ! Invalid answer returned for ", test_name)
                        test_result = 'invalid answer'
            data.setAnswer(test_result)

    elif test_name == 'pre_tests_message':
        # Beginning of test set, return next to confirm starting tests
        data.setAnswer('Next')
    
    elif test_name == 'post_tests_message':
        # End of test set, return next to confirm end and close webdriver window
        data.setAnswer('Next')
        tests.driver.close()

    else:
        # Not a recognised part of test suite
        return

    # POST response back to test suite with answer_response
    do_request('POST', data.getUrl(), json=json.loads(data.getJson()))
    return 


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5001)