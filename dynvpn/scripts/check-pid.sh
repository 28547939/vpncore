#!/bin/sh
#

set -o nounset
set -x

export NAME=$1
export LOCAL_ADDR=$2
export LOCAL_VPN_DIR=$3

SSH="ssh -i ~/.ssh/id.openvpn \
        -o ConnectTimeout=5 \
        -o StrictHostKeyChecking=off \
        openvpn@$LOCAL_ADDR"

PIDFILE=$LOCAL_VPN_DIR/pid/openvpn-$NAME.pid 
$SSH test -f $PIDFILE
if [ $? ]; then
    pid=$($SSH cat $PIDFILE)
    $SSH kill -0 $pid 2>/dev/null
    if [ $? ]; then
        echo $pid
        exit 0
    else
        exit 1
    fi
fi

exit 1
