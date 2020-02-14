"""
Custom Authenticator to use Agave OAuth with JupyterHub
"""

import grp
import json
import os
import pwd
import sys
import urllib
import re
import time

from tornado.auth import OAuth2Mixin
from tornado import gen, web

from tornado.httputil import url_concat
from tornado.httpclient import HTTPRequest, AsyncHTTPClient

from jupyterhub.auth import LocalAuthenticator
from jupyterhub.taccspawner import safe_string

from traitlets import Set

from .oauth2 import OAuthLoginHandler, OAuthenticator

from agavepy.agave import Agave
from kubernetes import client


INSTANCE = os.environ.get('INSTANCE')
TENANT = os.environ.get('TENANT')


def get_config_metadata_name():
    return 'config.{}.{}.jhub'.format(TENANT, INSTANCE)


service_token = os.environ.get('AGAVE_SERVICE_TOKEN')

if not service_token:
    raise Exception("Missing SERVICE_TOKEN configuration.")

base_url = os.environ.get('AGAVE_BASE_URL', "https://api.tacc.utexas.edu")
ag = Agave(api_server=base_url, token=service_token)
q={'name': get_config_metadata_name()}
configs = ag.meta.listMetadata(q=str(q))[0]['value']

class AgaveMixin(OAuth2Mixin):
    _OAUTH_AUTHORIZE_URL = "{}/oauth2/authorize".format(configs.get('agave_base_url').rstrip('/'))
    _OAUTH_ACCESS_TOKEN_URL = "{}/token".format(configs.get('agave_base_url').rstrip('/'))


class AgaveLoginHandler(OAuthLoginHandler, AgaveMixin):
    pass


