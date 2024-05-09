#!/bin/sh

set -o nounset
set -x

# jail subsystem triggers this script after jail creation, to be run outside the jail

mkdir -p $VPN_DIR
mount -t nullfs $BASE $VPN_DIR 

# for now, store some basic state on the filesystem for scripts that execute
# within the jail that don't have our environment
# (such as ipfw rule script when executed by the openvpn process)
export STATE_DIR=$BASE/state/$NAME
mkdir -p $STATE_DIR
echo $EPAIR > $STATE_DIR/epair-local
echo $EPAIR_ANYCAST > $STATE_DIR/epair-anycast

export jexec="/usr/sbin/jexec $NAME"

ifconfig ${EPAIR}b vnet $NAME
$jexec /sbin/ifconfig ${EPAIR}b inet $JAIL_LOCAL_ADDR netmask $LOCAL_NETMASK
ifconfig ${EPAIR}a up

ifconfig ${EPAIR_ANYCAST}b vnet $NAME
$jexec /sbin/ifconfig ${EPAIR_ANYCAST}b inet $JAIL_ANYCAST_ADDR netmask $ANYCAST_NETMASK
ifconfig ${EPAIR_ANYCAST}a up

$jexec /sbin/route add default $LOCAL_GATEWAY 

$jexec sh ${LOCAL_VPN_DIR}/scripts/create-ipfw-tables.sh $VPN_ID $NAME

$jexec sh /etc/extvpn-ipfw.sh

sh $VPN_DIR/scripts/add-routes.sh $NAME $LOCAL_GATEWAY $VPN_DIR/etc/openvpn

# optionally, load GRE from JSON (see load-gre.sh.sample)
if [ -f $BASE/etc/gre/$NAME.json ] && [ -x $BASE/scripts/load-gre.sh ] ;
    $jexec $LOCAL_VPN_DIR/scripts/load-gre.sh $LOCAL_VPN_DIR/etc/gre/$NAME.json
fi

#TUN=tun${VPN_ID}
#$jexec ifconfig $TUN create

# 2024-03-27: workaround for tun allocation issue - let the system choose the ID number
TUN=$($jexec ifconfig tun create)
echo $TUN > $STATE_DIR/tun

$jexec chown root:openvpn /dev/$TUN
$jexec chmod 660 /dev/$TUN

