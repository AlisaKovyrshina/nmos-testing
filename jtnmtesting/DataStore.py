import time

class DataStore:
    """
    Store json with test question details
    """

    def __init__(self):
        self.name = None
        self.description = None
        self.question = None
        self.answers = None
        self.time_sent = None
        self.url_for_response = None
        self.answer_response = None
        self.time_answered = None
        self.status = 'Empty'

    def clear(self):
        self.name = None
        self.description = None
        self.question = None
        self.answers = None
        self.time_sent = None
        self.url_for_response = None
        self.answer_response = None
        self.time_answered = None
        self.status = 'Empty'

    def getStatus(self):
        return self.status

    def setJson(self, json_str):
        if json_str['name'] == '':
            self.clear()
        else:
            self.status = 'Test'
            self.name = json_str['name']
            self.description = json_str['description']
            self.question = json_str['question']
            self.answers = json_str['answers']
            self.time_sent = json_str['time_sent']
            self.url_for_response = 'http://' + json_str['url_for_response'] + '/jtnm_response'
            self.answer_response = json_str['answer_response']
            self.time_answered = json_str['time_answered']

    def getJson(self):
        json = {
            'name': self.name,
            'description': self.description,
            'question': self.question,
            'answers': self.answers,
            'time_sent': self.time_sent,
            'url_for_response': self.url_for_response,
            'answer_response': self.answer_response,
            'time_answered': self.time_answered
        }
        return json

    def setAnswer(self, answer):
        self.answer_response = answer
        self.time_answered = time.time()

    def getTestDetails(self):
        return self.name, self.description

    def getQandA(self):
        return self.question, self.answers

    def getURL(self):
        return self.url_for_response

data = DataStore()
