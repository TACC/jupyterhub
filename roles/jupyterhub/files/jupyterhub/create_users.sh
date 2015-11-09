#!/bin/bash

adduser tacc
echo -e "tacc:tacc" | chpasswd
echo -e "root:root" | chpasswd
