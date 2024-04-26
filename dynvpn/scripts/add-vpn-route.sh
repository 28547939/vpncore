#!/bin/sh
#

set -o nounset

ANYCAST_ADDR=$1
GATEWAY=$2

sudo /sbin/route -n delete $ANYCAST_ADDR/32 2>/dev/null >/dev/null
sudo /sbin/route -n add $ANYCAST_ADDR/32 $GATEWAY

# 
exit $?
