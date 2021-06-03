import time
import json

class DataStore:
    """
    Store json with test question details
    """

    def __init__(self):
        self.test_type = None
        self.name = None
        self.description = None
        self.question = None
        self.answers = None
        self.time_sent = None
        self.timeout = None
        self.url_for_response = None
        self.answer_response = None
        self.time_answered = None
        self.status = "Empty"

    def clear(self):
        self.test_type = None
        self.name = None
        self.description = None
        self.question = None
        self.answers = None
        self.time_sent = None
        self.timeout = None
        self.url_for_response = None
        self.answer_response = None
        self.time_answered = None
        self.status = "Empty"

    def getStatus(self):
        return self.status

    def setJson(self, json_str):
            self.status = "Test"
            self.test_type = json_str["test_type"]
            self.name = json_str["name"]
            self.description = json_str["description"]
            self.question = json_str["question"]
            self.answers = json_str["answers"]
            self.time_sent = json_str["time_sent"]
            self.timeout = json_str['timeout']
            self.url_for_response = json_str["url_for_response"]
            self.answer_response = json_str["answer_response"]
            self.time_answered = json_str["time_answered"]

    def getJson(self):
        json_data = {
            "test_type": self.test_type,
            "name": self.name,
            "description": self.description,
            "question": self.question,
            "answers": self.answers,
            "time_sent": self.time_sent,
            "timeout": self.timeout,
            "url_for_response": self.url_for_response,
            "answer_response": self.answer_response,
            "time_answered": self.time_answered
        }
        return json.dumps(json_data)

    def setAnswer(self, answer):
        self.answer_response = answer
        self.time_answered = time.time()

    def getTest(self):
        return self.test_type
    
    def getName(self):
        return self.name

    def getDescription(self):
        return self.description

    def getQuestion(self):
        return self.question

    def getAnswers(self):
        return self.answers

    def getTime(self):
        return self.time_sent
    
    def getTimeout(self):
        return self.timeout

    def getUrl(self):
        return self.url_for_response


data = DataStore()
