import json
import os
import requests

import logging

from agavepy.agave import Agave
from jupyterhub.spawner import Spawner, LocalProcessSpawner
from kubespawner import KubeSpawner


logger = logging.getLogger(__name__)

# TAS configuration:
# base URL for TAS API.
TAS_URL_BASE = os.environ.get('TAS_URL_BASE', 'https://tas.tacc.utexas.edu/api/v1')
TAS_ROLE_ACCT = os.environ.get('TAS_ROLE_ACCT', 'tas-jetstream')
TAS_ROLE_PASS = os.environ.get('TAS_ROLE_PASS')


# class TaccSpawnerError(Exception):
#     def __init__(self, msg):
#         self.message = msg
#
#
# class TaccSpawnerModelError(TaccSpawnerError):
#     pass
#
#
# class TaccSpawner(KubeSpawner):
#     """Spawner class that leverages an Abaco actor to spawn notebook servers across a datacenter.
#
#     The high-level algorithm is as follows:
#     1. Hub process sends a message to an Abaco actor with metadata about the notebook server to launch.
#     2. Actor will manage launching the server, either by talking to a container API (e.g. Docker, Swarm or k8s) or by
#        launching an HPC job via Agave.
#     3. Actor will report the resulting IP and port back to the hub process via shared database or the agave
#        metadata service.
#
#     """
#
#     def __init__(self, *args, **kwargs):
#         super().__init__(*args, **kwargs)
#
#         self.tenant = os.environ.get('TENANT')
#         self.instance = os.environ.get('INSTANCE')
#         logger.info('tenant: {}. instance: {}'.format(self.tenant, self.instance))
#
#
#     # def start(self):
#     #     """Start the single-user server
#     #
#     #     Returns:
#     #       (str, int): the (ip, port) where the Hub can connect to the server.
#     #
#     #     """
#     #     super().start()
#
#     # def options_from_form(self, formdata):
#     #     options = {}
#     #     options['image'] = formdata['image'][0]
#     #     return options

INSTANCE = os.environ.get('INSTANCE')
TENANT = os.environ.get('TENANT')

def hook(spawner):
    ag = get_service_client()
    configs = ag.meta.listMetadata(
        q=str({'name': 'config.{}.{}.jhub'.format(TENANT, INSTANCE)})
    )[0]['value']
    access_token, refresh_token, url = get_agave_access_data(spawner)
    logger.info('access:{}, refresh:{}, url:{}'.format(access_token, refresh_token, url))
    # uid, gid, home_dir = get_tas_data(access_token, refresh_token, url)
    # if uid and not configs.get('uid'):
    #     spawner.uid = uid
    get_mounts(spawner, configs)
    spawner.image = u'taccsciapps/jupyteruser-ds:1.2.5'
    spawner.start_timeout = 60*6
    # spawner.volumes = [
    #     {'name': 'mlm55-designsafe-staging-jhub-agpy',
    #      'configMap': {'name': 'mlm55-designsafe-staging-jhub-agpy',}
    #      },
    #     {'name': 'mlm55-designsafe-staging-jhub-current',
    #      'configMap': {'name': 'mlm55-designsafe-staging-jhub-current',}
    #      },
    # ]
    # spawner.volume_mounts = [
    #     {'mountPath':'/home/jupyter/test',#for testing -- should be mounted to /etc
    #     # {'mountPath':'/etc',
    #      'name':'mlm55-designsafe-staging-jhub-agpy'
    #      },
    #     {'mountPath':'/home/jupyter/.agave',
    #      'name':'mlm55-designsafe-staging-jhub-current'
    #      },
    # ]

def get_service_client():
    """Returns an agave client representing the service account. This client can be used to access
    the authorized endpoints such as the Abaco endpoint."""
    service_token = os.environ.get('AGAVE_SERVICE_TOKEN')
    if not service_token:
        raise Exception("Missing SERVICE_TOKEN configuration.")
    base_url = os.environ.get('AGAVE_BASE_URL', "https://api.tacc.utexas.edu")
    return Agave(api_server=base_url, token=service_token)

def get_agave_access_data(spawner):
    """
    Returns the access token and base URL cached in the agavepy file
    :return:
    """
    #TODO figure out naming convetions that can follow k8 rules
    # k8 names must consist of lower case alphanumeric characters, '-' or '.', and must start and end with an alphanumeric character
    # do all tenant names follow that? usernames?
    token_file = os.path.join(get_user_token_dir(spawner.user.name), '.agpy')
    logger.info("spawner looking for token file: {} for user: {}".format(token_file, spawner.user.name))
    if not os.path.exists(token_file):
        logger.warning("spawner did not find a token file at {}".format(token_file))
        return None, None, None
    try:
        data = json.load(open(token_file))
    except ValueError:
        logger.warning('could not ready json from token file')
        return None, None, None

    try:
        access_token = data[0]['token']
        logger.info("Setting token: {}".format(access_token))
        refresh_token = data[0]['refresh_token']
        logger.info("Setting refresh token: {}".format(refresh_token))
        url = data[0]['api_server']
        logger.info("Setting url: {}".format(url))
        return access_token, refresh_token, url

    except (TypeError, KeyError):
        logger.warning("token file did not have an access token and/or an api_server. data: {}".format(data))
        return None, None, None

