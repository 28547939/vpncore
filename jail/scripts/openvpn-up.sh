#!/bin/sh

set -o nounset
set -x

#echo args $@

export ifconfig_local=$1
export ifconfig_netmask=$2
export route_vpn_gateway=$3
export dev=$4
export LOCAL_VPN_DIR=$5
export NAME=$6
export LOCAL_GATEWAY=$7

if [ ! -z $route_vpn_gateway ] && [ ! -z $ifconfig_local ]; then 
    ifconfig $dev inet $ifconfig_local $route_vpn_gateway \
        netmask $ifconfig_netmask broadcast 255.255.255.255
fi


route delete 0.0.0.0/1
route delete 128.0.0.0/1

route delete default 
route add default $route_vpn_gateway
route add 192.168.0.0/16 $LOCAL_GATEWAY

sh /etc/extvpn-ipfw.sh

env > /home/openvpn/last-up-env.txt
