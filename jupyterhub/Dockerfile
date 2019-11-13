# Image: taccsciapps/jupyterhub:1.0.0-1.0.0

from jupyterhub/jupyterhub:1.0.0

RUN apt-get update && apt-get install -y build-essential
RUN pip install oauthenticator agavepy notebook ipdb

ADD abacospawner.py /opt/conda/lib/python3.6/site-packages/jupyterhub/abacospawner.py
ADD jupyterhub_config.py /srv/jupyterhub/jupyterhub_config.py
ADD agave.py /opt/conda/lib/python3.6/site-packages/oauthenticator/agave.py
