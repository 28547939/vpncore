#!/bin/sh

BASE=/home/jail

echo JAIL_NAME=$JAIL_NAME
JAIL_NAME=$(echo $JAIL_NAME | sed -Ee 's/[^a-zA-Z0-9_-]//g')
echo JAIL_NAME=$JAIL_NAME


jail -v -p 1 -c \
    name=$JAIL_NAME \
    host.hostname=$JAIL_HOSTNAME    \
    path=$FS_ROOT                   \
    vnet=new    \
    persist \
    allow.mount=1   \
    allow.mount.devfs=1 \
    mount.devfs \
    devfs_ruleset=5 \
    allow.mount.nullfs=1    \
    enforce_statfs=1    \
    exec.clean=0    \
    exec.consolelog=$BASE/log/exec-consolelog-$JAIL_NAME.log    \
    exec.start="/bin/sh /etc/rc"   \
    exec.poststart="$BASE/src/exec-poststart.sh"

   # allow.raw_sockets=1 \
