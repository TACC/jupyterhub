import json
import os
import requests
import string

import logging

from agavepy.agave import Agave
from jupyterhub.common import TENANT, INSTANCE, CONFIGS, safe_string


# TAS configuration:
# base URL for TAS API.
TAS_URL_BASE = os.environ.get('TAS_URL_BASE', 'https://tas.tacc.utexas.edu/api/v1')
TAS_ROLE_ACCT = os.environ.get('TAS_ROLE_ACCT', 'tas-jetstream')
TAS_ROLE_PASS = os.environ.get('TAS_ROLE_PASS')


def hook(spawner):
    spawner.configs = CONFIGS

    get_agave_access_data(spawner)
    spawner.log.info('access:{}, refresh:{}, url:{}'.format(spawner.access_token, spawner.refresh_token, spawner.url))

    get_tas_data(spawner)


    # if uid and not configs.get('uid'):
    #     spawner.uid = uid
    get_mounts(spawner)

    if len(spawner.configs.get('images')) == 1:  # only 1 image option
        spawner.image = spawner.configs.get('images')[0]
    else:
        spawner.image = spawner.user_options['image'][0]

    spawner.start_timeout = 60*6

def get_oauth_client(base_url, access_token, refresh_token):
    return Agave(api_server=base_url, token=access_token, refresh_token=refresh_token)

def get_agave_access_data(spawner):
    """
    Returns the access token and base URL cached in the agavepy file
    :return:
    """
    #TODO figure out naming convetions that can follow k8 rules
    # k8 names must consist of lower case alphanumeric characters, '-' or '.', and must start and end with an alphanumeric character
    # do all tenant names follow that? usernames?
    token_file = os.path.join(get_user_token_dir(spawner.user.name), '.agpy')
    spawner.log.info("spawner looking for token file: {} for user: {}".format(token_file, spawner.user.name))
    if not os.path.exists(token_file):
        spawner.log.warning("spawner did not find a token file at {}".format(token_file))
        return None
    try:
        data = json.load(open(token_file))
    except ValueError:
        spawner.log.warning('could not ready json from token file')
        return None

    try:
        spawner.access_token = data[0]['token']
        spawner.log.info("Setting token: {}".format(spawner.access_token))
        spawner.refresh_token = data[0]['refresh_token']
        spawner.log.info("Setting refresh token: {}".format(spawner.refresh_token))
        spawner.url = data[0]['api_server']
        spawner.log.info("Setting url: {}".format(spawner.url))

    except (TypeError, KeyError):
        spawner.log.warning("token file did not have an access token and/or an api_server. data: {}".format(data))
        return None

def get_tas_data(spawner):
    """Get the TACC uid, gid and homedir for this user from the TAS API."""
    if not TAS_ROLE_ACCT:
        spawner.log.error("No TAS_ROLE_ACCT configured. Aborting.")
        return
    if not TAS_ROLE_PASS:
        spawner.log.error("No TAS_ROLE_PASS configured. Aborting.")
        return
    url = '{}/users/username/{}'.format(TAS_URL_BASE, spawner.user.name)
    headers = {'Content-type': 'application/json',
               'Accept': 'application/json'
               }
    try:
        rsp = requests.get(url,
                           headers=headers,
                           auth=requests.auth.HTTPBasicAuth(TAS_ROLE_ACCT, TAS_ROLE_PASS))
    except Exception as e:
        spawner.log.error("Got an exception from TAS API. "
                       "Exception: {}. url: {}. TAS_ROLE_ACCT: {}".format(e, url, TAS_ROLE_ACCT))
        return
    try:
        data = rsp.json()
    except Exception as e:
        spawner.log.error("Did not get JSON from TAS API. rsp: {}"
                       "Exception: {}. url: {}. TAS_ROLE_ACCT: {}".format(rsp, e, url, TAS_ROLE_ACCT))
        return
    try:
        spawner.tas_uid = data['result']['uid']
        spawner.tas_homedir = data['result']['homeDirectory']
    except Exception as e:
        spawner.log.error("Did not get attributes from TAS API. rsp: {}"
                       "Exception: {}. url: {}. TAS_ROLE_ACCT: {}".format(rsp, e, url, TAS_ROLE_ACCT))
        return

    # first look for an "extended profile" record in agave metadata. such a record might have the
    # gid to use for this user.
    spawner.tas_gid = None
    if spawner.access_token and spawner.refresh_token and spawner.url:
        ag = get_oauth_client(spawner.url, spawner.access_token, spawner.refresh_token)
        meta_name = 'profile.{}.{}'.format(TENANT, spawner.user.name)
        q = "{'name': '" + meta_name + "'}"
        spawner.log.info("using query: {}".format(q))
        try:
            rsp = ag.meta.listMetadata(q=q)
        except Exception as e:
            spawner.log.error("Got an exception trying to retrieve the extended profile. Exception: {}".format(e))
        try:
            spawner.tas_gid = rsp[0].value['posix_gid']
        except IndexError:
            spawner.tas_gid = None
        except Exception as e:
            spawner.log.error(
                "Got an exception trying to retrieve the gid from the extended profile. Exception: {}".format(e))
    # if the instance has a configured TAS_GID to use we will use that; otherwise,
    # we fall back on using the user's uid as the gid, which is (almost) always safe)
    if not spawner.tas_gid:
        spawner.tas_gid = spawner.configs.get('gid', spawner.tas_uid)
    spawner.log.info("Setting the following TAS data: uid:{} gid:{} homedir:{}".format(spawner.tas_uid,
                                                                                    spawner.tas_gid,
                                                                                    spawner.tas_homedir))

def get_user_token_dir(username):
        return os.path.join(
            '/agave/jupyter/tokens',
            INSTANCE,
            TENANT,
            username)

def get_mounts(spawner):
    safe_username = safe_string(spawner.user.name).lower()
    safe_tenant = safe_string(TENANT).lower()
    safe_instance = safe_string(INSTANCE).lower()
    spawner.volumes = [
        {'name': '{}-{}-{}-jhub-agpy'.format(safe_username, safe_tenant, safe_instance),
         'configMap': {'name': '{}-{}-{}-jhub-agpy'.format(safe_username, safe_tenant, safe_instance), }
         },
        {'name': '{}-{}-{}-jhub-current'.format(safe_username, safe_tenant, safe_instance),
         'configMap': {'name': '{}-{}-{}-jhub-current'.format(safe_username, safe_tenant, safe_instance), }
         },
    ]
    spawner.volume_mounts = [
         {'mountPath':'/etc/.agpy',
          'name': '{}-{}-{}-jhub-agpy'.format(safe_username, safe_tenant, safe_instance),
          'subPath': '.agpy'
         },
        {'mountPath': '/home/jupyter/.agave',
         'name': '{}-{}-{}-jhub-current'.format(safe_username, safe_tenant, safe_instance)
         },
    ]
    # volume_mounts = spawner.configs.get('volume_mounts')

    # if len(volume_mounts):
    #     for vol in volume_mounts:
    #         message['params']['volume_mounts'].append(vol.format(**template_vars))

