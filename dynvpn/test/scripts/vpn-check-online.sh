#!/bin/sh
#

set -o nounset

LOCAL_ADDR=$1
TIMEOUT=$2
NAME=$3

# each VPN jail has a "dynvpn" user
echo ssh -i ~/.ssh/id.dynvpn $LOCAL_ADDR \
	ping -W ${TIMEOUT}000 8.8.8.8

if [ -f $(dirname $0)/../state/$NAME ]; then
    exit 0
else
    sleep $TIMEOUT
    exit 1
fi


