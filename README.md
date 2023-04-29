# vpndns
### FreeBSD jail "VPN core" architecture with "vpndns" DNS proxy

![](img/overview.png)




In this setup, clients typically route all of their non-local traffic through the VPN containers, so that the traffic
accesses the internet through the containers' external VPN connections.   
Depending on the client, configuration may reside on the client, for example in the form of a route through a client's GRE
interface; or it may be transparent to the client, with the ipsec container inserting the client's packets into a GRE interface 
that it maintains.  
It's also possible for clients to use the VPN containers only for DNS resolution.  

Clients can also access internal "WAN" services ("internal WAN container"), of which their are many common examples: 
mail servers, web servers, XMPP, file servers, media servers, virtual desktops, etc. Media servers that require multicast, such
as Kodi and Plex, aren't compatible without extra work to configure multicast over the WAN. Audio servers such as Subsonic and Navidrome
which operate over unicast are compatible.
  
The diagram below, and the notes that follow, describe the process of DNS resolution, regardless of whether clients have
any routes through the VPN containers.

  
![](img/host-detail.png)
  

1. Incoming DNS request (UDP datagram) encapsulated in GRE over IPsec (tunnel mode)
2. First the ipsec container decapsulates IPsec and fowards to the GRE interface on the VPN container (via virtual bridge).  
The VPN container routes the underlying DNS request to the vpndns container;
the source address is translated to 10.10.1.2, the VPN container's own address on the bridge, with NAT.
3. vpndns instance listening on UDP port 53 receives the DNS request, applies processing (such as blocklist lookup),
and looks up the forwarder (i.e. DNS server to service the request with) based on the source address (10.10.1.2).   
In the standard configuration, the source -> forwarder map is an identity for VPN containers.
4. Therefore, the vpndns container sends the DNS request back into the VPN container (to resolve the request using that 
VPN container's remote endpoint's DNS server)
5. NAT on the VPN container is configured with port forwarding, to redirect incoming UDP on port 53 
to the external VPN's remote endpoint on the TUN interface (which is configured at tunnel setup time), or whatever
other DNS server address has been provided by the external VPN service.  
Due to this NAT, the vpndns instance is not aware of the possibly dynamically configured address of the DNS server
used by the VPN container.  
This also allows other clients to query DNS using this container's external VPN connection by just sending their requests to the container's
address on the bridge.  
Note that the lookup table used for this NAT instance is distinct from the one used in (2).
6. The VPN software on 10.10.1.2, attached to the TUN interface, sends the DNS request through the external VPN connection,
e.g. OpenVPN
7. The response is received, decapsulated, and inserted into the TUN interface.
8. The underlying UDP DNS response is routed from the TUN interface out through NAT to the vpndns instance; 
due to the existing NAT table entry from the port forward, this response is originating from 
10.10.1.2 from the standpoint of the vpndns instance.
9. The vpndns instance examines the response to determine which client to sent it to.   
It finds that the client is 10.10.1.2, and the DNS response UDP datagram is sent to 10.10.1.2.
The VPN container's NAT table (from step (2)) translates the destination address to the original remote client.
10. The response is routed to the original remote client  
The VPN container's routing table has an entry for the remote client, through the remote end of the GRE tunnel.
11. The ipsec container encapsulates the GRE packet in the IPsec tunnel.



