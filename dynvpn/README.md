# `dynvpn.py `
TODO markdown format

Currently, `dynvpn` requires Python 3.11 or newer.

### Overview

The `dynvpn` program is designed to run inside the `ipsec` jail, as described in the main `README.md` in this
repository. It uses shell scripts (in `scripts/`) to access and manipulate local VPN containers over SSH.

Terminology: 
* The word "`host`" is used similarly to the main repository README, to refer to the jail host which 
contains VPN jails and other jails. In the context of `dynvpn` we mostly refer to a "`site`", which is
a representation of a host and its VPN connections that exists inside the `dynvpn` program.
* Each "host" or "site" runs a single "`instance`" of the `dynvpn` program, also referred to as a "`node`".
The node can just be thought of as the actual operational object, with the nodes on different hosts all 
interacting as peers.
* Each site contains a number of VPN connections, and in general, a VPN connection is present
on multiple sites. The idea is that a failed or offline VPN connection on one site can be 
brought online on another site for high availability.
* Each VPN connection on a site has a "`status`" (or "`state`").
Currently these are `Online`, `Replica`, `Pending`, `Offline`, and `Failed`. These are described
in more detail below.

`dynvpn` instances periodically pull each peer's state, which includes the statuses of the peer's
VPNs. When a VPN's status changes on a particular node, the node will advertise this change to peers, except
during startup, which is designed to reduce noise and unnecessary churn.

Configuration consists of a "global" config file, assumed to be synchronized between hosts by some existing
system, and a "local" config file.
VPN connections are uniquely identified by a unique positive numeric ID, and are also referred to with
string names of the form `dynvpnN`, where `N` is some numeric ID. On FreeBSD, the VPN jails are named
using this string form.

### Fault tolerance and replicas 

Part of the global configuration is the `replica_priority` key, which describes, for each VPN connection, how failover 
should occur between sites. We make the assumption that all hosts are more or less equally well-equipped to run any given 
VPN session, so the ordering in the replica list can be arbitrary: it's purpose is just to stipulate unambiguously
to each site the order in which failover should take place between sites.

What specifically happens is that, when a site's VPN fails, peers which learn of this consult the priority list for that 
VPN to check whether they are next on the list. Only the next site on the list (wrapping to the beginning if necessary)
attempts to come online to fail over. In other words, peers use the priority list to elect the next site to
(attempt to) bring the VPN connection online. 

Only sites which have marked their copy of the VPN as being in `Replica` state are considered when traversing the
list. Therefore, participation in high availability is optional, on a per-VPN basis: if a VPN isn't Online or Failed,
it can be Offline, in which case it's safe from any incoming events and won't (automatically) leave that state. 

One consequence of this design is that if failover occurs, the instance will not attempt to bring the VPN session 
back online so long as another replica is online, even if that replica is "lower" in the list.

#### Replica mode

Ideally, opting into the failover functionality should not be all-or-nothing, but instead there should be some granularity
as far as how much of it is used. This is also desirable when gradually adding new VPNs to a site so as to not trigger 
any chaotic or unexpected behavior in the network as its scale grows, especially when the design is still being tested and
refined.

As mentioned above, a node's VPN only participates in failover if the VPN is in the Replica state. Nodes have a `replica_mode`
which helps to constrain how and when its VPNs can be in this state:
* `Auto`: A VPN will generally automatically enter Replica state whenever it's not Online, unless it's Failed or 
unless it was manually set to Offline by an administrator. For example, during startup, VPNs which don't come Online
(for example due to an existing site which has it Online) will end up in Replica.
* `Disabled`: In this mode it's not possible for a VPN to enter Replica status ever. This means that this node's 
VPNs will never participate in failover and they will need to be controlled (set online/offline) manually by the 
administrator.
* `Manual`: VPNs can enter the Replica state, but not automatically. Replicas need to be assigned manually by the
administrator. For example, during startup, if a VPN does not attempt to come Online, it will revert to Offline
rather than Replica. This is mode intended to be a mid-way point between Auto and Disabled.

In a sense, since Online/Offline state can always be controlled from the HTTP API, `Disabled` and `Manual` also just
provide the opportunity for some other system to control the VPNs' failover rather than the built-in mechanisms here.

