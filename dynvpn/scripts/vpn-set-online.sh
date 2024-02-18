#!/bin/sh
#

set -o nounset

NAME=$1
LOCAL_ADDR=$2
LOCAL_VPN_DIR=$3

SSH="ssh -o StrictHostKeyChecking=off -i ~/.ssh/id.openvpn openvpn@$LOCAL_ADDR"

$SSH 	sh $LOCAL_VPN_DIR/scripts/generate-config.sh \> /home/openvpn/openvpn.conf

# each VPN jail has a "openvpn" user to run the OpenVPN daemon unprivileged
$SSH	\
	openvpn --daemon $NAME --route-noexec --keepalive 5 10 --up $LOCAL_VPN_DIR/scripts/openvpn-up.sh \
		--script-security 2 --config /home/openvpn/openvpn.conf \
		--ifconfig-noexec

