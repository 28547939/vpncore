#!/bin/sh
#

set -o nounset

NAME=$1
LOCAL_ADDR=$2
LOCAL_VPN_DIR=$3

ssh="ssh -i ~/.ssh/id.openvpn -o StrictHostKeyChecking=off openvpn@$LOCAL_ADDR"

$ssh killall openvpn

PIDFILE=$LOCAL_VPN_DIR/state/openvpn-$NAME.pid 
$ssh wait $(cat $PIDFILE)

rm -f $PIDFILE
