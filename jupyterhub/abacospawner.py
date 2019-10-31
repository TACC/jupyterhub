import json
import os
import requests

import logging

from agavepy.agave import Agave
from jupyterhub.spawner import Spawner, LocalProcessSpawner


logger = logging.getLogger(__name__)

# TAS configuration:
# base URL for TAS API.
TAS_URL_BASE = os.environ.get('TAS_URL_BASE', 'https://tas.tacc.utexas.edu/api/v1')
TAS_ROLE_ACCT = os.environ.get('TAS_ROLE_ACCT', 'tas-jetstream')
TAS_ROLE_PASS = os.environ.get('TAS_ROLE_PASS')

NETWORK_STORAGE_ROOT_DIR = os.environ.get('NETWORK_STORAGE_ROOT_DIR', '/corral-repl/projects/agave')  #TODO should this in the config metadata
TOKENS_DIR = '{}/jupyter/tokens'.format(NETWORK_STORAGE_ROOT_DIR)
INSTANCE = os.environ.get('INSTANCE')
TENANT = os.environ.get('TENANT')

def get_user_token_dir(username):
    return os.path.join(TOKENS_DIR, INSTANCE, TENANT, username)

class AbacoSpawnerError(Exception):
    def __init__(self, msg):
        self.message = msg


class AbacoSpawnerModelError(AbacoSpawnerError):
    pass

def get_agave_exception_content(e):
    """Check if an Agave exception has content"""
    try:
        return e.response.content
    except Exception:
        return ""

def get_config_metadata_name():
    return 'config.{}.{}.jhub'.format(
        os.environ.get('TENANT'),
        os.environ.get('INSTANCE'))

