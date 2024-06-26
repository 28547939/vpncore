#!/bin/sh
#

set -o nounset

LOCAL_ADDR=$1
NAME=$2


# each VPN jail has a "dynvpn" user
echo ssh -i ~/.ssh/id.dynvpn $LOCAL_ADDR \
	ping -c 2 -W 3 8.8.8.8

if [ -f $(dirname $0)/../state/$NAME ]; then
    exit 0
else
    exit 1
fi


