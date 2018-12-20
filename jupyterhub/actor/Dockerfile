# build from project root
# docker build -f jupyterhub/actor/Dockerfile -t mlm55/jupyterhub_actor .

FROM python:3.6

RUN pip install --no-cache-dir agavepy jupyterhub
ADD jupyterhub/actor/actor.py /actor.py
ADD jupyterhub/abacospawner.py /abacospawner.py
CMD ["python", "/actor.py"]
