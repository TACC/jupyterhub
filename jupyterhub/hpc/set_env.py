# python program to parse an arbitrary environment JSON string and produce a set of export statements to be used
# in a parent process.
#
# note that since a child process can never update the environment of a parent, this
# script should should be called from within a bash script as follows:
#
# eval `python3 set_env.py ${environment}`
#
# Example test execution: eval `python3 set_env.py '{"key1": "value1", "key2": "value2"}'`


import json
import sys

SINGULARITY_ENV_PREFIX = 'SINGULARITYENV_'

env = json.loads(sys.argv[1])
output = ''
if len(env):
    for k,v in env.items():
        output += 'export {}{}="{}"; '.format(SINGULARITY_ENV_PREFIX, k,v)

print(output)
