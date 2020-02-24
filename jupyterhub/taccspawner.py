import json
import os
import re

import requests
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
    get_mounts(spawner)
    get_projects(spawner)

    spawner.uid = int(spawner.configs.get('uid', spawner.tas_uid))
    spawner.gid = int(spawner.configs.get('gid', spawner.tas_gid))


    if len(spawner.configs.get('images')) == 1:  # only 1 image option, so we skipped the form
        spawner.image = spawner.configs.get('images')[0]
    elif spawner.user_options['image'][0] == 'HPC':
        spawner.image = spawner.configs.get('hpc_image')
        spawner.mem_guarantee = spawner.configs.get('hpc_mem_guarantee')
        spawner.cpu_guarantee = float(spawner.configs.get('hpc_cpu_guarantee'))
        return
    else:
        spawner.image = spawner.user_options['image'][0]

    spawner.mem_limit = spawner.configs.get('mem_limit')
    spawner.cpu_limit = float(spawner.configs.get('cpu_limit'))
    spawner.start_timeout = 60 * 5

def get_oauth_client(base_url, access_token, refresh_token):
    return Agave(api_server=base_url, token=access_token, refresh_token=refresh_token)


def get_agave_access_data(spawner):
    """
    Returns the access token and base URL cached in the agavepy file
    :return:
    """
    # TODO figure out naming conventions that can follow k8 rules
    # k8 names must consist of lower case alphanumeric characters, '-' or '.',
    # and must start and end with an alphanumeric character
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
        {'mountPath': '/etc/.agpy',
         'name': '{}-{}-{}-jhub-agpy'.format(safe_username, safe_tenant, safe_instance),
         'subPath': '.agpy',
         },
        {'mountPath': '/home/jupyter/.agave/current',
         'name': '{}-{}-{}-jhub-current'.format(safe_username, safe_tenant, safe_instance),
         'subPath': 'current',
         },
    ]
    volume_mounts = spawner.configs.get('volume_mounts')

    template_vars = {
        'username': spawner.user.name,
        'tenant_id': TENANT, #TODO do we need this?
    }

    if hasattr(spawner, 'tas_homedir'):
        template_vars['tas_homeDirectory'] = spawner.tas_homedir

    if len(volume_mounts):
        for item in volume_mounts:
            item = item.format(**template_vars)
            m = item.split(":")
            # volume names must consist of lower case alphanumeric characters or '-',
            # and must start and end with an alphanumeric character (e.g. 'my-name',  or '123-abc',
            # regex used for validation is '[a-z0-9]([-a-z0-9]*[a-z0-9])?')
            vol_name = re.sub(r'([^a-z0-9-\s]+?)', '', m[2].split('/')[-1].lower())
            read_only = True if m[3] == 'ro' else False

            spawner.volumes.append({
                'name': vol_name,
                'nfs': {
                    'server': m[0],
                    'path': m[1],
                    'readOnly': read_only
                }
            })

            spawner.volume_mounts.append({
                'mountPath': m[2],
                'name': vol_name
            })
        spawner.log.info(spawner.volumes)
        spawner.log.info(spawner.volume_mounts)


def get_projects(spawner):
    spawner.host_projects_root_dir = spawner.configs.get('host_projects_root_dir')
    spawner.container_projects_root_dir = spawner.configs.get('container_projects_root_dir')
    spawner.network_storage = spawner.configs.get('network_storage')
    if not spawner.host_projects_root_dir or not spawner.container_projects_root_dir:
        spawner.log.info("No host_projects_root_dir or container_projects_root_dir. configs:{}".format(spawner.configs))
        return None
    if not spawner.access_token or not spawner.url:
        spawner.log.info("no access_token or url")
        return None
    url = '{}/projects/v2/'.format(spawner.url)

    try:
        ag = get_oauth_client(spawner.url, spawner.access_token, spawner.refresh_token)
        rsp = ag.geturl(url)
    except Exception as e:
        spawner.log.warn("Got exception calling /projects: {}".format(e))
        return None
    try:
        data = rsp.json()
    except ValueError as e:
        spawner.log.warn("Did not get JSON from /projects. Exception: {}".format(e))
        spawner.log.warn("Full response from service: {}".format(rsp))
        spawner.log.warn("url used: {}".format(url))
        return None
    projects = data.get('projects')
    spawner.log.info("service returned projects: {}".format(projects))
    try:
        spawner.log.info("Found {} projects".format(len(projects)))
    except TypeError:
        spawner.log.error("Projects data has no length.")
        spawner.log.info("response: {}, data: {}".format(rsp, data))
        return None
    for p in projects:
        uuid = p.get('uuid')
        if not uuid:
            spawner.log.warn("Did not get a uuid for a project: {}".format(p))
            continue
        project_id = p.get('value').get('projectId')
        if not project_id:
            spawner.log.warn("Did not get a projectId for a project: {}".format(p))
            continue

        spawner.volumes.append({
            'name': 'project-{}'.format(safe_string(uuid).lower()),
            'nfs': {
                'server': spawner.network_storage,
                'path': '{}/{}'.format(spawner.host_projects_root_dir, uuid),
                'readOnly': False
            }
        })

        spawner.volume_mounts.append({
            'mountPath': '{}/{}'.format(spawner.container_projects_root_dir, project_id),
            'name': 'project-{}'.format(safe_string(uuid).lower()),
        })
    spawner.log.info(spawner.volumes)
    spawner.log.info(spawner.volume_mounts)
