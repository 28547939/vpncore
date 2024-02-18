#!/bin/sh
#

set -o nounset

ANYCAST_ADDR=$1
GATEWAY=$2

sudo route add $ANYCAST_ADDR/32 $GATEWAY

# 
exit $?
