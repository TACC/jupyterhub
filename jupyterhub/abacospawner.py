import os

from agavepy.agave import Agave
from jupyterhub.spawner import Spawner, LocalProcessSpawner

import asyncio
import errno
import json
import os
import pipes
import shutil
import signal
import sys
import warnings
import pwd
from subprocess import Popen
from tempfile import mkdtemp

# FIXME: remove when we drop Python 3.5 support
from async_generator import async_generator, yield_

from sqlalchemy import inspect

from tornado.ioloop import PeriodicCallback

from traitlets.config import LoggingConfigurable
from traitlets import (
    Any, Bool, Dict, Instance, Integer, Float, List, Unicode, Union,
    default, observe, validate,
)

# from .objects import Server
# from .traitlets import Command, ByteSpecification, Callable
# from .utils import iterate_until, maybe_future, random_port, url_path_join, exponential_backoff

import logging

logger = logging.getLogger(__name__)


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

def get_jupyter_instance():
    """Return the Jupyter instance string, default to using the develop instance."""
    return os.environ.get('INSTANCE', 'develop')

def get_jupyter_tenant():
    """Return the Jupyter tenant string, default to using the dev tenant."""
    return os.environ.get('TENANT', 'dev')

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
        ag = self.get_service_client()
        actor_id = os.environ.get('ACTOR_ID')
        username = self.user.name

        self.log.info("Calling actor {} to start {} {} jupyterhub for user: {}".format(actor_id, os.environ.get('TENANT'), os.environ.get('INSTANCE'), username))

        message = {'service_token': os.environ.get('AGAVE_SERVICE_TOKEN'),
                'agave_base_url': os.environ.get('AGAVE_BASE_URL'),
                'tenant': os.environ.get('TENANT'),
                'instance': os.environ.get('INSTANCE'),
                'username': username,
                'command': 'START'
                }
        try:
            rsp = ag.actors.sendMessage(actorId=actor_id, body={'message': message})
        except Exception as e:
            msg = "Error executing actor. Execption: {}. Content: {}".format(e, get_agave_exception_content(e))
            self.log.error(msg)

        self.log.info("Called actor {}. Message: {}. Response: {}".format(actor_id, message, rsp))
        notebook = NotebookMetadata(username, ag)
        old_status = notebook.get_status()
        notebook.set_submitted()
        # ip, port = self.get_ip_and_port(username, ag)
        ip, port = get_ip_and_port(username, ag)
        print('ip: {}.  port: {}'.format(ip, port))
        return str(ip), int(port)

    async def stop(self, now=False):
        """Stop the single-user server

        If `now` is False (default), shutdown the server as gracefully as possible,
        e.g. starting with SIGINT, then SIGTERM, then SIGKILL.
        If `now` is True, terminate the server immediately.

        The coroutine should return when the single-user server process is no longer running.

        Must be a coroutine.
        """
        raise NotImplementedError("Override in subclass. Must be a Tornado gen.coroutine.")

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
        # raise NotImplementedError("Override in subclass. Must be a Tornado gen.coroutine.")
        ag = self.get_service_client()
        username = self.user.name
        notebook = NotebookMetadata(username, ag)
        if notebook.value['ip'] and notebook.value['port']:
            return None
        else:
            return 0

    # async def get_ip_and_port(self, username, ag):
    #     print('getting ip and port')
    #     notebook = NotebookMetadata(username, ag)
    #     if notebook.value['ip'] and notebook.value['port']:
    #         return notebook.value['ip'], notebook.value['port']
    #     else:
    #         self.get_ip_and_port(username, ag)
def get_ip_and_port(username, ag):
    notebook = NotebookMetadata(username, ag)
    import ipdb; ipdb.set_trace()
    if not (notebook.value['ip'] and notebook.value['port']):
        return get_ip_and_port(username, ag)
    return str(notebook.value['ip']), int(notebook.value['port'])