### Other notes

Startup: when an instance comes online, all its VPNs start out in Pending. After a waiting period during which it learns
of peer instances, it brings VPNs online for which it is first in the replica list among online instances/sites,
unless that VPN is already online elsewhere. 
  * This accommodates both the scenario of all/most instances coming online simultaneously, and the scenario where
 an instance comes online to join an existing network of instances that are already online.
  * This can result in multiple replicas coming online if there is a partition. But if the hosts are well-connected
 (such as in a mesh topology), a partition is rare.


### State changes

TODO: rewrite this section based on recent changes

<pre>


# TODO future option
# - if the VPN is a primary and `primary_override_online` is True, immediately attempt to connect the VPN
#    without waiting for notification of any online secondaries (and disregarding any such notification).

The following summarizes the conditions necessary for entering each of the following states, and the 
conditions for transitioning between states.


Online: 
    The VPN connection is functioning and it's currently the unique connected one with its name/ID.
        Technically, there may be a brief period between updates when more than one is Online.

    Online -> Replica
        If another  comes online, we disconnect our VPN
        (Currently this should not happen - this would be a possibility if `primary_override_online` is implemented)

    Online -> Failed:
        When failure is detected on a VPN connection that was Online


Replica: 
    The VPN connection is inactive; another replica is in the Online state

    Replica -> Pending:
        When the host immediately above us in the replica list (ignoring any sites which are offline) enters
        the Failed status
    
    Replica -> Offline: not possible 
        (except as seen from other sites, when other sites mark our entire site offline)

    Replica -> Online: not possible
    Replica -> Failed: not possible

Pending: 
    The status is unknown due to a site coming online or if the VPN connection is currently 
        in the process of being established 

    Pending -> Online: 
        when the VPN connection attempt is successful
    Pending -> Failed: 
        when the VPN connection attempt is not successful
    Pending -> Replica: 
        when a site comes online, sets VPNs to their initial Pending state, and learns of another instance coming online
    Pending -> Offline:
        not possible

        #TODO alternative future option:
        #    when a primary comes online and learns that a replica is already online, and `primary_override_online`
        #    is False, it will transition from its initial Pending state to Offline...
        #    
        #    Offline -> Pending:
        #        ...then, `primary_restart_timer` will start, after which the primary will transition to Pending and
        #        attempt VPN connection, taking over from the replica if successful.
        

    Online -> Pending: not possible

Failed: 
    either the VPN was Online and failed, or the the attempt to establish the VPN connection failed
    we give up and kill the VPN process, and enter the Failed state.
    When other sites hear of this, the next-highest Replica for this VPN (if any) will attempt to come Online

    Failed -> Offline: 
        After entering the Failed status, attempt to transition to Offline after `failed_status_timeout` seconds 
        have elapsed, but only if another replica is Online. 
        If not, continue to retry entering Offline every `failed_status_timeout` seconds.
        If `failed_status_timeout` is 0, remain in Failed forever.

    Failed -> Pending:
        - If no peer has any Replicas, immediately attempt to come Online again

        # TODO not yet implemented:
        #   - Optionally, after a certain timeout (failed_retry_timeout), if we are still Failed, retry bringing the VPN
        #       back online regardless of any other online replicas



    Failed -> Online: not possible (Failed -> Pending instead)
    Failed -> Replica: not currently possible

Offline:
    the VPN connection is "administratively" offline or entered the Failed state and was successfully replaced
    by a replica under certain circumstances

    A remote peer's VPN will also be marked 'Offline' when the peer's site has been marked Offline.
    In this case the state of the VPN is actually unknown and pending reconnection with the peer.
</pre>


### Testing (manual)

Since `dynvpn.py` interacts with the local system using shell scripts, it abstracts away from how those shell scripts
actually carry out their tasks, such as starting/stopping VPN processes, adding/removing routes, and checking
for connectivity. Because of this we can test `dynvpn.py` using test scripts. These scripts simulate the presence
of the VPN process using empty files on the filesystem: as long as the file exists, the VPN is considered to be online.
Failover is tested by deleting the file on one host. See the `test` sub-directory.

TODO: more realistic test environment with automated tests

See `SETUP.md` for more specific instructions

