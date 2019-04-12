# Image: taccsciapps/hub-actor
# build from project root
# docker build -f jupyterhub/actor/Dockerfile -t taccsciapps/hub-actor .

FROM python:3.6

RUN pip install --no-cache-dir agavepy jupyterhub

ADD jupyterhub/actor/actor.py /actor.py
ADD jupyterhub/actor/requirements.txt /requirements.txt
ADD jupyterhub/abacospawner.py /abacospawner.py

RUN pip install -r /requirements.txt

CMD ["python", "/actor.py"]