class AbacoSpawner(Spawner):
    """Spawner class that leverages an Abaco actor to spawn notebook servers across a datacenter.

    The high-level algorithm is as follows:
    1. Hub process sends a message to an Abaco actor with metadata about the notebook server to launch.
    2. Actor will manage launching the server, either by talking to a container API (e.g. Docker, Swarm or k8s) or by
       launching an HPC job via Agave.
    3. Actor will report the resulting IP and port back to the hub process via shared database or the agave
       metadata service.

    """

    def get_service_client(self):
        """Returns an agave client representing the service account. This client can be used to access
        the authorized endpoints such as the Abaco endpoint."""
        if not os.environ.get('CALL_ACTOR', True):
            self.log.info("Skipping call to actor since CALL_ACTOR was False.")
        service_token = os.environ.get('AGAVE_SERVICE_TOKEN')
        if not service_token:
            raise Exception("Missing SERVICE_TOKEN configuration.")
        base_url = os.environ.get('AGAVE_BASE_URL', "https://api.tacc.utexas.edu")
        return Agave(api_server=base_url, token=service_token)

    def get_oauth_client(self, base_url, access_token, refresh_token):
        return Agave(api_server=base_url, token=access_token, refresh_token=refresh_token)

    def load_state(self, state):
        """Restore state of spawner from database.

        Called for each user's spawner after the hub process restarts.

        `state` is a dict that'll contain the value returned by `get_state` of
        the spawner, or {} if the spawner hasn't persisted any state yet.

        Override in subclasses to restore any extra state that is needed to track
        the single-user server for that user. Subclasses should call super().
        """
        # todo -
        pass

    def get_state(self):
        """Save state of spawner into database.

        A black box of extra state for custom spawners. The returned value of this is
        passed to `load_state`.

        Subclasses should call `super().get_state()`, augment the state returned from
        there, and return that state.

        Returns
        -------
        state: dict
             a JSONable dict of state
        """
        # todo -
        state = {}
        return state

    async def start(self):
        """Start the single-user server

        Returns:
          (str, int): the (ip, port) where the Hub can connect to the server.

        """
        self.actor_id = os.environ.get('ACTOR_ID')
        self.tenant = TENANT
        self.instance = INSTANCE
        self.set_agave_access_data()
        self.get_tas_data()

        ag = self.get_service_client()
        q={'name': get_config_metadata_name()}
        self.configs = ag.meta.listMetadata(q=str(q))[0]['value']

        message = {
                'actor_id': os.environ.get('ACTOR_ID'),
                'tenant': self.tenant,
                'instance': self.instance,
                'username': self.user.name,
                'command': 'START',
                'params': {
                     "uid": self.configs.get('uid', getattr(self, 'tas_uid', None)),
                     "gid": self.configs.get('gid', getattr(self, 'tas_gid', None)),
                     "name": "{}-{}-{}-Jhub".format(self.user.name, self.tenant, self.instance),
                     "nb_mem_limit": os.environ.get('NB_MEM_LIMIT'),
                     "environment": self.get_env()
                    }
                }

        message['params']['environment']['JUPYTERHUB_API_URL'] = 'https://{}:{}/hub/api'.format(
            os.environ.get('HUB_IP'), os.environ.get('HUB_PORT'))

        if len(self.configs.get('images')) == 1: #only 1 image option
            message['params']['image'] = self.configs.get('images')[0]
        else:
            message['params']['image'] = self.user_options['image']

        template_vars = {
            'username': self.user.name,
            'tenant_id': self.tenant,
        }
        if hasattr(self, 'tas_homedir'):
            template_vars['tas_homeDirectory'] = self.tas_homedir

        message['params']['volume_mounts'] = []
        volume_mounts = self.configs.get('volume_mounts')

        if len(volume_mounts):
            for vol in volume_mounts:
                message['params']['volume_mounts'].append(vol.format(**template_vars))

        message['params']['volume_mounts'].append('{}:{}:/etc/.agpy:ro'.format(
            os.environ.get("NETWORK_STORAGE"), os.path.join(get_user_token_dir(self.user.name), '.agpy')))
        message['params']['volume_mounts'].append('{}:{}:/home/jupyter/.agave/current:ro'.format(
            os.environ.get("NETWORK_STORAGE"), os.path.join(get_user_token_dir(self.user.name), 'current')))

        projects = self.get_projects()
        if projects:
            message['params']['volume_mounts'] = message['params']['volume_mounts'] + projects

        notebook = NotebookMetadata(self.user.name, ag)
        notebook.set_submitted()

        try:
            self.log.info("Calling actor {} to start {} {} jupyterhub for user: {}. Message: {}".format(self.actor_id, self.tenant, self.instance, self.user.name, message))
            rsp = ag.actors.sendMessage(actorId=self.actor_id, body={'message': message})
        except Exception as e:
            msg = "Error executing actor. Execption: {}. Content: {}".format(e, get_agave_exception_content(e))
            self.log.error(msg)

        self.log.info("Called actor {}. Response: {}".format(self.actor_id, rsp))

        notebook = self.check_notebook_status(ag, NotebookMetadata.ready_status)
        self.log.info("{} {} jupyterhub for user: {} is {}. ip: {}. "
                      "port: {}".format(self.tenant, self.instance, self.user.name,
                                        notebook.value['status'], notebook.value['ip'], notebook.value['port']))
        return str(notebook.value['ip']), int(notebook.value['port'])

    async def stop(self, now=False):
        """Stop the single-user server

        If `now` is False (default), shutdown the server as gracefully as possible,
        e.g. starting with SIGINT, then SIGTERM, then SIGKILL.
        If `now` is True, terminate the server immediately.

        The coroutine should return when the single-user server process is no longer running.

        Must be a coroutine.
        """
        ag = self.get_service_client()
        q={'name': get_config_metadata_name()}
        self.configs = ag.meta.listMetadata(q=str(q))[0]['value']

        message = {
            'tenant': self.tenant,
            'instance': self.instance,
            'username': self.user.name,
            'command': 'STOP',
            'params': {
                 "name": "{}-{}-{}-Jhub".format(self.user.name, self.tenant, self.instance),
                }
            }

        notebook = NotebookMetadata(self.user.name, ag)
        notebook.set_stop_submitted()

        try:
            self.log.info("Calling actor {} to stop {} {} jupyterhub for user: {}".format(self.actor_id, self.tenant, self.instance, self.user.name))
            rsp = ag.actors.sendMessage(actorId=self.actor_id, body={'message': message})
        except Exception as e:
            msg = "Error executing actor. Execption: {}. Content: {}".format(e, get_agave_exception_content(e))
            self.log.error(msg)

        self.log.info("Called actor {}. Response: {}".format(self.actor_id, rsp))

        notebook = self.check_notebook_status(ag, NotebookMetadata.stopped_status)
        self.log.info("{} {} jupyterhub for user: {} is {}".format(self.tenant, self.instance, self.user.name, notebook.value['status']))
        return notebook.value['status']


    async def poll(self):
        """Check if the single-user process is running

        Returns:
          None if single-user process is running.
          Integer exit status (0 if unknown), if it is not running.

        State transitions, behavior, and return response:

        - If the Spawner has not been initialized (neither loaded state, nor called start),
          it should behave as if it is not running (status=0).

        - If the Spawner has not finished starting,
          it should behave as if it is running (status=None).

        Design assumptions about when `poll` may be called:

        - On Hub launch: `poll` may be called before `start` when state is loaded on Hub launch.
          `poll` should return exit status 0 (unknown) if the Spawner has not been initialized via
          `load_state` or `start`.

        - If `.start()` is async: `poll` may be called during any yielded portions of the `start`
          process. `poll` should return None when `start` is yielded, indicating that the `start`
          process has not yet completed.
        """
        ag = self.get_service_client()
        notebook = NotebookMetadata(self.user.name, ag)
        if notebook.value['status'] == (NotebookMetadata.ready_status or NotebookMetadata.submitted_status):
            return None
        else:
            return 0

    def set_agave_access_data(self):
        """
        Returns the access token and base URL cached in the agavepy file
        :return:
        """

        token_file = os.path.join(get_user_token_dir(self.user.name), '.agpy')
        self.log.info("spawner looking for token file: {} for user: {}".format(token_file, self.user.name))
        if not os.path.exists(token_file):
            self.log.warn("abacospawner did not find a token file at {}".format(token_file))
            self.access_token = None
            return None
        try:
            data = json.load(open(token_file))
        except ValueError:
            self.log.warn('could not ready json from token file')
            return None
        try:
            self.access_token = data[0]['token']
            self.log.info("Setting token: {}".format(self.access_token))
            self.refresh_token = data[0]['refresh_token']
            self.log.info("Setting refresh token: {}".format(self.refresh_token))
            self.url = data[0]['api_server']
            self.log.info("Setting url: {}".format(self.url))
            return None
        except (TypeError, KeyError):
            self.access_token = None
            self.refresh_token = None
            self.url = None
            self.log.warn("token file did not have an access token and/or an api_server. data: {}".format(data))
        return None

    def get_projects(self):
        self.host_projects_root_dir = self.configs.get('host_projects_root_dir')
        self.container_projects_root_dir = self.configs.get('container_projects_root_dir')
        if not self.host_projects_root_dir or not self.container_projects_root_dir:
            self.log.info("No host_projects_root_dir or container_projects_root_dir. configs: {}".format(self.configs))
            return None
        if not self.access_token or not self.url:
            self.log.info("no access_token or url")
            return None
        url = '{}/projects/v2/'.format(self.url)

        try:
            ag = self.get_oauth_client(self.url, self.access_token, self.refresh_token)
            rsp = ag.geturl(url)
        except Exception as e:
            self.log.warn("Got exception calling /projects: {}".format(e))
            return None
        try:
            data = rsp.json()
        except ValueError as e:
            self.log.warn("Did not get JSON from /projects. Exception: {}".format(e))
            self.log.warn("Full response from service: {}".format(rsp))
            self.log.warn("url used: {}".format(url))
            return None
        projects = data.get('projects')
        self.log.info("service returned projects: {}".format(projects))
        try:
            self.log.info("Found {} projects".format(len(projects)))
            user_projects = []
        except TypeError:
            self.log.error("Projects data has no length.")
            self.log.info("response: {}, data: {}".format(rsp, data))
            return None
        for p in projects:
            uuid = p.get('uuid')
            if not uuid:
                self.log.warn("Did not get a uuid for a project: {}".format(p))
                continue
            project_id = p.get('value').get('projectId')
            if not project_id:
                self.log.warn("Did not get a projectId for a project: {}".format(p))
                continue

            user_projects.append('{}/{}:{}/{}:rw'.format(self.host_projects_root_dir,
                                                  uuid, self.container_projects_root_dir, project_id))
        return user_projects

    def get_tas_data(self):
        """Get the TACC uid, gid and homedir for this user from the TAS API."""
        self.log.info("Top of get_tas_data")
        if not TAS_ROLE_ACCT:
            self.log.error("No TAS_ROLE_ACCT configured. Aborting.")
            return
        if not TAS_ROLE_PASS:
            self.log.error("No TAS_ROLE_PASS configured. Aborting.")
            return
        url = '{}/users/username/{}'.format(TAS_URL_BASE, self.user.name)
        headers = {'Content-type': 'application/json',
                   'Accept': 'application/json'
                   }
        try:
            rsp = requests.get(url,
                               headers=headers,
                               auth=requests.auth.HTTPBasicAuth(TAS_ROLE_ACCT, TAS_ROLE_PASS))
        except Exception as e:
            self.log.error("Got an exception from TAS API. "
                           "Exception: {}. url: {}. TAS_ROLE_ACCT: {}".format(e, url, TAS_ROLE_ACCT))
            return
        try:
            data = rsp.json()
        except Exception as e:
            self.log.error("Did not get JSON from TAS API. rsp: {}"
                           "Exception: {}. url: {}. TAS_ROLE_ACCT: {}".format(rsp, e, url, TAS_ROLE_ACCT))
            return
        try:
            self.tas_uid = data['result']['uid']
            self.tas_homedir = data['result']['homeDirectory']
        except Exception as e:
            self.log.error("Did not get attributes from TAS API. rsp: {}"
                           "Exception: {}. url: {}. TAS_ROLE_ACCT: {}".format(rsp, e, url, TAS_ROLE_ACCT))
            return

        # first look for an "extended profile" record in agave metadata. such a record might have the
        # gid to use for this user.
        self.tas_gid = None
        if self.access_token and self.url and self.refresh_token:
            ag = self.get_oauth_client(self.url, self.access_token, self.refresh_token)
            meta_name = 'profile.{}.{}'.format(self.tenant, self.user.name)
            q = "{'name': '" + meta_name + "'}"
            self.log.info("using query: {}".format(q))
            try:
                rsp = ag.meta.listMetadata(q=q)
            except Exception as e:
                self.log.error("Got an exception trying to retrieve the extended profile. Exception: {}".format(e))
            try:
                self.tas_gid = rsp[0].value['posix_gid']
            except IndexError:
                self.tas_gid = None
            except Exception as e:
                self.log.error("Got an exception trying to retrieve the gid from the extended profile. Exception: {}".format(e))
        # if the instance has a configured TAS_GID to use we will use that; otherwise,
        # we fall back on using the user's uid as the gid, which is (almost) always safe)
        if not self.tas_gid:
            self.tas_gid = os.environ.get('TAS_GID', self.tas_uid)
        self.log.info("Setting the following TAS data: uid:{} gid:{} homedir:{}".format(self.tas_uid,
                                                                                        self.tas_gid,
                                                                                        self.tas_homedir))

    def check_notebook_status(self, ag, status_needed):
        i=0
        notebook = NotebookMetadata(self.user.name, ag)
        while notebook.value['status'] != status_needed and i < 100 :
            notebook = NotebookMetadata(self.user.name, ag)
            i = i + 1
        return notebook

    def options_from_form(self, formdata):
        options = {}
        options['image'] = formdata['image'][0]
        return options