# def get_tas_data(access_token, refresh_token, url):
#     """Get the TACC uid, gid and homedir for this user from the TAS API."""
#     if not TAS_ROLE_ACCT:
#         logger.error("No TAS_ROLE_ACCT configured. Aborting.")
#         return
#     if not TAS_ROLE_PASS:
#         logger.error("No TAS_ROLE_PASS configured. Aborting.")
#         return
#     url = '{}/users/username/{}'.format(TAS_URL_BASE, self.user.name)
#     headers = {'Content-type': 'application/json',
#                'Accept': 'application/json'
#                }
#     try:
#         rsp = requests.get(url,
#                            headers=headers,
#                            auth=requests.auth.HTTPBasicAuth(TAS_ROLE_ACCT, TAS_ROLE_PASS))
#     except Exception as e:
#         logger.error("Got an exception from TAS API. "
#                        "Exception: {}. url: {}. TAS_ROLE_ACCT: {}".format(e, url, TAS_ROLE_ACCT))
#         return
#     try:
#         data = rsp.json()
#     except Exception as e:
#         logger.error("Did not get JSON from TAS API. rsp: {}"
#                        "Exception: {}. url: {}. TAS_ROLE_ACCT: {}".format(rsp, e, url, TAS_ROLE_ACCT))
#         return
#     try:
#         tas_uid = data['result']['uid']
#         tas_homedir = data['result']['homeDirectory']
#     except Exception as e:
#         logger.error("Did not get attributes from TAS API. rsp: {}"
#                        "Exception: {}. url: {}. TAS_ROLE_ACCT: {}".format(rsp, e, url, TAS_ROLE_ACCT))
#         return
#
#     # first look for an "extended profile" record in agave metadata. such a record might have the
#     # gid to use for this user.
#     tas_gid = None
#     if access_token and refresh_token and url:
#         ag = get_oauth_client(url, access_token, refresh_token)
#         meta_name = 'profile.{}.{}'.format(TENANT, user.name)
#         q = "{'name': '" + meta_name + "'}"
#         logger.info("using query: {}".format(q))
#         try:
#             rsp = ag.meta.listMetadata(q=q)
#         except Exception as e:
#             logger.error("Got an exception trying to retrieve the extended profile. Exception: {}".format(e))
#         try:
#             self.tas_gid = rsp[0].value['posix_gid']
#         except IndexError:
#             self.tas_gid = None
#         except Exception as e:
#             logger.error(
#                 "Got an exception trying to retrieve the gid from the extended profile. Exception: {}".format(e))
#     # if the instance has a configured TAS_GID to use we will use that; otherwise,
#     # we fall back on using the user's uid as the gid, which is (almost) always safe)
#     if not self.tas_gid:
#         self.tas_gid = os.environ.get('TAS_GID', self.tas_uid)
#     logger.info("Setting the following TAS data: uid:{} gid:{} homedir:{}".format(self.tas_uid,
#                                                                                     self.tas_gid,
#                                                                                     self.tas_homedir))

def get_user_token_dir(username):
        return os.path.join(
            '/agave/jupyter/tokens',
            INSTANCE,
            TENANT,
            username)

def get_mounts(spawner, configs):
    spawner.volumes = [
        {'name': 'mlm55-designsafe-staging-jhub-agpy',
         'configMap': {'name': 'mlm55-designsafe-staging-jhub-agpy', }
         },
        {'name': 'mlm55-designsafe-staging-jhub-current',
         'configMap': {'name': 'mlm55-designsafe-staging-jhub-current', }
         },
    ]
    spawner.volume_mounts = [
         {'mountPath':'/etc/.agpy',
          'name': 'mlm55-designsafe-staging-jhub-agpy',
          'subPath': '.agpy'
         },
        {'mountPath': '/home/jupyter/.agave',
         'name': 'mlm55-designsafe-staging-jhub-current'
         },
    ]
    volume_mounts = configs.get('volume_mounts')

    # if len(volume_mounts):
    #     for vol in volume_mounts:
    #         message['params']['volume_mounts'].append(vol.format(**template_vars))