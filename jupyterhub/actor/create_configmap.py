import json
import subprocess
import sys

message = json.loads(sys.argv[1])

params = {
    'tenant': message['tenant'],
    'instance': message['instance'],
    'username': message['username'],
    'name':message['params']['name'].lower(), #k8 names must consist of lower case alphanumeric characters, '-' or '.', and must start and end with an alphanumeric character
    'agpy':message['params']['agpy'],
    'current': message['params']['current']
}

current = '''
apiVersion: v1
kind: ConfigMap
metadata:  
  name: {name}-current
  labels:
    app: {name}
    tenant: {tenant}
    instance: {instance}
    username: {username}
data:
  current: "{current}"
'''
current=current.format(**params)

print(current)

process = subprocess.run(['kubectl', 'apply', '--validate=false', '-f', '-'], input=current, stdout=subprocess.PIPE, encoding='utf-8')

agpy = '''
apiVersion: v1
kind: ConfigMap
metadata:  
  name: {name}-agpy
  labels:
    app: {name}
    tenant: {tenant}
    instance: {instance}
    username: {username}
data:
  .agpy: "{agpy}"
'''
agpy=agpy.format(**params)

print(agpy)

process = subprocess.run(['kubectl', 'apply', '--validate=false', '-f', '-'], input=agpy, stdout=subprocess.PIPE, encoding='utf-8')

# process = subprocess.run(['kubectl', 'create', 'configmap', params['name'], '--from-file', '-'], input=current, stdout=subprocess.PIPE, encoding='utf-8')