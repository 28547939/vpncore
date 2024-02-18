#!/bin/sh

set -o nounset

# functionality used both inside and outside the jail

# TODO this doesn't work if the highest-level calling script is 
# not in our directory
. $(dirname $0)/../etc/vars.sh

# re-set this variable in case we have not inherited the environment from start-jail.sh
export LOCAL_VPN_DIR=/mnt/vpn

export HOST_ADDR="${LOCAL_NET}.1"
export VPNDNS_LOCAL_ADDR="${LOCAL_NET}.3"
export VPNDNS_ANYCAST_ADDR="${ANYCAST_NET}.3"

vpn_ifid () {
    VPN_ID=$1
    expr $VPN_ID + $VPN_ID_OFFSET
}

vpn_anycast_ifid () {
    VPN_ID=$1
    printf '1%03d' $(vpn_ifid $VPN_ID)
}

vpn_local_addr () {
    VPN_ID=$1
    echo ${LOCAL_NET}"."$(vpn_ifid $VPN_ID)
}

vpn_anycast_addr () {
    VPN_ID=$1
    echo ${ANYCAST_NET}"."$(vpn_ifid $VPN_ID)
}

vpn_name() {
    ID=$1
    echo "dynvpn${ID}"
}

