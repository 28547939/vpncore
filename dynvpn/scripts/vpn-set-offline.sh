#!/bin/sh
#

set -o nounset

LOCAL_ADDR=$1

ssh -i ~/.ssh/id.openvpn -o StrictHostKeyChecking=off openvpn@$LOCAL_ADDR killall openvpn
