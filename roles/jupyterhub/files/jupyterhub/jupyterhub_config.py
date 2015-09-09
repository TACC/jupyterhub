c = get_config()
c.JupyterHub.spawner_class = 'dockerspawner.SystemUserSpawner'

import jupyter_client
c.JupyterHub.hub_ip = jupyter_client.localinterfaces.public_ips()[0]

c.DockerSpawner.container_ip = '172.17.42.1'
c.SystemUserSpawner.host_homedir_format_string = '/tmp/{username}'
