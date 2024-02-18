#!/bin/sh
#

set -o nounset

LOCAL_ADDR=$1

ssh -i ~/.ssh/id.openvpn openvpn@$LOCAL_ADDR \
	ping -c 2 -W 3 8.8.8.8

# failure here also includes failure to ssh
exit $?
