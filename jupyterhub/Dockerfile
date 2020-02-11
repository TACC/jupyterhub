# Image: taccsciapps/jupyterhub:1.0.0-1.0.0

from jupyterhub/jupyterhub:1.0.0

RUN apt-get update && apt-get install -y build-essential
RUN pip install oauthenticator agavepy jupyterhub-kubespawner notebook ipdb git+https://github.com/kubernetes-client/python.git

ADD taccspawner.py /opt/conda/lib/python3.6/site-packages/jupyterhub/taccspawner.py
ADD jupyterhub_config.py /srv/jupyterhub/jupyterhub_config.py
ADD agave.py /opt/conda/lib/python3.6/site-packages/oauthenticator/agave.py
