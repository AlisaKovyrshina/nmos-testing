# JTNM Test Suite - Fully Automated testing of nmos-js

## Installation and usage

1. Install flask and selenium
`pip install -r requirements.txt`

2. Install the webdriver for the browser you wish to use. [See selenium docs for more info.](https://www.selenium.dev/documentation/en/webdriver/driver_requirements/#quick-reference) 

3. Update `self.BCut_url`, `self.mock_registry_url` and `self.driver` in JTNMTests.py init method to the urls of your instance of nmos-js, the mock registry set up when JTNM Test suite is run and the driver you installed in the previous step.

4. Run your NMOS Testing instance and choose JTNM Client Tests

5. Run AutomatedTests.py. Your chosen browser will launch and navigate to the url given in `self.BCut_url` then update the query API to the url given in `self.mock_registry_url` (Note: until test suite is started nmos-js will show unable to retrieve resources error messages if you navigate away from the settings page)

6. On your NMOS Testing instance enter the IP address and Port where AutomatedTests is running

7. Choose tests and click Run

8. Test suite POSTs json with test details to AutomatedTests API endpoint '/x-nmos/client-testing'. AutomatedTests will retrieve the data and run the relevant set of selenium instructions defined in JTNMTests.py to operate the browser launched in step 5.

9. After last test, test suite will POST a clear request to empty the data store

10. Results are displayed on NMOS Testing Tool
