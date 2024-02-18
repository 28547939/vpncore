#!/bin/sh

set -o nounset
set -x

DYN_BASE=$ZFS_BASE/dynvpn-base

NAME=$1

src_snap=$(zfs list -o name -H -rt snapshot $DYN_BASE | tail -n 1)

if [ -z $src_snap ]; then 
	echo no snapshots available on $DYN_BASE
    exit 1
fi

zfs clone $src_snap $ZFS_BASE/$NAME
