#!/bin/sh

set -o nounset
set -x

# jail subsystem triggers this script after jail creation, to be run outside the jail

mkdir -p $VPN_DIR
mount -t nullfs -o ro $BASE $VPN_DIR 

# for now, store some basic state on the filesystem for scripts that execute
# within the jail that don't have our environment
# (such as ipfw rule script when executed by the openvpn process)
export STATE_DIR=$BASE/state/$HOSTNAME
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

TUN=tun${VPN_ID}
$jexec ifconfig $TUN create
$jexec chown root:openvpn /dev/$TUN
$jexec chmod 660 /dev/$TUN

