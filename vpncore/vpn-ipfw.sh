#!/bin/sh

ipfw -f flush

ipfw table all destroy

# Note: In this configuration, an 'up' script is provided to the openvpn daemon using the --up flag;
# the up script should make sure routes are set up properly, and it should call this (firewall) script.
# This script then inherits the environment provided to the --up script, which should contain relevant
# variables. In this way, we ensure that values that may change dynamically (eg. addresses on the TUN 
# interface) are always kept up to date.
#
# The following variables are used in this script:
# $dev the name of the TUN interface 
# $ifconfig_local the local address assigned to the TUN interface
# $route_vpn_gateway the remote end of the TUN interface
#

set -x 

add="ipfw add"

jladdr="192.168.100.10"
epair=epair10b
vpndns=192.168.100.3

TUN=$dev

# 
ipfw table vpndns create
ipfw table vpndns add $vpndns


ipfw table intnet create

# only necessary in case there is a program that sets $jladdr as source address... normally,
# the outgoing tun interface will be automatically selected
ipfw table intnet add $jladdr

# local address of the tun interface. necessary for local access (i.e. from the jail) through the tunnel
ipfw table intnet add $ifconfig_local

# etc
ipfw table intnet add $vpnclient_a
ipfw table intnet add $vpnclient_b

intnet="$jladdr,$ifconfig_local"


ipfw nat 1 config if $TUN log deny_in
ipfw nat 2 config if $epair log \
	redirect_port udp $route_vpn_gateway:53 53

ipfw nat 4 config if $epair log 


# many programs will attempt to resolve independently of system-wide configuration, especially
# if resolution is failing; keep these entries here to prevent any of our intnet hosts from
# doing this
# The implications of failing to block such requests are not severe; the requests still go over the
# 	VPN connection, but not to the VPN provider's DNS, and are not processed by our vpndns program for
# 	blocklists, etc; but they do not bypass the VPN connection.
ipfw table dns-block create
ipfw table dns-block add 8.8.8.8
ipfw table dns-block add 1.1.1.1
ipfw table dns-block add 8.8.4.4
ipfw table dns-block add 1.0.0.1


# hosts which are able to query our VPN provider's DNS (through our NAT port forward)
ipfw table priv-dns create
ipfw table priv-dns add 192.168.3.0/24
ipfw table priv-dns add 192.168.2.0/24
ipfw table priv-dns add 192.168.100.0/24

$add 	20 	allow ip from 127.0.0.0/8 to 127.0.0.0/8 via lo0
$add    25  	deny gre from any to any in recv $TUN


$add    60      deny log ip from any to 'table(dns-block)'

# port-forward incoming DNS to VPN endpoint
$add	80	nat 2 udp from any to $jladdr 53 in recv $epair
$add	81	nat 1 udp from 'table(priv-dns)' to $route_vpn_gateway 53 out xmit $TUN
$add	82 	nat 2 udp from $route_vpn_gateway 53 to 'table(priv-dns)' out xmit $epair

$add    85      nat 4 udp from 'table(intnet)' to $vpndns 53 keep-state 
$add    86      nat 4 udp from $vpndns 53 to $jladdr in recv $epair 

# in case it is desirable to make this VPN connection available over an ssh tunnel, instead of GRE
$add    97      allow tcp from any to $jladdr 22 setup keep-state

$add    00100   nat 1 ip from any to any in via $TUN

# repeat for 
$add    120     allow ip from any to any out xmit $GRE
$add    121     allow ip from any to any in recv $GRE

$add    00200   nat 1 tcp from 'table(intnet)' to any out via $TUN 
$add    00201   nat 1 udp from 'tabie(intnet)' to any out via $TUN 
$add    00202   nat 1 icmp from 'table(intnet)' to any out via $TUN 


# optional: artificial latency to mitigate location information leaking based on latency
# - 'jitter' is also possible
# see ipfw(8)
ipfw pipe 1 config delay 50
ipfw queue 1 config pipe 1
ipfw add 650 queue 1 ip from $jladdr to not 192.168.0.0/16 out xmit $epair

$add    700     allow ip from $jladdr to any
$add    701     allow ip from any to $jladdr

$add    900     skipto 65534 ip from any to any

$add    1000    nat 1 ip from any to any out via $TUN

$add 65534 deny log ip from any to any