class NotebookMetadata(object):
    """Model to hold metadata about a specific user's notebook session."""

    pending_status = "PENDING"
    submitted_status = "SUBMITTED"
    ready_status = "READY"
    stopped_status = "STOPPED"
    error_status = "ERROR"

    def get_metadata_name(self, username):
        """ Return the name associated with a metadata record for a given username and Jupyter instance.
        :param user:
        :return:
        """
        instance = get_jupyter_instance()
        tenant = get_jupyter_tenant()
        return '{}-{}-{}-JHub'.format(username, tenant, instance)

    # def _get_meta_dict(self, status, url=""):
    def _get_meta_dict(self, **kwargs):
        """Returns the basic Python dictionary containing the metadata to be stored in Agave."""
        return {"name": self.name,
                "value": {"name": self.name,
                          "instance": self.instance,
                          "tenant": self.tenant,
                          "ip": kwargs.get('ip'),
                          "port": kwargs.get('port'),
                          "status": kwargs.get('status'),
                          "url": kwargs.get('url')}}

    def _get_meta(self, ag):
        """Retrieve the meta record from Agave using the agave client, `ag`."""
        try:
            q={'name': self.get_metadata_name(self.username)}
            records = ag.meta.listMetadata(q=str(q))
            # records = ag.meta.listMetadata(search={'name.eq': self.get_metadata_name(self.username)})
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
            logger.error(msg)
            raise AbacoSpawnerModelError(msg)

    def _create_meta(self, ag):
        """Create the meta record in Agave using the agave client, `ag`. """
        name = self.get_metadata_name(self.username)
        d = self._get_meta_dict(status=self.pending_status)
        try:
            m = ag.meta.addMetadata(body=json.dumps(d))
        except Exception as e:
            msg = "Exception trying to create the meta record for user {}. Exception: {}".format(self.username, e)
            logger.error(msg)
            raise logger.error(msg)
        self.uuid = m['uuid']
        self.value = m['value']
        # share the metadata with the service account
        service_account = os.environ.get('AGAVE_SERVICE_ACCOUNT', "apitest")
        try:
            ag.meta.updateMetadataPermissions(uuid=self.uuid,
                                              body={'permission':'READ_WRITE',
                                                    'username': service_account})
        except Exception as e:
            msg = "Exception trying to share the meta record for user: {}. Exception:{}".format(self.username, e)
            logger.error(msg)
            raise AbacoSpawnerModelError(msg)

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
        self.name = self.get_metadata_name(username)
        self.instance = get_jupyter_instance()
        self.tenant = get_jupyter_tenant()
        self.username = username
        self.ag = ag

        try:
            # try to retrieve the metadata record from agave
            self._get_meta(ag)
        except AbacoSpawnerModelError:
            # if that failed, assume the user does not yet have a meta data record and create one now:
            m = self._create_meta(ag)

    def set_submitted(self):
        """Update the status on the user's metadata record for a submitted terminal session."""
        d = self._get_meta_dict(status=self.submitted_status, url="")
        return self._update_meta(self.ag, d)

    def set_ready(self, url):
        """Update the status and URL on the user's metadata record for a ready terminal session."""
        d = self._get_meta_dict(status=self.ready_status, url=url)
        return self._update_meta(self.ag, d)

    def set_error(self, url=None):
        """Update the status to error on the user's metadata record for a failed terminal session."""
        # if they don't pass a URL, simply use what is already in the metadata.
        if not url:
            url = self.value['url']
        d = self._get_meta_dict(status=self.error_status, url=url)
        return self._update_meta(self.ag, d)

    def set_stopped(self):
        """Update the status to stopped on the user's metadata record for a stopped terminal session."""
        d = self._get_meta_dict(status=self.stopped_status, url='')
        return self._update_meta(self.ag, d)

    def set_pending(self):
        """Update the status to pending on the user's metadata record for a stopped terminal session."""
        d = self._get_meta_dict(status=self.stopped_status, url='')
        return self._update_meta(self.ag, d)

    def get_status(self):
        """Return the status of associated with this terminal,."""
        # refresh the object's representation from Agave:
        return self.value['status']

    def set_ip_and_port(self, ip, port):
        """Update the status to pending on the user's metadata record for a stopped terminal session."""
        d = self._get_meta_dict(ip=ip, port=port)
        return self._update_meta(self.ag, d)
