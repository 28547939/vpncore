#!/bin/sh

# to be run within the jail

. $(dirname $0)/common.sh

VPN_ID=$1
NAME=$2

ipfw table def-epair create type iface
ipfw table def-epair add ${EPAIR}b
ipfw table def-epair add ${EPAIR_ANYCAST}b


ipfw table def-vpndns create 
ipfw table def-vpndns add "${LOCAL_NET}.3"
ipfw table def-vpndns add "${ANYCAST_NET}.3"

# addresses on our epair interfaces
ipfw table def-jladdr create
ipfw table def-jladdr add $JAIL_LOCAL_ADDR
ipfw table def-jladdr add $JAIL_ANYCAST_ADDR

ipfw table intnet create
ipfw table intnet add $JAIL_LOCAL_ADDR
ipfw table intnet add $JAIL_ANYCAST_ADDR

# hosts which can query our VPN session's DNS by sending the request
# to the jail address
# it needs to at least include the vpndns instances
ipfw table priv-dns create
ipfw table def-vpndns list | while read addr x; do
    ipfw table priv-dns add $addr
done


# some clients will attempt to resolve independently of system-wide configuration, especially
# if resolution is failing; keep these entries here to prevent any of our intnet hosts from
# doing this
# The implications of failing to block such requests are not severe; the requests still go over the  
#   VPN connection, but not to the VPN provider's DNS, and are not processed by our vpndns program for
#   blocklists, etc; but they do not bypass the VPN connection.
ipfw table dns-block create
ipfw table dns-block add 8.8.8.8
ipfw table dns-block add 8.8.4.4
ipfw table dns-block add 4.4.8.8
# etc

# GRE interfaces
ipfw table allow-gre create type iface


# could add and/or remove tables and entries in this script
# should include specific GRE interfaces
if [ -f $(dirname $0)/additional-ipfw-tables.sh ]; then
    sh $(dirname $0)/additional-ipfw-tables.sh $VPN_ID $NAME
fi