class NotebookMetadata(object):
    """Model to hold metadata about a specific user's notebook session."""

    pending_status = "PENDING"
    submitted_status = "SUBMITTED"
    ready_status = "READY"
    stop_submitted_status = "STOP_SUBMITTED"
    stopped_status = "STOPPED"
    error_status = "ERROR"

    def get_metadata_name(self, username):
        """ Return the name associated with a metadata record for a given username and Jupyter instance.
        :param user:
        :return:
        """
        return '{}-{}-{}-JHub'.format(username, self.tenant, self.instance)

    def _get_meta_dict(self, **kwargs):
        """Returns the basic Python dictionary containing the metadata to be stored in Agave."""
        return {"name": self.name,
                "value": {"name": self.name,
                          "instance": self.instance,
                          "tenant": self.tenant,
                          "ip": kwargs.get('ip'),
                          "port": kwargs.get('port'),
                          "status": kwargs.get('status')}}

    def _get_meta(self, ag):
        """Retrieve the meta record from Agave using the agave client, `ag`."""
        try:
            q={'name': self.name}
            records = ag.meta.listMetadata(q=str(q))
        except Exception as e:
            msg = "Python exception trying to get the meta record for user {}. Exception: {}".format(self.username, e)
            logger.error(msg)
            raise AbacoSpawnerModelError(msg)
        for m in records:
            if m['name'] == self.name:
                self.uuid = m['uuid']
                self.value = m['value']
                return m
        else:
            msg = "Did not find meta record for user: {}".format(self.name)
            logger.warning(msg)
            raise AbacoSpawnerModelError(msg)

    def _create_meta(self, ag):
        """Create the meta record in Agave using the agave client, `ag`. """
        d = self._get_meta_dict(status=self.pending_status)
        try:
            m = ag.meta.addMetadata(body=json.dumps(d))
        except Exception as e:
            msg = "Exception trying to create the meta record for user {}. Exception: {}".format(self.username, e)
            logger.error(msg)
            raise logger.error(msg)
        self.uuid = m['uuid']
        self.value = m['value']

    def _update_meta(self, ag, d):
        """Update the metadata record value to the data in `d`, a dictionary."""
        if not hasattr(self, 'uuid') or not self.uuid:
            msg = "NotebookMetadata object has no uuid."
            logger.error(msg)
            raise AbacoSpawnerModelError(msg)
        if not d:
            msg = "dictionary required for updating metadata."
            logger.error(msg)
            raise AbacoSpawnerModelError(msg)
        try:
            m = ag.meta.updateMetadata(uuid=self.uuid, body=d)
        except Exception as e:
            msg = "Python exception trying to update the meta record for user {}. Exception: {}".format(self.username, e)
            logger.error(msg)
            raise AbacoSpawnerModelError(msg)
        self.value = m['value']

    def __init__(self, username, ag):
        """
        Construct a metadata object representing a user's notebook server for a JupyterHub instance and tenant. This
        constructor will attempt to fetch the associated metadata record from Agave, but if it does not exist it will
        create it automatically. In the later case, the metadata record will be created in PENDING status.
        """
        if not username:
            raise AbacoSpawnerModelError("no user defined.")
        self.instance = os.environ.get('INSTANCE', 'develop')
        self.tenant = os.environ.get('TENANT', 'dev')
        self.username = username
        self.name = self.get_metadata_name(username)
        self.ag = ag

        try:
            # try to retrieve the metadata record from agave
            self._get_meta(ag)
        except AbacoSpawnerModelError:
            # if that failed, assume the user does not yet have a meta data record and create one now:
            msg = "Creating meta record for user: {}".format(self.name)
            logger.info(msg)
            m = self._create_meta(ag)

    def set_submitted(self):
        """Update the status on the user's metadata record for a submitted notebook session."""
        d = self._get_meta_dict(status=self.submitted_status)
        return self._update_meta(self.ag, d)

    def set_ready(self, ip, port):
        """Update the status and URL on the user's metadata record for a ready notebook session."""
        d = self._get_meta_dict(ip=ip, port=port, status=self.ready_status)
        return self._update_meta(self.ag, d)

    def set_error(self):
        """Update the status to error on the user's metadata record for a failed notebook session."""
        d = self._get_meta_dict(status=self.error_status)
        return self._update_meta(self.ag, d)

    def set_stopped(self):
        """Update the status to stopped on the user's metadata record for a stopped notebook session."""
        d = self._get_meta_dict(status=self.stopped_status)
        return self._update_meta(self.ag, d)

    def set_stop_submitted(self):
        """Update the status to stopped on the user's metadata record for a stopped notebook session."""
        d = self._get_meta_dict(status=self.stop_submitted_status)
        return self._update_meta(self.ag, d)

    def set_pending(self):
        """Update the status to pending on the user's metadata record for a stopped notebook session."""
        d = self._get_meta_dict(status=self.stopped_status)
        return self._update_meta(self.ag, d)

    def get_status(self):
        """Return the status of associated with this notebook."""
        # refresh the object's representation from Agave:
        return self.value['status']
