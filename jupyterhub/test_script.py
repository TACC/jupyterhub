import docker

# Starts up jupyter notebook docker image

# This script uses python to run a docker command similar to the following:
# docker run --rm -p 8888:8888 -v $(pwd)/jupyter-notebook-localconf.py:/home/jupyter/.jupyter/jupyter_notebook_config.py taccsciapps/jupyteruser-ds start-notebook.sh

client = docker.from_env()
image = client.images.pull('taccsciapps/jupyteruser-ds:1.2.4')

client.containers.run(
    image,
    ports={'8888/tcp': 8888},
    volumes={'/jupyter-notebook-localconf.py':
                {'bind': '/home/jupyter/.jupyter/jupyter_notebook_config.py',
                 'mode': 'rw'}
             }
)

