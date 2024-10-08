#!/bin/sh
#

set -o nounset
set -x

export NAME=$1
export LOCAL_ADDR=$2
export LOCAL_VPN_DIR=$3
export SITE_ID=$4
export LOCAL_GATEWAY=$5


SSH="ssh    \
            -o SendEnv=NAME \
            -o SendEnv=LOCAL_VPN_DIR \
            -o StrictHostKeyChecking=off \
            -o ConnectTimeout=5 \
            -i ~/.ssh/id.openvpn openvpn@$LOCAL_ADDR"

$SSH     sh $LOCAL_VPN_DIR/scripts/generate-config.sh $NAME $LOCAL_VPN_DIR $SITE_ID \> /home/openvpn/openvpn.conf

# each VPN jail has a "openvpn" user to run the OpenVPN daemon unprivileged
$SSH    \
    openvpn --route-noexec --keepalive 5 10 --up $LOCAL_VPN_DIR/scripts/openvpn-up-sudo.sh \
        --daemon OPENVPN \
        --log $LOCAL_VPN_DIR/log/openvpn-$NAME.log \
        --setenv LOCAL_VPN_DIR $LOCAL_VPN_DIR \
        --setenv NAME $NAME \
        --setenv LOCAL_GATEWAY $LOCAL_GATEWAY \
        --script-security 2 --config /home/openvpn/openvpn.conf \
        --ifconfig-noexec \
        --writepid $LOCAL_VPN_DIR/pid/openvpn-$NAME.pid

