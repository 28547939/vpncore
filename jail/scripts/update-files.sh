#!/bin/sh

set -o nounset
set -x 


if [ $(zfs get -Ho value mounted $DYN_BASE) != "yes" ]; then
    zfs mount $DYN_BASE 
    if [ $? != 0 ]; then
        echo update-files failed
        exit 1
    fi
fi


mountpoint=$(zfs get mountpoint $DYN_BASE)

cp -v $BASE/files/container/sysctl.conf.local $mountpoint/usr/local/etc/
cp -v $BASE/files/container/openvpn-sudo $mountpoint/usr/local/etc/sudoers.d/
cp -v $BASE/files/container/extvpn-ipfw.sh $mountpoint/usr/local/etc/


