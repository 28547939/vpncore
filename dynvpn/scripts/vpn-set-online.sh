#!/bin/sh
#

set -o nounset

NAME=$1
LOCAL_ADDR=$2
BASE=$3


# each VPN jail has a "dynvpn" user
ssh -i ~/.ssh/id.dynvpn $LOCAL_ADDR \
	openvpn --daemon $NAME --route-noexec --keepalive 5 10 --up $BASE/up.sh \
		--script-security 2 --config $BASE/openvpn.conf
