#!/bin/sh


JAIL_NAME=$(echo $JAIL_NAME | sed -Ee 's/[^a-zA-Z0-9_-]//g')
echo JAIL_NAME=$JAIL_NAME

set -x


ifconfig epair${JAIL_HOSTID}b vnet $JAIL_NAME
echo INET_ADDR $INET_ADDR
/usr/sbin/jexec $JAIL_NAME /sbin/ifconfig epair${JAIL_HOSTID}b inet $INET_ADDR/$INET_PREFIX
ifconfig epair${JAIL_HOSTID}a up
# prevent a failed route installation from aborting the jail initialization
/usr/sbin/jexec $JAIL_NAME /sbin/route add default $DEFAULTROUTE || /usr/bin/true

