import os


c = get_config()
c.JupyterHub.spawner_class = 'dockerspawner.SystemUserSpawner'

import jupyter_client
c.JupyterHub.hub_ip = jupyter_client.localinterfaces.public_ips()[0]

c.DockerSpawner.container_ip = '172.17.42.1'
c.SystemUserSpawner.host_homedir_format_string = '/home/{username}'

c.JupyterHub.authenticator_class = 'oauthenticator.AgaveOAuthenticator'

c.AgaveOAuthenticator.oauth_callback_url = os.environ['OAUTH_CALLBACK_URL']
c.AgaveOAuthenticator.client_id = os.environ['AGAVE_CLIENT_ID']
c.AgaveOAuthenticator.client_secret = os.environ['AGAVE_CLIENT_SECRET']
c.AgaveOAuthenticator.agave_base_url = os.environ['AGAVE_BASE_URL']
c.AgaveOAuthenticator.agave_base_url = os.environ['AGAVE_TENANT_NAME']

c.AgaveMixin.agave_base_url = os.environ['AGAVE_BASE_URL']

