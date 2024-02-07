#!/bin/sh
#

set -o nounset

NAME=$1
LOCAL_ADDR=$2
#BASE=$3


# each VPN jail has a "dynvpn" user
echo ssh -i ~/.ssh/id.dynvpn $LOCAL_ADDR /root/vpn/start-openvpn.sh $NAME

touch $(dirname $0)/../state/$NAME
