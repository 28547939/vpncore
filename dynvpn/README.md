# `dynvpn.py `
TODO markdown format


### Overview


Terminology: 
* The word "host" is used similarly to the main repository README, to refer to the jail host which 
contains VPN jails and other jails. It's also used interchangeably here with "site"

Notes:
* Failover uses "priority" or "replica" lists of hosts for each VPN. When failover takes place, it is 
from one host to the next in the replica list. The replica lists are assumed to already be consistently 
synchronized among hosts by other means.
 * Generally, ignoring entries which are "skipped" due to sites being offline, only the immediate adjacency between 
 entries in the list is meaningful. Failover only takes place between adjacent entries (from one entry to the next one
 in the list) and the first and last entries in the list are considered to be adjacent, i.e., the first immediately
 follows the last.
* We make the assumption that all hosts are more or less equally well-equipped to run any given VPN session,
so the ordering in the replica list can be arbitrary. 
 * One consequence of this is that if failover occurs,
 the instance will not attempt to bring the VPN session back online so long as another replica is online, even if that 
 replica is "lower" in the list.
* Startup: when an instance comes online, all its VPNs start out in Pending. After a waiting period during which it learns
of peer instances, it brings VPN sessions online for which it is first in the replica list among online instances/sites,
unless that VPN session is already online elsewhere. 
  * This accommodates both the scenario of all/most instances coming online simultaneously, and the scenario where
 an instance comes online to join an existing network of instances that are already online.
  * This can result in multiple replicas coming online if there is a partition. But if the WAN is well-connected
 (such as in a mesh topology), this is rare.
* Currently, when a local VPN connection failure, we assume that it's a "bonafide" failure and we don't try to 
  restart it, instead immediately closing/aborting the connection and notifying peers of the failure. Future work
  can add the possibility to retry/restart before assuming failure.


### State changes

<pre>


# TODO future option
# - if the VPN is a primary and `primary_override_online` is True, immediately attempt to connect the VPN
#    without waiting for notification of any online secondaries (and disregarding any such notification).


Online: 
    The VPN connection is functioning and it's currently the unique one (with this ID and anycast_addr) 
    which is in use. (Technically, there may be a brief period between updates when more than one is Online,
    before secondaries transition to Replica status)

    Online -> Replica
        If another replica comes online, we disconnect our VPN
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
    either the VPN was online and failed, or the the attempt to establish the VPN connection failed
    we give up and kill the VPN process, and enter the Failed state.
    When other sites hear of this, the next-highest replica site for this VPN will attempt to bring it online.

    Failed -> Offline: 
        After entering the Failed status, attempt to transition to Offline after `failed_status_timeout` seconds 
        have elapsed, but only if another replica is Online. 
        If not, continue to retry entering Offline every `failed_status_timeout` seconds.
        If `failed_status_timeout` is 0, remain in Failed forever.

    Failed -> Pending:
        - If there are no available replicas (according to our configuration), immediately attempt to come online again
        - If the replica directly above us in the replica list (ignoring any offline sites) fails, we attempt
            to come online, even though we have already failed

        # TODO not yet implemented:
        #   - Optionally, after a certain timeout (failed_retry_timeout), if we are still Failed, retry bringing the VPN
        #       back online regardless of any other online replicas



    Failed -> Online: not possible (Failed -> Pending instead)
    Failed -> Replica: not currently possible

Offline:
    the VPN connection is "administratively" offline or entered the Failed state and was successfully replaced
    by a replica under certain circumstances
        Currently unimplemented

    A remote peer's VPN will also be marked 'Offline' when the peer's site has been marked Offline.
    In this case the state of the VPN is actually unknown and pending reconnection with the peer.
</pre>


### Testing

Since `dynvpn.py` interacts with the local system using shell scripts, it abstracts away from how those shell scripts
actually carry out their tasks, such as starting/stopping VPN processes, adding/removing routes, and checking
for connectivity. Because of this we can test `dynvpn.py` using a set of test scripts. These scripts simulate the presence
of the VPN process using empty files on the filesystem; as long as the file exists, the VPN is considered to be online.
Failover is tested by deleting the file on one host. See the `test` sub-directory.
