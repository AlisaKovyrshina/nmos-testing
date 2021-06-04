## Installation & Usage

`python3 ClientFacade.py`

Should be available on 127.0.0.1:5001

## Test processes

### Semi-automated tests:

1. Run nmos-test.py and choose JTNM Client Tests 
3. Run ClientTesting.py to launch client facade. Facade will periodically check whether tests have appeared
4. On nmos testing enter IP address and Port where Client testing is running
5. Choose tests and click Run
6. Test suite POSTs json with test details to Client testing API endpoint '/x-nmos/client-testing'

```
{
    'test_type': 'radio' (single answer), 'checkbox' (potentially multiple answers) or 'action' (instructions only)
    'name': name from test method,
    'description': docstring from test method,
    'question': question from question variable in test method (str),
    'answers': answers from answers variable in test method (list),
    'time_sent': time.time(),
    'timeout': time in seconds for test suite to wait for answer to be POSTed to url_for_response
    'url_for_response': url of test suite API endpoint to signal an answer has been posted,
    'answer_response': empty string,
    'time_answered': empty string
}
```
    Then waits for period of timeout to receive a POST to the url_for_response API endpoint 

7. Client testing saves the json in a data store and facade presents question, answers and timer.
8. Test facade will POST to itself on submitting an answer then POST to url_for_response with updated json including chosen answer(s) in answer_response
9. Test suite url_for_response endpoint saves json and signals to test that answer has been received. Answer is verified and result registered.
10. Test suite moves on to next test and repeats 6-9 until all chosen tests are completed.
11. After last test, test suite will POST a clear request to the client testing API to empty the data store
12. Results are displayed on the test suite

### Fully automated tests:
Will need to have endpoint for 'x-nmos/client-testing' to receive questions and some method of storing the json. Then add the answer_response and POST back to url_for_response