# put in /home/apim/start_notebook.py on the swarm manager
# this is the script that the actor calls

import json
import subprocess
import sys
import re

message = json.loads(sys.argv[1])

volume_mounts = ''
volumes=''
if len(message['params']['volume_mounts']):
    for item in message['params']['volume_mounts']:
        m=item.split(":")
        #volume names must consist of lower case alphanumeric characters or '-',
        #and must start and end with an alphanumeric character (e.g. 'my-name',  or '123-abc',
        # regex used for validation is '[a-z0-9]([-a-z0-9]*[a-z0-9])?')
        vol_name = re.sub(r'([^a-z0-9-\s]+?)', '', m[2].split('/')[-1].lower())
        read_only = 'true' if m[3] == 'ro' else 'false'
        volumes = volumes + '''      - name: {}
        nfs:
          server: {}
          path: {}
          readOnly: {}\n'''.format(vol_name, m[0], m[1], read_only)
        volume_mounts = volume_mounts + '''        - name: {}
          mountPath: "{}"\n'''.format(vol_name, m[2])

env = message['params']['environment']

environment = ''
if len(env):
    for k,v in env.items():
        environment = environment + '        - name: {}\n          value: {}\n'.format(k,v)

params = {
    'tenant': message['tenant'],
    'instance': message['instance'],
    'username': message['username'],
    'uid':message['params']['uid'],
    'gid':message['params']['gid'],
    'name':message['params']['name'].lower(), #k8 names must consist of lower case alphanumeric characters, '-' or '.', and must start and end with an alphanumeric character
    # 'nb_mem_limit':message['params']['nb_mem_limit'],
    'image':message['params']['image'],
    'volume_mounts':volume_mounts,
    'volumes': volumes,
    'environment': environment
}

pod = '''
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {name}
  labels:
    app: {name}
    tenant: {tenant}
    instance: {instance}
    username: {username}
spec:
  selector:
    matchLabels:
      app: {name}
  template:
    metadata:
      labels:
        app: {name}
        tenant: {tenant}
        instance: {instance}
        username: {username}
    spec:
      securityContext:
        runAsUser: {uid}
        runAsGroup: {gid}
      containers:
      - name: {name}
        image: {image}
        env:
{environment}
        ports:
        - name: http
          containerPort: 8888
        volumeMounts:
{volume_mounts}
      volumes:
{volumes}
'''

pod=pod.format(**params)

process = subprocess.run(['kubectl', 'apply', '-f', '-'], input=pod, stdout=subprocess.PIPE, encoding='utf-8')

status = ''
while status != 'Running':
    process = subprocess.run(['kubectl', 'get', 'pods', '-l', 'app={}'.format(params['name'])], stdout=subprocess.PIPE, encoding='utf-8')
    output = process.stdout.split()
    pod_name = output[5]
    status = output[7]

service = '''
apiVersion: v1
kind: Service
metadata:  
  name: {name}
  labels:
    app: {name}
    tenant: {tenant}
    instance: {instance}
    username: {username}
spec:
  selector:    
    app: {name}
  type: NodePort
  ports:  
  - name: http
    port: 8888
    targetPort: 8888
'''
service=service.format(**params)

process = subprocess.run(['kubectl', 'apply', '-f', '-'], input=service, stdout=subprocess.PIPE, encoding='utf-8')

process = subprocess.run(['kubectl', 'get', 'service', '-l', 'app={}'.format(params['name'])], stdout=subprocess.PIPE, encoding='utf-8')
#process.stdout.split() gives ['NAME', 'TYPE', 'CLUSTER-IP', 'EXTERNAL-IP', 'PORT(S)', 'AGE', 'jupyter', 'NodePort', '10.97.5.88', '<none>', '8888:30117/TCP', '5s']
port = process.stdout.split()[10].split(':')[1].split('/')[0]

print(port)
