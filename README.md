# vpncore
## IPsec WAN "VPN concentrator" (FreeBSD)


Major components of this repository:

* This document
* Policy-based forwarding DNS server supporting blocklists and authoritative 
`A` and `PTR` records (`vpndns`), and caching based on TTL, with HTTP interface	
* VPN connection/session failover system to monitor connection availability and update
BGP anycast routes appropriately (`dynvpn.py`)
* Various FreeBSD jail-related scripts which are used to 
implement the design described in this document



The purpose of this repository is to provide the tools and information 
needed to implement this design.  

"VPN containers" provide
clients with routes out to the Internet, and other containers
provide various local services.

The repository, aside from this document, is partly specific to FreeBSD, but this
design can also be supported on Linux without issue.

#### Preliminary discussion: VPN containers

Clients are
configured statically with default routes through GRE, or their traffic is directed by policy route 
through a GRE tunnel on a router somewhere the default route, to access default routes provided
by the remote tunnel endpoints (residing on "VPN containers").

GRE tunnel endpoints (inside "VPN containers") on remote hosts are accessed over the WAN, 
and the endpoint addresses can be
anycast to take advantage of routing failover among multiple instances when using BGP to advertise the addresses
(but see the discussion below under `dynvpn.py`).
Endpoint containers ("VPN containers") use OpenVPN or Wireguard
to connect the clients to the Internet, and the containers use NAT to decouple client resolver configuration
from the dynamic resolver config obtained from the VPN provider.

The `vpndns` DNS server (Perl) primarily forwards requests, acting in tandem with the VPN containers' NAT to ensure
client requests are directed through the appropriate VPN session; it can also be used indepedently to
provide DNS resolution for clients which do not use any VPN container.

`vpndns` supports DNS blocklists intended to handle large lists that are typical with 
ad blocking and similar.
Certain parts of the configuration can be modified over HTTP API, such as blocklist exceptions.
It also provides very basic authoritative DNS server functionality in case it doesn't make sense to run a
proper DNS server.

Throughout this document, the term `VPN` is intended to refer to a connection through which
Internet access is made available as a commercially available service.

### Overview

![](readme-img/overview.png)

At a high level, the components of the system are the following:

* Hosts: physical or virtual servers connected to the Internet, connected
via site-to-site tunnels to form an IP overlay network/IPsec WAN, with each host
having an internal virtual network, typically a `/24`. A virtual bridge
(`bridge` interface) is given an address on the virtual network.
In our case, where IPv4 is used, containers access the Internet over the host's 
NAT.
* Containers, each having its own IP address on a host's internal virtual network; 
each container has its own virtual network stack
(using a Linux network namespace or FreeBSD VNET setting), as if it were a 
separate machine on the LAN represented by the host's virtual network.
* Clients, which can each either be a container on some host, or an Internet-connected
device such as laptop or mobile phone capable of dialing into the network directly
(via IPsec or otherwise).
  * Clients are just those things which, roughly, have IP connectivity with the WAN
and act as clients to a host's containers in some way, whether a VPN container
or otherwise.

Containers can be categorized as follows:


1. **IPsec container**: each host has a unique IPsec container which connects the
host's internal network to other hosts via site-to-site IPsec tunnel(s).
   * The IPsec container, with its own virtual network stack like any other container,
acts as a router for the host and its containers: packets to and from remote
sites pass through the container in both directions.
   * If VPN failover/high availability is desired between hosts, a `dynvpn.py` 
instance on each host's IPsec container manages the election of a primary
for each VPN instance and communicates anycast routing changes to the local
BGP daemon via static route.

2. **VPN containers**: providing Internet connectivity to clients via an external
VPN connection, such as OpenVPN or Wireguard. Generally, client traffic
is directed into the VPN container via GRE route or SOCKS connection.
   * **`vpndns` container**: each host as a unique `vpndns` container, 
running a `vpndns` instance, which forwards DNS
requests (originating from that host or from elsewhere) to VPN containers on 
that host, based on policy.
It also supports other functionality - see below.
An implementation is included in this repository.
3. **Internal/WAN services**: services which are not necessarily connected to the Internet but
which are accessible across the WAN (labeled "Internal WAN container" above).
4. Other containers: whatever other containers the host might have.



The first three container types are discussed in the following sections.


### I. IPsec containers

#### Overview

The IPsec container links a host's internal network to the
internal networks of the other hosts, acting as a router in tandem with the
host's `bridge` interface.
It has an IP 
address on the host's network like any other container, and it's not actually
directly connected to the Internet - it uses the host's NAT
(forwarding UDP ports `500` and `4500`).

