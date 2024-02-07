#!/bin/sh
#

set -o nounset

NAME=$1
LOCAL_ADDR=$2


# each VPN jail has a "dynvpn" user
ssh -i ~/.ssh/id.dynvpn $LOCAL_ADDR \
	killall openvpn
