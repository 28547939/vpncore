#!/bin/sh
#

set -o nounset

ANYCAST_ADDR=$1


sudo route delete $ANYCAST_ADDR/32

# 
exit $?