In other words, 
IPsec processing takes place inside the container, and the site-to-site tunnel
traverses the host's NAT (generally using ESP-in-UDP), with the tunnel
endpoints being inside the IPsec container. So while the IPsec
container is what connects the hosts together into a WAN, the endpoints are
technically located within the host's network, behind the host's NAT.

The recommended configuration is a route-based configuration using StrongSwan.
In a route-based configuration, the operating system represents 
the ingress and egress of tunneled packets as taking place inside virtual 
interfaces (`ipsecN`, for some number `N`). Traffic is directed into the tunnel
using the routing table (in this case the IPsec container's routing
table). For example, a remote site's `/24` would be routed through the `ipsec`
tunnel interface.

The alternative approach is the "policy-based" configuration, where IPsec
traffic selector policies are installed (by StrongSwan), without the 
presence of `ipsec` interfaces.

Advantages of the route-based approach:

* Routing policy specified by routing table instead of IPsec traffic-selector
policy. 
 * Familiar tools, such as standard routing protocols, can be used to 
establish the forwarding between sites rather than relying on the less
accessible traffic selector policy configuration.
* MTU configuration
 * It's not always possible to establish MTU when using a policy-based
configuration, but this is easy to specify on an `ipsec` interface when
using a route-based configuration.
* `tcpdump`/BPF accessibility
 * Examining traffic with `tcpdump` is easier when the traffic is presented
unencrypted on an interface.


Disadvantages

* When using a route-based configuration, incoming policy is no longer enforced 
by the traffic selector. This needs to be accomplished in the firewall instead.



See [https://docs.strongswan.org/docs/5.9/features/routeBasedVpn.html](https://docs.strongswan.org/docs/5.9/features/routeBasedVpn.html) for 
more information.

Some example configuration material is provided for StrongSwan: see
`vpncore/swanctl.conf`. 

#### Configuration

The relevant configuration is essentially the following:

1. StrongSwan configuration (see their documentation, and example config
provided)
2. Interface configuration: `rc.conf.local` (FreeBSD) or NetworkManager, etc 
(Linux), to create necessary GRE and IPsec tunnel interfaces.
3. Routing: in this design, FRR adds local routes for other hosts on the
WAN automatically using BGP. 
Routes that need to be accessible to other hosts
on the WAN should either be configured statically (e.g. in `rc.conf.local`) or
specified in the FRRouting configuration. A sample configuration is provided in
`misc/bgpd.conf`.



IPsec tunnel interface configuration:

```
# ifconfig ipsec1 create
# ifconfig ipsec1 tunnel 192.168.2.4 1.2.3.4 reqid 1
# ifconfig ipsec1 inet 192.168.2.4 192.168.1.4 netmask 255.255.255.255 
# ifconfig ipsec1
ipsec1: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> metric 0 mtu 1400
        tunnel inet 192.168.2.4 --> 1.2.3.4
        inet 192.168.2.4 --> 192.168.1.4 netmask 0xffffffff
        groups: ipsec
        reqid: 1
        nd6 options=29<PERFORMNUD,IFDISABLED,AUTO_LINKLOCAL>
```
`1.2.3.4` is the public address of the host that contains the `192.168.1.0/24`
network. Our outer tunnel endpoint address is still `192.168.2.4` because the 
packets will be processed by NAT when they leave the local host.

It's worth noting that the netmask needs to be `255.255.255.255`. This prevents
the remote `/24` from being directly connected in our routing table; if it is,
then failure of the IPsec tunnel will make that entire remote  `/24` unavailable.
With the netmask correct, the remote `/24` (except for `192.168.1.4`) will be 
accessed through another 
path on the WAN thanks to BGP in case the link fails.

The `reqid` parameter can be arbitrary and just needs to be equal to the 
parameter in the StrongSwan configuration (see example `swanctl.conf`).

### II. VPN containers


#### Client access options

Different types of clients will have different capabilities as far as setting
their default route through the VPN container. Options include the following:

* Policy route through GRE tunnel somewhere along the path
  * Some clients don't have any specific capability to establish a tunnel. In 
this case one option is to install a policy route somewhere along the default 
route where the GRE tunnel is installed.
  * Typically this will happen relatively close
to the client, such as in the first-hop IPsec container  to ensure the 
Internet-bound traffic is forwarded over the tunnel.
  * This option is common for mobile devices, where it is more difficult
to install and configure a GRE tunnel.

* Local GRE tunnel
  * This is the same as the above, except the client has the GRE tunnel
installed locally.

* SOCKS proxy
  * This is an option for clients which can't use a tunnel (for example an
unprivileged process which can SSH but can't configure interfaces or modify
the local routing table). It's also an option when more granularity is desired,
e.g. a device accesses the Internet through one tunnel but specific applications
on that device use another tunnel over SOCKS.
  * The VPN container will need to be configured for SOCKS (for example with
an SSH server)


