FROM jupyter/jupyterhub

WORKDIR /root
RUN git clone https://github.com/jupyter/dockerspawner.git
WORKDIR /root/dockerspawner
RUN pip3 install -r requirements.txt
RUN python3 setup.py install


