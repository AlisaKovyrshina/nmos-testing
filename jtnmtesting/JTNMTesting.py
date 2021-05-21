import random
import requests
import json
from flask import Flask, render_template, make_response, abort, request, Response, url_for
from .DataStore import data


CACHEBUSTER = random.randint(1, 10000)

app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'GET':
        if data.getStatus() == 'Test':
            r = make_response(render_template("index.html", question=data.getQuestion(), 
                                              answers=data.getAnswers(), name=data.getName(), 
                                              description=data.getDescription(), response_url=data.getUrl(),
                                              all_data=data.getJson(), cachebuster=CACHEBUSTER))
        else:
            r = make_response(render_template("index.html", question=None, answers=None, 
                                              name=None, description=None, cachebuster=CACHEBUSTER))
        r.headers['Cache-Control'] = 'no-cache, no-store'
        return r

    else:
        form = request.form.to_dict()

        if 'answer' in form:
            json_data = json.loads(form['all_data'])
            json_data['answer_response'] = form['answer']

            # POST to x-nmos/client-testing/ with new data
            valid, response = do_request('POST', "http://" + request.headers.get("Host") + url_for('.jtnm_tests'), json=json_data)
            # POST to test suite to confirm answer available
            valid, response = do_request('POST', form['response_url'], json={})

        else:
            return False, "No answer submitted"

        return 'Answer set'

@app.route('/x-nmos/client-testing/', methods=['GET', 'POST'], strict_slashes=False)
def jtnm_tests():
    if request.method == 'POST':
        # Should be json from Test Suite with questions
        json_list = ['name', 'description', 'question', 'answers', 'time_sent', 'url_for_response']

        if 'clear' in request.json and request.json['clear'] == 'True':
            # End of current tests, clear data store
            data.clear()
        elif 'answer_response' in request.json and request.json['answer_response'] != "":
            # Answer was given, check details compared to question POST to verify answering correct question
            for entry in json_list:
                method = getattr(data, 'get' + entry.split('_')[0].capitalize())
                current = method()
                if current != request.json[entry]:
                    return False, "{} : {} doesn't match current question details".format(entry, request.json[entry])
            # All details are consistent so update the data store to contain the answer
            data.setAnswer(request.json['answer_response'])
            # POST to test suite to indicate answer has been set
            valid, response = do_request('POST', request.json['url_for_response'], json={})
        else:
            # Should be a new question
            for entry in json_list:
                if entry not in request.json:
                    return False, "Missing {}".format(entry)
            # All required entries are present so update data
            data.setJson(request.json)
        return 'OK'

    elif request.method == 'GET':
        return Response(data.getJson(), mimetype='application/json')

    return "Here"

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
