#!/bin/sh

set -o nounset

# - this script is triggered by the openvpn client daemon after a successful (re-)connection
# - we inherit certain environment variables from the daemon which were initially passed in by
#   dynvpn/scripts/vpn-set-online.sh 
# - we then pass those variables as arguments
# - openvpn-up.sh needs to run as root

sudo $LOCAL_VPN_DIR/scripts/openvpn-up.sh \
    $ifconfig_local $ifconfig_netmask $route_vpn_gateway $dev $LOCAL_VPN_DIR $NAME $LOCAL_GATEWAY \
    > $LOCAL_VPN_DIR/log/openvpn-up_stdout_$NAME.log \
    2> $LOCAL_VPN_DIR/log/openvpn-up_stderr_$NAME.log