#### Apple devices

Based on limited experimentation, and without having done any further research,
 the following appears to be true about Apple systems.

* It appears that current generation iOS/macOS does not allow users to connect 
to VPNs unless those VPNs are registered with/approved by Apple. This appears 
to rule
out using newer Apple devices with the self-hosted setup configuration
described here.
* iOS releases from
approximately 10 or more years ago, and macOS/Mac OS X up until the current
generation, support IPsec VPN connections with certain additional requirements
and limitations which are generally documented by Apple or made clear in
the user interface.
* iOS releases newer than the cohort discussed above (approx. 10+ years old),
but not in the cohort of current/recent generation releases, cannot connect to IPsec 
VPNs without a `mobileconfig` configuration, and they enforce undocumented
requirements on things like certificates. 
* Unlike the StrongSwan client available on Android, there is generally
no diagnostic data available to the user, error messages are sometimes 
wrong, and rejected configurations sometimes result in a non-conforming
abort of the IKE handshake (see RFC7296 2.21.2)

For these reasons, we recommend against using Apple devices as clients for
self-hosted VPNs.


#### Windows

We have not done any testing with Windows systems. According to StrongSwan
documentation, Windows systems are generally compatible.


#### Container firewall and policy with `ipfw`

An example `ipfw` configuration, for VPN containers, is provided in this repository. 
It incorporates the features that are discussed in this document.





### RE: `dynvpn.py` and high availability for I and II (containers and hosts)


#### High availability: WAN connectivity - routing with BGP

