import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


class JTNMAutoTest:
    """
    Automated version of JTNM Test suite without client facade
    """
    def __init__(self):
        self.BCuT_url = "http://43.195.121.122/admin/#/" # url of nmos-js instance
        self.mock_registry_url = "http://127.0.0.1:5102/" # url of mock registry from test suite
        self.driver = webdriver.Firefox() # selenium driver for browser
        # Launch browser, navigate to nmos-js and update query api url to mock registry
        self.driver.get(self.BCuT_url + "Settings")
        query_api = self.driver.find_element_by_xpath("//main/div[2]/div/div/div/ul/li[1]/div/div/input")
        query_api.clear()
        query_api.send_keys(self.mock_registry_url + "x-nmos/query/v1.3")
        time.sleep(2)

    def _format_device_metadata(self, label, description, id):
        """ Used to format answers based on device metadata """
        return label + ' (' + description + ', ' + id + ')'

    def test_01(self):
        """
        Ensure BCuT uses DNS-SD to find registry
        """
        return "NMOS-js does not use DNS-SD to find registry"

    def test_02(self):
        """
        Ensure BCuT can access the IS-04 Query API
        """
        # Use the BCuT to browse the Senders and Receivers on the discovered Registry via the selected IS-04 Query API.
        # Once you have finished browsing click 'Next'. Successful browsing of the Registry will be automatically logged by the test framework.
        
        # Browse senders and receivers
        time.sleep(5)
        self.driver.find_element_by_link_text('Senders').click()
        time.sleep(5)
        self.driver.find_element_by_link_text('Receivers').click()
        time.sleep(5)

        return "Next"

    def test_03(self):
        """
        Query API should be able to discover all the senders that are registered in the Registry
        """
        # The Query API should be able to discover all the Senders that are registered in the Registry.
        # Refresh the BCuT's view of the Registry and carefully select the Senders that are available from the following list.
        time.sleep(5)
        self.driver.find_element_by_link_text('Senders').click()
        time.sleep(5)
        # Find all senders
        senders = self.driver.find_elements_by_partial_link_text("Test-node-1/sender")
        sender_labels = [sender.text for sender in senders]
        actual_answers = []

        # loop through senders and gather ids and descriptions
        for sender in sender_labels:
            self.driver.find_element_by_link_text(sender).click()
            time.sleep(2)
            sender_id = self.driver.find_element_by_xpath("//div[@class='ra-field ra-field-id']/div/div/span").text
            sender_description = self.driver.find_element_by_xpath("//div[@class='ra-field ra-field-description']/div/div/span").text
            actual_answers.append(self._format_device_metadata(sender, sender_description, sender_id))
            self.driver.find_element_by_link_text('Senders').click()

        return actual_answers


    def test_04(self):
        """
        Query API should be able to discover all the receivers that are registered in the Registry
        """
        # The Query API should be able to discover all the Receivers that are registered in the Registry.
        # Refresh the BCuT's view of the Registry and carefully select the Receivers that are available from the following list.
        time.sleep(5)
        self.driver.find_element_by_link_text('Receivers').click()
        time.sleep(5)
        # Find all receivers
        receivers = self.driver.find_elements_by_partial_link_text("Test-node-2/receiver")
        receiver_labels = [receiver.text for receiver in receivers]
        actual_answers = []

        # loop through receivers and gather ids and descriptions
        for receiver in receiver_labels:
            self.driver.find_element_by_link_text(receiver).click()
            time.sleep(2)
            receiver_id = self.driver.find_element_by_xpath("//div[@class='ra-field ra-field-id']/div/div/span").text
            receiver_description = self.driver.find_element_by_xpath("//div[@class='ra-field ra-field-description']/div/div/span").text
            actual_answers.append(self._format_device_metadata(receiver, receiver_description, receiver_id))
            self.driver.find_element_by_link_text('Receivers').click()

        return actual_answers

    def test_05(self):
        """
        Reference Sender is put offline; Reference Sender is put back online
        """
        return "Test not yet implemented"

    def test_06(self):
        """
        Identify which Receiver devices are controllable via IS-05
        """
        # Some of the discovered Receivers are controllable via IS-05, for instance, allowing Senders to be connected.
        # Carefully select the Receivers that have connection APIs from the following list.
        time.sleep(5)
        self.driver.find_element_by_link_text('Receivers').click()
        time.sleep(5)
        # Find all receivers
        receivers = self.driver.find_elements_by_partial_link_text("Test-node-2/receiver")
        receiver_labels = [receiver.text for receiver in receivers]
        actual_answers = []

        # loop through receivers and check if connection tab is disabled
        for receiver in receiver_labels:
            self.driver.find_element_by_link_text(receiver).click()
            time.sleep(2)
            receiver_id = self.driver.find_element_by_xpath("//div[@class='ra-field ra-field-id']/div/div/span").text
            receiver_description = self.driver.find_element_by_xpath("//div[@class='ra-field ra-field-description']/div/div/span").text
            
            connect_button = WebDriverWait(self.driver, 10).until(EC.visibility_of_element_located((By.XPATH, "//main/div[2]/div[1]/div[1]/div/div/div/a[4]"))) 
            
            if connect_button.get_attribute("aria-disabled") == 'false':
                actual_answers.append(self._format_device_metadata(receiver, receiver_description, receiver_id))
            self.driver.find_element_by_link_text('Receivers').click()

        return actual_answers

    def test_07(self):
        """
        Instruct Receiver to subscribe to a Sender’s Flow via IS-05
        """
        return "Test not yet implemented"

    def test_08(self):
        """
        Disconnecting a Receiver from a connected Flow via IS-05
        """
        return "Test not yet implemented"

    def test_09(self):
        """
        Indicating the state of connections via updates received from the IS-04 Query API
        """
        return "Test not yet implemented"
  
tests = JTNMAutoTest()