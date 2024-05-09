#!/bin/sh

set -o nounset
set -x

export VPN_ID=$1

export BASE=$(dirname $0)/..
. $BASE/scripts/common.sh

# main entry point - all scripts either inherit our environment, or 
# use specific variables stored on the filesystem by us (exec-poststart.sh)

# LOCAL_VPN_DIR: directory that $BASE is nullfs mounted on in the VPN jail, 
# 	relative to the jail root
export LOCAL_VPN_DIR=/mnt/vpn

export NAME=$(vpn_name $VPN_ID)
export JAIL_ROOT=$JAIL_BASE_PATH/$NAME

# VPN_DIR: the same directory as LOCAL_VPN_DIR, but relative to the host root
export VPN_DIR=${JAIL_ROOT}${LOCAL_VPN_DIR}


echo jail name is $NAME

# install relevant files that we have here into the base before cloning
sh $BASE/scripts/update-files.sh $NAME

sh $BASE/scripts/clone.sh $NAME

export IF_ID=$(vpn_ifid $VPN_ID)
export IF_ID_Anycast=$(vpn_anycast_ifid $VPN_ID)

export EPAIR=epair${IF_ID}
export EPAIR_ANYCAST=epair${IF_ID_Anycast}

export JAIL_LOCAL_ADDR=$(vpn_local_addr $VPN_ID)
export JAIL_ANYCAST_ADDR=$(vpn_anycast_addr $VPN_ID)

ifconfig ${EPAIR}a destroy
ifconfig $EPAIR create
ifconfig ${EPAIR_ANYCAST}a destroy
ifconfig $EPAIR_ANYCAST create

ifconfig bridge0 addm ${EPAIR}a
ifconfig bridge1 addm ${EPAIR_ANYCAST}a

mount -t nullfs $BASEJAIL_PATH $JAIL_ROOT/basejail

export HOSTNAME=$NAME.$HOST_HOSTNAME

RCCONF_PATH=$BASE/etc/rc-conf/$NAME.conf
cp $RCCONF_PATH $JAIL_ROOT/etc/rc.conf.local

# create this file ourselves every time since it's simple enough, and to 
# avoid hard-coding $LOCAL_VPN_DIR 
sudoers_path=$JAIL_ROOT/usr/local/etc/sudoers.d/openvpn-up
echo "openvpn ALL=(root) NOPASSWD: $LOCAL_VPN_DIR/scripts/openvpn-up.sh" > $sudoers_path
echo "openvpn ALL=(root) NOPASSWD: $LOCAL_VPN_DIR/scripts/load-gre.sh" >> $sudoers_path

jail -v -p 1 -c \
    name=$NAME \
    host.hostname=$HOSTNAME    \
    path=$JAIL_BASE_PATH/$NAME     \
    vnet=new    \
    persist \
    allow.mount.devfs=1 \
    mount.devfs \
    devfs_ruleset=5 \
    enforce_statfs=1    \
    exec.clean=0    \
    exec.consolelog=$(realpath $BASE/log)/exec-consolelog-$NAME.log    \
    exec.start="/bin/sh /etc/rc"   \
    exec.poststart="sh $BASE/scripts/exec-poststart.sh"


