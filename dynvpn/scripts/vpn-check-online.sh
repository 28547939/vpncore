#!/bin/sh
#

LOCAL_ADDR=$1
TIMEOUT=$2

set -o nounset

: {$TIMEOUT:=3}

ssh -i ~/.ssh/id.openvpn openvpn@$LOCAL_ADDR \
	ping -W ${TIMEOUT}000 -c 1 8.8.8.8

# failure here also includes failure to ssh
exit $?
