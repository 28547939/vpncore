#!/bin/sh

# should be run from start-jail.sh
# 
# requires DYN_BASE variable (set in common.sh) 
# requires $NAME argument

set -o nounset
set -x

NAME=$1

src_snap=$(zfs list -o name -H -rt snapshot $DYN_BASE | tail -n 1)

if [ -z $src_snap ]; then 
	echo no snapshots available on $DYN_BASE
    exit 1
fi

zfs destroy -rf $ZFS_BASE/$NAME
zfs clone $src_snap $ZFS_BASE/$NAME
