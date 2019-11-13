"""
Custom Authenticator to use Agave OAuth with JupyterHub
"""

import grp
import json
import os
import pwd
import sys
import urllib
import time

from tornado.auth import OAuth2Mixin
from tornado import gen, web

from tornado.httputil import url_concat
from tornado.httpclient import HTTPRequest, AsyncHTTPClient

from jupyterhub.auth import LocalAuthenticator

from traitlets import Set

from .oauth2 import OAuthLoginHandler, OAuthenticator

from agavepy.agave import Agave


INSTANCE = os.environ.get('INSTANCE')
TENANT = os.environ.get('TENANT')


def get_user_token_dir(username):
    return os.path.join(
        '{}/jupyter/tokens'.format(os.environ.get('LOCAL_NETWORK_STORAGE_ROOT_DIR', '/corral-repl/projects/agave')),
        INSTANCE,
        TENANT,
        username)


def get_config_metadata_name():
    return 'config.{}.{}.jhub'.format(TENANT, INSTANCE)


service_token = os.environ.get('AGAVE_SERVICE_TOKEN')

if not service_token:
    raise Exception("Missing SERVICE_TOKEN configuration.")

base_url = os.environ.get('AGAVE_BASE_URL', "https://api.tacc.utexas.edu")
ag = Agave(api_server=base_url, token=service_token)
q={'name': get_config_metadata_name()}
configs = ag.meta.listMetadata(q=str(q))[0]['value']

TOKENS_DIR = '{}/jupyter/tokens'.format(os.environ.get('LOCAL_NETWORK_STORAGE_ROOT_DIR', '/corral-repl/projects/agave'))

class AgaveMixin(OAuth2Mixin):
    _OAUTH_AUTHORIZE_URL = "{}/oauth2/authorize".format(configs.get('agave_base_url').rstrip('/'))
    _OAUTH_ACCESS_TOKEN_URL = "{}/token".format(configs.get('agave_base_url').rstrip('/'))


class AgaveLoginHandler(OAuthLoginHandler, AgaveMixin):
    pass


class AgaveOAuthenticator(OAuthenticator):
    login_service = configs.get('agave_login_button_text')
    client_id_env = 'AGAVE_CLIENT_ID'
    client_secret_env = 'AGAVE_CLIENT_SECRET'
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
        return username

    def ensure_token_dir(self, username):
        try:
            os.makedirs(get_user_token_dir(username))
        except OSError as e:
            self.log.info("Got error trying to make token dir: "
                          "{} exception: {}".format(get_user_token_dir(username), e))

    def get_uid_gid(self):
        """look up uid and gid of apim home dir. If this path doesn't exist, stat_info will contain the root user and group
        (that is, uid = 0 = gid)."""
        # first, see if they are defined in the environment
        uid = configs.get('uid')
        gid = configs.get('gid')
        if uid and gid:
            self.log.info('Tenant set custom UID and GID for jupyteruser. UID: {} GID: {}'.format(uid, gid))
            try:
                uid = int(uid)
                gid = int(gid)
                self.log.info('Cast to int and returning UID: {}, GID:{}'.format(uid, gid))
                return uid, gid
            except Exception as e:
                self.log.info("Got exception {} casting to int".format(e))
        # otherwise, try to derive them from existing file ownerships
        stat_info = os.stat('/home/apim/jupyterhub_config.py')
        uid = stat_info.st_uid
        gid = stat_info.st_gid
        self.log.info("Used the /home/apim/jupyterhub_config.py file to determine UID: {}, GID:{}".format(uid, gid))
        return uid, gid

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
        with open(os.path.join(get_user_token_dir(username), '.agpy'), 'w') as f:
            json.dump(d, f)
        self.log.info("Saved agavepy cache file to {}".format(os.path.join(get_user_token_dir(username), '.agpy')))
        self.log.info("agavepy cache file data: {}".format(d))
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
        with open(os.path.join(get_user_token_dir(username), 'current'), 'w') as f:
            json.dump(d, f)
        self.log.info("Saved CLI cache file to {}".format(os.path.join(get_user_token_dir(username), 'current')))
        self.log.info("CLI cache file data: {}".format(d))
        # try to set the ownership of the cache files to the apim user and an appropriate group. We need to ignore
        # permission errors for portability.
        try:
            uid, gid = self.get_uid_gid()
            os.chown(os.path.join(get_user_token_dir(username), '.agpy'), uid, gid)
            os.chown(os.path.join(get_user_token_dir(username), 'current'), uid, gid)
        # if we get a permission error, ignore it because we may be in a different enviornment without an apim user and
        # thus trying to set ownership to root.
        except OSError as e:
            self.log.info("OSError setting permissions on cache files: {}".format(e))
        except PermissionError as e:
            self.log.info("PermissionError setting permissions on cache files: {}".format(e))
        try:
            os.chmod(os.path.join(get_user_token_dir(username), 'current'), 0o777)
            os.chmod(os.path.join(get_user_token_dir(username), '.agpy'), 0o777)
            self.log.info("Changed permissions on token cache files to 0777.")
        except OSError as e:
            self.log.info("OSError setting permissions on cache files: {}".format(e))
        except PermissionError as e:
            self.log.info("PermissionError setting permissions on cache files: {}".format(e))

class LocalAgaveOAuthenticator(LocalAuthenticator,
                                   AgaveOAuthenticator):
    """A version that mixes in local system user creation"""
    pass
