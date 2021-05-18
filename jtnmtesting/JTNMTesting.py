import random
import requests
import json
from flask import Flask, render_template, make_response, abort, request, Response
from .DataStore import data


CACHEBUSTER = random.randint(1, 10000)

app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'GET':
        if data.getStatus() == 'Test':
            question, answers = data.getQandA()
            name, description = data.getTestDetails()
            r = make_response(render_template("index.html", question=question, answers=answers, 
                                              name=name, description=description, cachebuster=CACHEBUSTER))
        else:
            r = make_response(render_template("index.html", question=None, answers=None, 
                                              name=None, description=None, cachebuster=CACHEBUSTER))
        r.headers['Cache-Control'] = 'no-cache, no-store'
        return r

    else:
        form = request.form.to_dict()
        data.setAnswer(form['answer'])

        return Response(json.dumps(data.getJson()), mimetype='application/json')

@app.route('/x-nmos/client-testing/', methods=['GET', 'POST'], strict_slashes=False)
def jtnm_tests():
    if request.method == 'POST':
        # Should be json from Test Suite with questions
        data.setJson(request.json)
        return 'Request received'

    elif request.method == 'GET':
        return Response(json.dumps(data.getJson()), mimetype='application/json')

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
