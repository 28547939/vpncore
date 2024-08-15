#!/bin/sh

ipfw -f flush

set -x 

add="ipfw add"

# for now, store some (addresses or interfaces) in ipfw tables
# later on, we could use JSON state files to pass this information
#
# it's assumed that the table contains exactly one item
tablevar () {
    T=$1
    ipfw table $T list | head -n 1 | cut -f 1 -d ' '
}

# these variables are passed to our environment by the openvpn process via the "--up" script:
# 	$dev				The tunnel interface that OpenVPN is attached to
#	$route_vpn_gateway	The far end of the tunnel (our VPN default route)
# 	$ifconfig_local		The near end of the tunnel (we NAT to this when going over the VPN)
#
# certain rules should only be set when we are being run by the openvpn process, so set this 
# variable if so
VPN_SESSION=
if [ ! -z $dev ] && [ ! -z $route_vpn_gateway ] && [ ! -z $ifconfig_local ]; then
	VPN_SESSION=1
fi

jladdr_local=$(tablevar def-jladdr-local)
jladdr_anycast=$(tablevar def-jladdr-anycast)
TUN=$dev
vpndns_local=$(tablevar def-vpndns-local)
vpndns_anycast=$(tablevar def-vpndns-anycast)

#epair_local=$(cat $LOCAL_VPN_DIR/state/$NAME/epair-local)b
#epair_anycast=$(cat $LOCAL_VPN_DIR/state/$NAME/epair-anycast)b

epair_local=$(tablevar def-epair-local)
epair_anycast=$(tablevar def-epair-anycast)

# GRE present in every VPN container
# `intnet` is defined by create-ipfw-tables.sh, and consists of anything that
# "originates" from our container, including internal clients which tunnel into
# us using GRE. it also includes our public container addresses ($jladdr_*)
ipfw table intnet add 10.15.12.0/30
ipfw table intnet add 10.10.12.0/30

if [ $VPN_SESSION ]; then
	ipfw table intnet add $ifconfig_local

    # internet-bound packets going through the VPN (tun interface)
	ipfw nat 1 config if $TUN log deny_in same_ports

    # port forward for any DNS lookups directed to us from WAN clients (not `intnet`)
	ipfw nat 2 config if $epair_local log same_ports \
		redirect_port udp $route_vpn_gateway:53 53
	ipfw nat 3 config if $epair_anycast log same_ports \
		redirect_port udp $route_vpn_gateway:53 53
fi

# we request from the vpndns server on behalf of DNS from `intnet` 
ipfw nat 4 config if $epair_local log same_ports
ipfw nat 5 config if $epair_anycast log same_ports


$add    20      allow ip from 127.0.0.0/8 to 127.0.0.0/8 via lo0

# connectivity check
if [ $VPN_SESSION ]; then 
    $add	21 		allow icmp from $ifconfig_local to 1.1.1.1,8.8.8.8 out xmit $TUN keep-state
fi

$add    25  	deny gre from any to any in recv $TUN

$add    60      deny log ip from any to 'table(dns-block)'
# disable TCP-based DNS resolution for now
$add    61      unreach protocol tcp from any to any 53 out

if [ $VPN_SESSION ]; then 
	$add	80		nat 2 udp from 'table(priv-dns)' to $jladdr_local 53 in recv $epair_local
	$add	81		nat 3 udp from 'table(priv-dns)' to $jladdr_anycast 53 in recv $epair_anycast
	$add	82		nat 1 udp from 'table(priv-dns)' to $route_vpn_gateway 53 out xmit $TUN
	$add	83 		nat 2 udp from $route_vpn_gateway 53 to 'table(priv-dns)' out xmit $epair_local
	$add	84 		nat 3 udp from $route_vpn_gateway 53 to 'table(priv-dns)' out xmit $epair_anycast
fi

$add    85      nat 4 udp from 'table(intnet)' to $vpndns_local 53 keep-state 
$add    86      nat 5 udp from 'table(intnet)' to $vpndns_anycast 53 keep-state 
$add    87      nat 4 udp from $vpndns_local 53 to $jladdr_local in recv $epair_local
$add    88      nat 5 udp from $vpndns_anycast 53 to $jladdr_anycast in recv $epair_anycast

$add    97      allow tcp from any to $jladdr_local,$jladdr_anycast 22 setup keep-state

if [ $VPN_SESSION ]; then 
	$add    00100   nat 1 ip from any to any in via $TUN
fi

$add	150	allow ip from any to any out xmit 'table(allow-gre)'
$add	151	allow ip from any to any in recv 'table(allow-gre)'

if [ $VPN_SESSION ]; then 
	$add    00200   nat 1 tcp from 'table(intnet)' to any out via $TUN 
	$add    00201   nat 1 udp from 'table(intnet)' to any out via $TUN 
	$add    00202   nat 1 icmp from 'table(intnet)' to any out via $TUN 
fi

# optional: artificial latency to mitigate location information leaking based on latency
# - 'jitter' is also possible
# see ipfw(8)
ipfw pipe 1 config delay 50
ipfw queue 1 config pipe 1
ipfw add 650 queue 1 ip from $jladdr_local to not 192.168.0.0/16,10.0.0.0/8 out xmit $epair_local
ipfw add 651 queue 1 ip from $jladdr_anycast to not 192.168.0.0/16,10.0.0.0/8 out xmit $epair_anycast

$add    700     allow ip from $jladdr_local,$jladdr_anycast to any
$add    701     allow ip from any to $jladdr_local,$jladdr_anycast

$add    900     skipto 65534 ip from any to any

$add 65534 deny log ip from any to any