class AgaveOAuthenticator(OAuthenticator):
    login_service = configs.get('agave_login_button_text')
    # client_id_env = 'AGAVE_CLIENT_ID'
    # client_secret_env = 'AGAVE_CLIENT_SECRET'
    login_handler = AgaveLoginHandler

    team_whitelist = Set(
        config=True,
        help="Automatically whitelist members of selected teams",
    )

    @gen.coroutine
    def authenticate(self, handler, data):
        self.log.info('data', data)
        code = handler.get_argument("code", False)
        if not code:
            raise web.HTTPError(400, "oauth callback made without a token")
        # TODO: Configure the curl_httpclient for tornado
        http_client = AsyncHTTPClient()

        params = dict(
            grant_type="authorization_code",
            code=code,
            redirect_uri=configs.get('oauth_callback_url'),
            client_id=configs.get('agave_client_id'),
            client_secret=configs.get('agave_client_secret')
        )

        url = url_concat(
            "{}/oauth2/token".format(configs.get('agave_base_url').rstrip('/')), params)
        self.log.info(url)
        self.log.info(params)
        bb_header = {"Content-Type":
                     "application/x-www-form-urlencoded;charset=utf-8"}
        req = HTTPRequest(url,
                          method="POST",
                          validate_cert=eval(configs.get('oauth_validate_cert')),
                          body=urllib.parse.urlencode(params).encode('utf-8'),
                          headers=bb_header
                          )
        resp = yield http_client.fetch(req)
        resp_json = json.loads(resp.body.decode('utf8', 'replace'))

        access_token = resp_json['access_token']
        refresh_token = resp_json['refresh_token']
        expires_in = resp_json['expires_in']
        try:
            expires_in = int(expires_in)
        except ValueError:
            expires_in = 3600
        created_at = time.time()
        expires_at = time.ctime(created_at + expires_in)
        self.log.info(str(resp_json))
        # Determine who the logged in user is
        headers = {"Accept": "application/json",
                   "User-Agent": "JupyterHub",
                   "Authorization": "Bearer {}".format(access_token)
                   }
        req = HTTPRequest("{}/profiles/v2/me".format(configs.get('agave_base_url').rstrip('/')),
                          validate_cert=eval(configs.get('oauth_validate_cert')),
                          method="GET",
                          headers=headers
                          )
        resp = yield http_client.fetch(req)
        resp_json = json.loads(resp.body.decode('utf8', 'replace'))
        self.log.info('resp_json after /profiles/v2/me:',str(resp_json))
        username = resp_json["result"]["username"]

        self.ensure_token_dir(username)
        self.save_token(access_token, refresh_token, username, created_at, expires_in, expires_at)
        # self.create_configmaps(access_token, refresh_token, username, created_at, expires_in, expires_at)
        return username

    def ensure_token_dir(self, username):
        try:
            os.makedirs(self.get_user_token_dir(username))
        except OSError as e:
            self.log.info("Got error trying to make token dir: "
                          "{} exception: {}".format(self.get_user_token_dir(username), e))

    def get_user_token_dir(self, username):
        return os.path.join(
            '/agave/jupyter/tokens',
            INSTANCE,
            TENANT,
            username)

    def save_token(self, access_token, refresh_token, username, created_at, expires_in, expires_at):
        tenant_id = configs.get('agave_tenant_id')
        # agavepy file
        d = [{'token': access_token,
              'refresh_token': refresh_token,
              'tenant_id': tenant_id,
              'api_key': configs.get('agave_client_id'),
              'api_secret': configs.get('agave_client_secret'),
              'api_server': '{}'.format(configs.get('agave_base_url').rstrip('/')),
              'verify': eval(configs.get('oauth_validate_cert')),
              }]
        with open(os.path.join(self.get_user_token_dir(username), '.agpy'), 'w') as f:
            json.dump(d, f)
        self.log.info("Saved agavepy cache file to {}".format(os.path.join(self.get_user_token_dir(username), '.agpy')))
        self.log.info("agavepy cache file data: {}".format(d))
        self.create_configmap(username, '.agpy', d)

        # cli file
        d = {'tenantid': tenant_id,
             'baseurl': '{}'.format(configs.get('agave_base_url').rstrip('/')),
             'devurl': '',
             'apikey': configs.get('agave_client_id'),
             'username': username,
             'access_token': access_token,
             'refresh_token': refresh_token,
             'created_at': str(int(created_at)),
             'apisecret': configs.get('agave_client_secret'),
             'expires_in': str(expires_in),
             'expires_at': str(expires_at)
             }
        with open(os.path.join(self.get_user_token_dir(username), 'current'), 'w') as f:
            json.dump(d, f)
        self.log.info("Saved CLI cache file to {}".format(os.path.join(self.get_user_token_dir(username), 'current')))
        self.log.info("CLI cache file data: {}".format(d))
        self.create_configmap(username, 'current', d)

    def create_configmap(self, username, name, d):
        with open('/run/secrets/kubernetes.io/serviceaccount/token') as f:
            token = f.read()
        with open('/run/secrets/kubernetes.io/serviceaccount/namespace') as f:
            namespace = f.read()

        configuration = client.Configuration()
        configuration.api_key['authorization'] = 'Bearer {}'.format(token)
        configuration.host = 'https://kubernetes.default'
        configuration.ssl_ca_cert = '/run/secrets/kubernetes.io/serviceaccount/ca.crt'

        api_instance = client.CoreV1Api(client.ApiClient(configuration))
        # k8 names must consist of lower case alphanumeric characters, '-' or '.', and must start and end with an alphanumeric character
        # safe_username = escapism.escape(self.user.name, safe=safe_chars, escape_char='-').lower()
        # safe_tenant = '-{}'.format(escapism.escape(TENANT, safe=safe_chars, escape_char='-').lower())
        # safe_instance = '-{}'.format(escapism.escape(INSTANCE, safe=safe_chars, escape_char='-').lower())
        safe_username = safe_string(username).lower()
        safe_tenant = safe_string(TENANT).lower()
        safe_instance = safe_string(INSTANCE).lower()
        configmap_name_prefix = '{}-{}-{}-jhub'.format(safe_username, safe_tenant, safe_instance)
        body = client.V1ConfigMap(
            data={name: str(d)},
            metadata={
                'name': '{}-{}'.format(configmap_name_prefix, re.sub('[^A-Za-z0-9]+', '', name)), #remove the . from .agpy to accomodate k8 naming rules
                'labels': {'app': configmap_name_prefix, 'tenant': TENANT, 'instance': INSTANCE, 'username': username}
            }
        )

        self.log.info('{}:{}'.format('configmap body',body))
        try:
            api_response = api_instance.create_namespaced_config_map(namespace, body)
            self.log.info('{} configmap created'.format(name))
            print(str(api_response))
        except Exception as e:
        # except client.rest.ApiException as e:
            print("Exception when calling CoreV1Api->create_namespaced_config_map: %s\n" % e)

    # def create_configmaps(self, access_token, refresh_token, username, created_at, expires_in, expires_at):
    #     tenant_id = configs.get('agave_tenant_id')
    #
    #     with open('/run/secrets/kubernetes.io/serviceaccount/token') as f:
    #         token = f.read()
    #     with open('/run/secrets/kubernetes.io/serviceaccount/namespace') as f:
    #         namespace = f.read()
    #
    #     configuration = client.Configuration()
    #     configuration.api_key['authorization'] = 'Bearer {}'.format(token)
    #     # configuration.api_key['authorization'] = token
    #     # configuration.api_key_prefix['authorization'] = 'Bearer'
    #     configuration.host = 'https://kubernetes.default'
    #     configuration.ssl_ca_cert = '/run/secrets/kubernetes.io/serviceaccount/ca.crt'
    #
    #     api_instance = client.CoreV1Api(client.ApiClient(configuration))
    #     configmap_name_prefix = '{}-{}-{}-jhub'.format(username, TENANT, INSTANCE)
    #
    #
    #     # agavepy file
    #     d = [{'token': access_token,
    #          'refresh_token': refresh_token,
    #          'tenant_id': tenant_id,
    #          'api_key': configs.get('agave_client_id'),
    #          'api_secret': configs.get('agave_client_secret'),
    #          'api_server': '{}'.format(configs.get('agave_base_url').rstrip('/')),
    #          'verify': eval(configs.get('oauth_validate_cert')),
    #          }]
    #     self.log.info("agavepy cache file data: {}".format(d))
    #     # create configmap for agpy
    #     agpy_body = client.V1ConfigMap(
    #         data={'.agpy': str(d)},
    #         metadata={
    #             'name': "{}-agpy".format(configmap_name_prefix),
    #             'labels': {'app': configmap_name_prefix, 'tenant': TENANT, 'instance': INSTANCE, 'username': username}
    #         }
    #     )
    #     self.log.info('{}:{}'.format('agpybody' * 121, agpy_body))
    #     try:
    #         api_response = api_instance.create_namespaced_config_map(namespace, agpy_body)
    #         self.log.info('agpy configmap created')
    #         print(api_response)
    #     except client.rest.ApiException as e:
    #         print("Exception when calling CoreV1Api->create_namespaced_config_map: %s\n" % e)
    #
    #     # cli file
    #     d = {'tenantid': tenant_id,
    #          'baseurl': '{}'.format(configs.get('agave_base_url').rstrip('/')),
    #          'devurl': '',
    #          'apikey': configs.get('agave_client_id'),
    #          'username': username,
    #          'access_token': access_token,
    #          'refresh_token': refresh_token,
    #          'created_at': str(int(created_at)),
    #          'apisecret': configs.get('agave_client_secret'),
    #          'expires_in': str(expires_in),
    #          'expires_at': str(expires_at)
    #          }
    #     self.log.info("CLI cache file data: {}".format(d))
    #     # create configmap for current
    #     current_body = client.V1ConfigMap(
    #         data={'current': str(d)},
    #         metadata={
    #             'name': "{}-current".format(configmap_name_prefix),
    #             'labels': {'app': configmap_name_prefix, 'tenant': TENANT, 'instance': INSTANCE, 'username': username}
    #         }
    #     )
    #
    #     try:
    #         api_response = api_instance.create_namespaced_config_map(namespace, current_body)
    #         self.log.info('current configmap created')
    #         print(api_response)
    #     except client.rest.ApiException as e:
    #         print("Exception when calling CoreV1Api->create_namespaced_config_map: %s\n" % e)

class LocalAgaveOAuthenticator(LocalAuthenticator,
                                   AgaveOAuthenticator):
    """A version that mixes in local system user creation"""
    pass
