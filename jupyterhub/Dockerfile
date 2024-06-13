# Image: taccsciapps/jupyterhub:1.0

#FROM jupyterhub/jupyterhub:1.0.0
#FROM taccsciapps/jupyterhub-ds:0.0.6
FROM jupyterhub/jupyterhub:3.1.0

RUN apt-get update 
RUN apt-get install -y build-essential curl chromium-browser unzip vim git

RUN pip install oauthenticator agavepy jupyterhub-kubespawner==4.3.0 notebook ipdb humanfriendly git+https://github.com/kubernetes-client/python.git selenium webdriver-manager

ADD agave.py /usr/local/lib/python3.10/dist-packages/oauthenticator/agave.py
ADD common.py /usr/local/lib/python3.10/dist-packages/jupyterhub/common.py
ADD selenium/ /srv/jupyterhub/selenium
ADD spawner_hooks.py /usr/local/lib/python3.10/dist-packages/jupyterhub/spawner_hooks.py
ADD jupyterhub_config.py /srv/jupyterhub/jupyterhub_config.py
ADD custom_templates /usr/local/share/jupyterhub/templates/custom_templates
ADD admin-react.js /usr/local/share/jupyterhub/static/js/admin-react.js
