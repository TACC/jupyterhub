# Image: taccsciapps/jupyterhub:1.0.0-1.0.0

from jupyterhub/jupyterhub:1.0.0

RUN apt-get update && apt-get install -y build-essential chromium-browser curl unzip vim
RUN pip install oauthenticator agavepy jupyterhub-kubespawner notebook ipdb humanfriendly git+https://github.com/kubernetes-client/python.git selenium webdriver-manager

ADD spawner_hooks.py /opt/conda/lib/python3.6/site-packages/jupyterhub/spawner_hooks.py
ADD jupyterhub_config.py /srv/jupyterhub/jupyterhub_config.py
ADD agave.py /opt/conda/lib/python3.6/site-packages/oauthenticator/agave.py
ADD common.py /opt/conda/lib/python3.6/site-packages/jupyterhub/common.py
ADD selenium/ /srv/jupyterhub/selenium
