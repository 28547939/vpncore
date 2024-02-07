#!/bin/sh
#

set -o nounset

NAME=$1
LOCAL_ADDR=$2


# each VPN jail has a "dynvpn" user
ssh -i ~/.ssh/id.dynvpn $LOCAL_ADDR \
	ping -c 2 -W 3 8.8.8.8

# failure here also includes failure to ssh
exit $?
