#replace these
url = 'https://designsafe.jupyterhub.staging.tacc.cloud/'
users = [
{'username':'test1', 'password': 'testpw'},
{'username':'test2', 'password': 'testpw'},
{'username':'test3', 'password': 'testpw'},
]

import os

from kubernetes import client
from pprint import pprint
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC

chrome_options = Options()
chrome_options.add_argument('--headless')
chrome_options.add_argument('--no-sandbox')
chrome_options.add_argument('--disable-dev-shm-usage')

def start_notebook(driver, user):
    login(driver, user)
    return submit_form(driver)


def login(driver, user):
    driver.find_element_by_xpath("/html/body/div[1]/div/a").click()
    # agave oauth page
    username = driver.find_element_by_id("username")
    username.clear()
    username.send_keys(user['username'])
    pw = driver.find_element_by_id("password")
    pw.clear()
    pw.send_keys(user['password'])
    pw.send_keys(Keys.RETURN)
    # agave approve always page
    try:
        driver.find_element_by_id("approveAlways").click()
    except NoSuchElementException as e:
        pass

def submit_form(driver):
    try:
        select = Select(driver.find_element_by_name('image'))
        select.select_by_index('0')
        driver.find_element_by_xpath("/html/body/div[1]/div[2]/form/input").click()
        WebDriverWait(driver, 100).until(
            EC.presence_of_element_located((By.ID, 'progress-message')))
    except NoSuchElementException as e:
        try:
            driver.find_element_by_id('refresh_notebook_list')
            print('{} notebook already running'.format(user['username']))
            return 'RUNNING'
        except NoSuchElementException as e:
            print('there is no options form'.format(user['username']))
            pass
        pass
    # except TimeoutException as e:
    #     driver.find_element_by_id('refresh_notebook_list')
    #     print('What happened here {} '.format(driver.page_source))
    #     pass

def get_more_info(driver, user):
    try:
        # WebDriverWait(driver, 400).until(
        #     EC.text_to_be_present_in_element((By.ID, ''), 'Spawn failed'))
        WebDriverWait(driver, 280).until(
            EC.presence_of_element_located((By.ID, 'refresh_notebook_list')))
        print('{} notebook spawned successfully'.format(user['username']))
    except TimeoutException as e:  # notebook didn't spin up
        with open('/run/secrets/kubernetes.io/serviceaccount/token') as f:
            token = f.read()
        with open('/run/secrets/kubernetes.io/serviceaccount/namespace') as f:
            namespace = f.read()

        configuration = client.Configuration()
        configuration.api_key['authorization'] = 'Bearer {}'.format(token)
        configuration.host = 'https://kubernetes.default'
        configuration.ssl_ca_cert = '/run/secrets/kubernetes.io/serviceaccount/ca.crt'

        api_instance = client.CoreV1Api(client.ApiClient(configuration))

        try:
            api_response = api_instance.read_namespaced_pod('jupyter-{}'.format(user['username']), namespace)
            pprint('😡 read_namespaced_pod {}'.format(str(api_response)))
        except Exception as e:
            pprint("Exception when calling CoreV1Api->read_namespaced_pod: %s\n" % e)
            pass

        try:
            api_response = api_instance.read_namespaced_pod_log('jupyter-{}'.format(user['username']), namespace)
            pprint('😡 {} read_namespaced_pod_log'.format(str(api_response)))
        except Exception as e:
            pprint("Exception when calling CoreV1Api->read_namespaced_pod_log: %s\n" % e)
            pass


for user in users:
    driver = webdriver.Chrome('{}/chromedriver'.format(os.path.dirname(os.path.realpath(__file__))), options=chrome_options)
    driver.get(url)
    nb_status = start_notebook(driver, user)
    if nb_status != 'RUNNING':
        get_more_info(driver, user)
    driver.find_element_by_id("logout").click()
    driver.quit()