As discussed above, in this route-based IPsec design, the IPsec containers
act as virtual routers, routing packets between the hosts (across the WAN) 
as determined by their (IPsec containers') routing tables.

BGP can be used to install appropriate routes automatically. 
IPsec containers on each host are directly connected over IPsec tunnel, and 
BGP sessions are established over the IPsec tunnel's single hop. 
`FRRouting` is one option for 
a BGP server, and we provide a sample configuration in `misc/bgpd.conf`.

Unless the WAN topology is a full mesh, packets from client to container will 
often be traversing multiple hosts (i.e. multiple IPsec containers and their
associated tunnels). If a host or a link between hosts becomes unavailable, BGP 
instances running in the IPsec containers will change routes automatically
(after a small delay) to direct traffic across available routes. 

This failover mechanism does not pertain specifically to VPN containers 
(which is covered next), but to the hosts and their internal network all 
together.



#### High availability: container availability - anycast

Individual containers can become unavailable on the network for a variety of reasons.
Regardless of the applications running in the container, 
various general conditions result in non-availability: the container actually
being stopped/shut down, the container's `epair` (or `veth` on Linux) interface
failing or being removed, a firewall problem on the host's internal network, etc.

In addition to those conditions, in the case of VPN containers, we consider
non-availability of the container to also hold in the case of
failure/non-availability of the VPN connection/session.

A combination of anycast and BGP can be used to implement high availability 
for containers: all replicas of the container have the same (anycast) address,
and that address is advertised over BGP. If all such addresses are advertised
at once, the resulting setup has a load balancing effect where clients are 
directed to the container with the shortest path. But this is not appropriate 
if containers maintain per-client state (unless that state is synchronized
across replicas as needed), unless it can be guaranteed that shortest
paths will not change except as a result of container failure.

In the case of VPN containers, the containers do maintain a kind of state
for clients, which essentially comes down to the OpenVPN or Wireguard association
with the remote end of the VPN session (and ultimately, the public IP address
assigned by the service provider along with any flow-related state
along the path). As a result, packets within each flow need to travel through
the same VPN container. This, along with the fact that service providers 
generally limit the number of active VPN sessions, means that
it's best to implement high availability with only one active VPN container
at a time, with a replica coming online if the primary fails.

This is implemented in `dynvpn.py`. Each VPN container can be activated on
any host in the WAN, and ordering of primary/replica hosts is statically 
configured. The program checks for connectivity in the VPN containers that 
it's monitoring, and notifies peers of state changes, in addition to monitoring
peers with a heartbeat. Only the active VPN container's anycast address is
advertised on BGP, and this is changed appropriately when failover takes place;
that is, when the container on one host fails and the corresponding container on
another host is automatically activated, the BGP route is automatically 
advertised on the new host instead of the old.

Regarding routes, what we do specifically is set FRR to redistribute local 
static routes (but not directly connected ones), and set a static route
for the anycast address with next hop equal to the IPsec container's
default gateway (which should be the host's virtual bridge). In this 
configuration, the anycast addresses are on a distinct subnet from the
host's `/24`, so the host has two virtual bridges, and conceptually, the
main virtual bridge routes to the anycast bridge once the packet exits
the IPsec container. When `dynvpn.py` installs the local static route
for the anycast address, FRR advertises it over BGP, making the VPN accessible
to clients.

This failover will inevitably mean that existing state, such as TCP connections,
will need to be re-established, but this is unavoidable without the VPN service
provider migrating the VPN session state over to the newly activated 
session.



### III. `vpndns` container:  DNS resolution for VPN containers  
  
The diagram below, and the notes that follow, describe in detail the process of 
DNS resolution as performed by the `vpndns` server. This service can be used 
by any client, but it's mainly intended to automatically decouple the resolver
configuration of VPN container clients from that VPN container's dynamic
resolver configuration.

VPN container clients statically configure the address of the `vpndns` server as
their resolver. Typically, clients route their access to the server through
their route to the VPN container, so that the VPN container itself is automatically 
chosen by the server to handle the request (because of NAT to the container's 
jail address; see details below).

Clients which are not interacting with VPN containers can still use the `vpndns`
server for resolution, in which case the server can configure the correct 
VPN container to forward requests to.

  
![](readme-img/host-detail.png)
  

1. Incoming DNS request (UDP) encapsulated in GRE over IPsec (tunnel mode)
2. First the ipsec container decapsulates IPsec and forwards to the GRE interface on the VPN container (via virtual bridge).  
The VPN container routes the underlying DNS request to the `vpndns` container;
the source address is translated to 10.10.1.2, the VPN container's own address on the bridge, with NAT.
3. `vpndns` instance listening on UDP port 53 receives the DNS request, applies processing (such as blocklist lookup),
and looks up the DNS server to service the request with based on the source address (10.10.1.2).   
In the standard configuration, the source -> server map is an identity for VPN containers.
4. Therefore, the `vpndns` container sends the DNS request back into the VPN container (to resolve the request using that 
VPN container's remote endpoint's DNS server)
5. NAT on the VPN container is configured with port forwarding, to redirect incoming UDP on port 53 
to the external VPN's remote endpoint address on the TUN interface (which is configured dynamically whenever the VPN session
is (re-)initiated), or whatever
other DNS server address has been provided by the external VPN service.  
Due to this NAT, the `vpndns` instance is not aware of the possibly dynamically configured address of the DNS server
used by the VPN container.  
This also allows other clients to query DNS using this container's external VPN connection by just sending their requests to the container's
address on the bridge.  
Note that the lookup table used for this NAT instance is distinct from the one used in (2).
6. The VPN software (such as OpenVPN) on 10.10.1.2, attached to the TUN interface, sends the DNS request through the external VPN connection,
7. The response is received, decapsulated, and inserted into the TUN interface 
by the VPN software (such as OpenVPN)
8. The underlying, decapsulated UDP DNS response is routed from the TUN interface out through NAT to the `vpndns` instance; 
due to the existing NAT table entry from the port forward, this response is originating from 
10.10.1.2 from the standpoint of the `vpndns` instance.
9. The `vpndns` instance examines the response to determine which client to sent it to.   
It finds that the client is 10.10.1.2, and the DNS response UDP datagram is sent to 10.10.1.2.
The VPN container's NAT table (from step (2)) translates the destination address to the original remote client.
10. The response is routed to the original remote client; the VPN container's routing table has an entry for the remote client, through the remote end of the GRE tunnel.
11. The ipsec container encapsulates the GRE packet in the IPsec tunnel.




#### HTTP API examples

Currently a few different commands are available to make it possible to modify
configuration and state without restarting the program.

```
# curl -X POST 127.0.0.1:8080/static_records/reload
{
   "message" : "completed",
   "data" : {
      "error" : [],
      "total" : 10,
      "successful" : 10
   },
   "is_error" : 0
}
# curl -X POST 127.0.0.1:8080/blocklist/add_exception/analytics.google.com
{
   "data" : {
      "added" : "analytics.google.com"
   },
   "message" : "success",
   "is_error" : 0
}
# curl -X GET 127.0.0.1:8080/blocklist/list_exceptions  # note - GET method
{
   "message" : "success",
   "data" : {
      "exceptions" : [
         "analytics.google.com"
      ]
   },
   "is_error" : 0
}
# curl -X POST 127.0.0.1:8080/blocklist/del_exception/analytics.google.com
{
   "is_error" : 0,
   "data" : {
      "deleted" : "analytics.google.com"
   },
   "message" : "success"
}

```

See `vpndns/sample.yml` for full list of methods.




#### Dependencies (Perl)

Currently the program is written in Perl. It has the following dependencies.
In the future, it may be rewritten in another language. Dependencies can be
installed with `cpan`. The author prefers to build Perl programs into 
executables using the "PAR Packer" ([https://metacpan.org/pod/pp](https://metacpan.org/pod/pp))
in a separate sandboxed environment that has needed CPAN modules installed, 
followed by deployment.

* `POE` (including `POE::Kernel`, `POE::Session`, etc, which should be among
its dependencies)
* `YAML::XS` (we should probably change this to `YAML::Any`)



### IV. Internal/WAN services

The WAN makes it easy to make a number of services accessible to 
clients from anywhere. One advantage is that, setting aside security concerns 
that arise from within the WAN itself, services that are accessible on the WAN
generally do not need the level of security as they would if they were 
Internet-facing. Essentially, as far as the WAN services are concerned, 
all Internet-facing 
security considerations are taken care of at once by IPsec.

But the reality is inevitably more complex when multiple users are involved or when
certain services are more trusted than others, especially when it comes to services
which access the Internet. Threats can also arise from within the WAN.
Firewalls/ACLs on the host can go a long way in this 
context.

Examples of internal/WAN services include:

* SMTP/IMAP servers: multi-user communication, system monitoring/alerting/logging,
general remote storage of small documents, e.g. sharing URL from a mobile phone
web browser to consult later
* XMPP: secure communication; no need to back up chat transcripts when the instance
already stores them locally
* Virtual desktops: containers (or VMs) running a VNC server; this benefits from 
local access to the host's storage and services, and the ability to maintain 
long-running desktop state (e.g. open windows/work flow), so long as the host is 
running.
  * Virtual desktops are an example of a container which would benefit from using a
VPN container for its Internet connection, typically via GRE. "Blank-slate" 
Internet-enabled virtual desktops are an ongoing area of work already for
privacy, convenience, and productivity reasons, e.g. Kasmweb and others.
* Web servers, file servers (SMB, sshfs, user-space NFS)
* Media servers: such as Subsonic-compatible Java-based music servers; other popular
options Kodi and Plex require additional configuration on the hosts to support
multicast routing
* Podcast/RSS ingestion and local redistribution/aggregation (e.g. gPodder CLI, TT-RSS)
* HDHomeRun and similar: export local radio-based TV over the WAN to remote clients (also requires
multicast to be configured)


#### Case study: jailed virtual desktops with VNC

Multi-user graphical desktop environments can be run in FreeBSD jails and 
configured for access over VNC with minimal setup. Commercial options for this
kind of setup do also exist, e.g. Kasmweb. Based on the author's experimentation
this setup appears to be simpler on FreeBSD than on Linux, at least on Linux
systems that use `systemd`.

Policy routes (`ipfw fwd` rules) can provide each user with a different default
route (specifically, forwarding policies through different GRE tunnels)

Each user can run their own desktop environment, executed by the VNC server.

TODO: More details, config

1. `vnet`-enabled jail with an `epair` interface
2. Install and configure VNC (`net/tigervnc-server`)
3. Install GRE tunnels and policy routes (`ipfw fwd`)
4. Install "dummy" `bridge` interface and set default route through it
   * Since this is a local interface and it's the default route, outgoing packets 
will use it as its source address, simplifying routing and configuration.
5. Add route(s) and modify ACLs in VPN container for jail desktop


### Container setup: FreeBSD

Scripts and other configuration materials are available in the `jail` directory.
This is not meant to be a complete, self contained system, but it provides 
most of what is needed to start and operate the VPN jails using clones given 
an existing base jail. This work is basically finished but is currently a work 
in progress, pending an unresolved FreeBSD issue involving tunnel creation/destruction.
