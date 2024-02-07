#!/bin/sh
#

set -o nounset

ANYCAST_ADDR=$1


echo sudo route delete $ANYCAST_ADDR/32

# 
exit 0
