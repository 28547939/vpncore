#!/bin/sh
#

set -o nounset

NAME=$1
LOCAL_ADDR=$2
LOCAL_VPN_DIR=$3

ssh -i ~/.ssh/id.openvpn -o StrictHostKeyChecking=off openvpn@$LOCAL_ADDR killall openvpn

PIDFILE=$LOCAL_VPN_DIR/state/openvpn-$NAME.pid 
rm -f $PIDFILE
