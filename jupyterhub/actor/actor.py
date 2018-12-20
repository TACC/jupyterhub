# Abaco actor to manage IPT terminals and associated metadata.
# Supports the following actions, specified by passing the following values in the
# "command" string:
#    1. START - Start a new IPT terminal for a user.
#    2. STOP  - Stop and remove the IPT terminal container associated with a user.
#    3. SYNC  - Sync the metadata record with the existing state of the terminal container
#               associated with a user. The IPT existence of an IPT container or its state
#               will not be affected by this command; only the metadata record will be
#               modified.
#
# Required parameters. The following parameters should be registered in the actor's default
# environment or passed in the JSON message when executing the actor. This actor looks
# in the context created by the Abaco SDK for these values.
#
# PASSED IN AT REGISTRATION (these can be overritten at run time):
#    - execution_ssh_key (required): The KEY used to access the execution host(s).
#    - ipt_instance (required): The IPT instance string (e.g. "dev" or "prod").

# PARAMETERS to ALL commands:
#    - user_name (required): The username to act on.
#    - access_token (required): An OAuth access token representing the user.
#    - command (optional, defaults to START): the command to use.
#    - execution_ip and execution_ssh_user (optional): Connection information for the
#           execution host.
#    - api_server (optional): The Agave api_server to use.
#
# Building the image:
#    docker build -t taccsciapps/ipt-actor -f Dockerfile-actor .
#
# Testing locally:
#    docker run -it --rm -e MSG='{...}' taccsciapps/ipt-actor

import os
from agavepy.actors import get_context
from agavepy.agave import Agave
from abacospawner import NotebookMetadata


def get_agave_client(message):
    """Instantiate an Agave client using the access token and api server in the message."""
    token = message.get("service_token")
    api_server = message.get("agave_base_url", "https://api.tacc.utexas.edu")
    return Agave(api_server=api_server, token=token)

def get_config_metadata_name(message):
    return 'config.{}.{}.jhub'.format(
        message.get('tenant', 'designsafe-ci'),
        message.get('instance', 'local'))

def main():
    context = get_context()
    message = context['message_dict']
    ag = get_agave_client(message)
    q={'name': get_config_metadata_name(message)}
    configs = ag.meta.listMetadata(q=str(q))[0]['value']
    command = message.get('command', 'START')
    print('context["message_dict"]', message)
    print('context["raw_message"]', context["raw_message"])
    print('configs (from metadata)', configs)
    os.environ['TENANT'] = message.get('tenant')
    os.environ['INSTANCE'] = message.get('instance')
    notebook = NotebookMetadata(message.get('username'), ag)
    #todo call script and parse for ip and port
    from random import randint
    notebook.set_ip_and_port(ip='123.123.12.12', port=randint(1,100))


main()
