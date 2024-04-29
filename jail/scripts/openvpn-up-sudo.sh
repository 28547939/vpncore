#!/bin/sh

set -o nounset

sudo $LOCAL_VPN_DIR/scripts/openvpn-up.sh $ifconfig_local $ifconfig_netmask $route_vpn_gateway $dev $LOCAL_VPN_DIR $NAME \
    > $LOCAL_VPN_DIR/log/openvpn-up_stdout_$NAME.log \
    2> $LOCAL_VPN_DIR/log/openvpn-up_stderr_$NAME.log
