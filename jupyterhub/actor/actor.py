# Abaco actor to manage Jupyterhub notebooks and associated metadata.
# Supports the following actions, specified by passing the following values in the
# "command" string:
#    1. START - Start a new Jupyterhub notebooks for a user.
#    2. STOP  - Stop and remove the Jupyterhub notebook container associated with a user.
#    3. SYNC  - Sync the metadata record with the existing state of the terminal container
#               associated with a user. The Jupyterhub existence of an Jupyterhub container or its state
#               will not be affected by this command; only the metadata record will be
#               modified.
#
# Required parameters. The following parameters should be registered in the actor's default
# environment or passed in the JSON message when executing the actor. This actor looks
# in the context created by the Abaco SDK for these values.
#
# PASSED IN AT REGISTRATION (these can be overritten at run time):
#    - execution_ssh_key (required): The KEY used to access the execution host(s).

# PARAMETERS to ALL commands:
#    - username (required): The username to act on.
#    - service_token (required): token for service account.
#    - agave_base_url (required): base url for service account
#    - command (optional, defaults to START): the command to use.
#    - tenant (required)
#    - instance (required) e.g. local, dev, staging, prod
#    - username (required)
#    - execution_ip and execution_ssh_user (optional): Connection information for the
#           execution host.
#
# Building the image (build from project root):
#    docker build -t taccsciapps/XXXX -f jupyterhub/actor/Dockerfile .
#
# Testing locally:
#    docker run -it --rm -e MSG='{...}' taccsciapps/XXXX

import io
import json
import os
import paramiko

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
        message.get('tenant'),
        message.get('instance'))

def get_ssh_connection(context, message):
    """Create an SSH connection to the execution host."""
    execution_ip = context.get('execution_ip')
    if message.get('execution_ip'):
        execution_ip = message.get('execution_ip')
    execution_ssh_user = context.get('execution_ssh_user', 'root')
    if message.get('execution_ssh_user'):
        execution_ssh_user = message.get('execution_ssh_user')
    execution_ssh_key = context.get('execution_ssh_key')
    if message.get('execution_ssh_key'):
        execution_ssh_key = message.get('execution_ssh_key')
    # instantiate an SSH client
    ssh = paramiko.SSHClient()
    # create an RSAKey object from the private key string.
    pkey = paramiko.RSAKey.from_private_key(io.StringIO(execution_ssh_key))
    # ignore known_hosts errors:
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    # connect to the execution system
    ssh.connect(execution_ip, username=execution_ssh_user, pkey=pkey)
    return ssh, execution_ip

def launch_notebook(message, conn, ip):
    """Launch an JupyterHub notebook container."""
    username = message.get('username')
    # command = 'cd /home/apim; ' \
              # 'echo "{}" > {}.txt; ' \
              # 'python start_notebook.py {}'.format(message.get('params'), username, message.get('params'))
    command = 'python /home/apim/start_notebook.py \'{}\''.format(json.dumps(message.get('params')))
    print("command: {}".format(command))
    ssh_stdin, ssh_stdout, ssh_stderr = conn.exec_command(command)
    print("ssh connection made and command executed")
    st_out = ssh_stdout.read()
    print("st out from command: {}".format(st_out))
    st_err = ssh_stderr.read()
    print("st err from command: {}".format(st_err))
    try:
        port = st_out.splitlines()[-2].decode('ascii')
    except IndexError:
        print("There was an IndexError parsing the standard out of the notebook launch for the port. "
              "Standard out was: {}".format(st_out))
        return ""
    print("got a port: {}".format(port))
    return port

def stop_notebook(container_name, conn):
    """Stop and remove a jupyterHub notebook container."""
    command = 'docker service rm {}'.format(container_name)
    _, ssh_stdout, ssh_stderr = conn.exec_command(command)
    st_out = ssh_stdout.read()
    print("st out from command: {}".format(st_out))
    st_err = ssh_stderr.read()
    print("st err from command: {}".format(st_err))

def main():
    context = get_context()
    message = context['message_dict']
    ag = get_agave_client(message)

    query={'name': get_config_metadata_name(message)}
    configs = ag.meta.listMetadata(q=str(query))[0]['value']

    command = message.get('command', 'START')

    # print('context["message_dict"]', message)
    # print('context["raw_message"]', context["raw_message"])
    print('context', context)
    print('configs (from metadata)', configs)

    tenant = message.get('tenant')
    instance = message.get('instance')
    username = message.get('username')

    os.environ['TENANT'] = tenant #these are needed to find the current NotebookMetadata opbject
    os.environ['INSTANCE'] = instance

    notebook = NotebookMetadata(message.get('username'), ag)

    conn, ip = get_ssh_connection(context, message)
    if command == 'START':
        #####
        if message['params']['image'] == 'HPC':
            try:
                actor_id = message.get('actor_id')
                resp = ag.actors.addNonce(actorId=actor_id, body={'maxUses':1, 'level':'EXECUTE'})
                nonce = resp['id']
                url = '{}/actors/v2/{}/messages?x-nonce={}'.format(message.get("agave_base_url", "https://api.tacc.utexas.edu"), actor_id, nonce)
                job_dict = {
                    'name': message.get('params').get('name'),
                    'parameters': {
                        'nonce_url': url,
                        'tenant': tenant,
                        'instance': instance,
                        'username': username,
                        'uid': message.get('params').get('uid'),
                        'gid': message.get('params').get('gid'),
                        'token': message.get("service_token"),
                        'api_server': message.get("agave_base_url", "https://api.tacc.utexas.edu")
                    }
                }
                print('Job Submission Body: {}'.format(job_dict))
                response = ag.jobs.submit(body=job_dict)
                print('Job Submission Response: {}'.format(response))
            except Exception as e:
                err_resp = e.response.json()
                err_resp['status_code'] = e.response.status_code
                print('Error response'.format(err_resp))
        #####
        else:
            print('****'*100, 'notebook value before calling launch_notebook for user {}: {}'.format(message.get('username'), notebook.value))
            port = launch_notebook(message, conn, ip)
            ip_to_return = context.get('execution_private_ip', ip)
            if message.get('execution_private_ip'):
                ip_to_return = message.get('execution_private_ip')
            print('context.get: {} {} {}'.format(context.get('execution_ip'),context.get('execution_private_ip'),context.get('execution_ssh_key')))
            print('context.get execution_private_ip: {} '.format(context.get('execution_private_ip')))
            print('context.get: {} '.format(context.get('execution_private_ip', ip)))
            print('ip_to_return: {} '.format(ip_to_return))
            #todo see if url is used at all
            notebook.set_ready(ip=ip_to_return, port=port)
            print('****'*100, 'notebook value: {}'.format(notebook.value))

    elif command == 'STOP':
        params = message.get('params')
        container_name = params['name']
        print('stopping container: ', container_name)
        stop_notebook(container_name, conn)
        notebook.set_stopped()

    elif command == 'UPDATE': #this should only come from an HPC agave app
        ag = get_agave_client(message)
        ip = message.get('ip')
        port = message.get('port')
        notebook = NotebookMetadata(message.get('username'), ag)
        notebook.set_ready(ip=ip, port=port)
        print('****' * 100, 'updated notebook value: {}'.format(notebook.value))



main()
