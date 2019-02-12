# put in /home/apim/start_notebook.py on the swarm manager
# this is the script that the actor calls

import json
import sys
import subprocess
import time

data = json.loads(sys.argv[1])
volumes = data['volume_mounts']

volume_mounts = ''
if len(volumes):
    for item in volumes:
        m=item.split(":")
        volume_mounts = volume_mounts + '--mount source={},target={},type=bind '.format(m[0],m[1])

params = {
    'uid':data['uid'],
    'gid':data['gid'],
    'name':data['name'],
    'image':data['image'],
    'volume_mounts':volume_mounts,
}

command = 'docker service create --name {name} --user {uid}:{gid} {volume_mounts} --publish 3000 {image}'.format(**params)
print('docker service create command: {}'.format(command))
process = subprocess.Popen(command, stdout=subprocess.PIPE, shell=True)
text = process.stdout.read()
print(text)

time.sleep(2)
inspect_command="port=\"$(docker service inspect {}|grep PublishedPort| awk ' {{ print substr($2, 1, length($2)-1) }} ')\"; echo $port".format(params['name'])
print('inspect_command to get the port: {}'.format(inspect_command))
process = subprocess.Popen(inspect_command, stdout=subprocess.PIPE, shell=True)

# command = 'docker service create --name {name} --user {uid}:{gid} {volume_mounts} --publish 3000 {image}'.format(**params)
# inspect_command="port=\"$(docker service inspect {}|grep PublishedPort| awk ' {{ print substr($2, 1, length($2)-1) }} ')\";".format(params['name'])
# full_command = "{}; sleep 5; {}; echo $port".format(command, inspect_command)
# print('subprocess command: {}'.format(full_command))
# process = subprocess.Popen("{}; sleep 5; {}; echo $port".format(command, params['name'], inspect_command), stdout=subprocess.PIPE, shell=True)

text = process.stdout.read()
print(text)
