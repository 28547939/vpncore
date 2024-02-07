# `dynvpn.py `
TODO markdown format

Outline of operation and state changes:

<pre>
When a site comes online, all its VPNs start out in Pending. As the site receives state from peers, for each 
VPN we either: 
- update status to Replica if we learn of an Online primary
- attempt to connect the VPN if there are no Online primaries
- if the VPN is a primary and `primary_override_online` is True, immediately attempt to connect the VPN
    without waiting for notification of any online secondaries (and disregarding any such notification).

Terminology: 
    the term "primary" or "a primary" is just used to refer to a replica which has a priority in the replica list
    which is higher than the one in question (depending on the context)

Online: 
    The VPN connection is functioning and it's currently the unique one (with this ID and anycast_addr) 
    which is in use. (Technically, there may be a brief period between updates when more than one is Online,
    before secondaries transition to Replica status)

    Online -> Replica
        If a primary comes online, we disconnect our VPN

    Online -> Failed:
        When failure is detected on a VPN connection that was Online


Replica: 
    The VPN connection is inactive; a primary is in the Online state


    Replica -> Pending:
        if all candidates above it in the replica list go to Failed and/or Offline
    
    Replica -> Offline: not possible (except when other sites mark our entire site offline)
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
        when a site comes online, sets VPNs to their initial Pending state, and learns of a primary coming online
    Pending -> Offline:
        when a primary comes online and learns that a replica is already online, the primary goes Offline
        indefinitely

        TODO alterantive future optoin:
            when a primary comes online and learns that a replica is already online, and `primary_override_online`
            is False, it will transition from its initial Pending state to Offline...
            
            Offline -> Pending:
                ...then, `primary_restart_timer` will start, after which the primary will transition to Pending and
                attempt VPN connection, taking over from the replica if successful.
        

    Online -> Pending: not possible

Failed: 
    either the VPN was online and failed, or the the attempt to establish the VPN connection failed
    we give up and kill the VPN process, and enter the Failed state.
    When other sites hear of this, the next-highest replica site for this VPN will attempt to bring it online.

    Failed -> Offline: 
        After a timeout (`failed_status_timeout`), forget our Failed status and move to Offline. This reflect the fact that
            the conditions that caused the failure may be transient.

        TODO future work: 
            optionally, after `primary_restart_timeout`, transition Offline -> Pending and try again, as above; when we come online, 
            any online replica will shut off its connection and transition to Replica state

    Failed -> Pending:
        - If there are no secondaries (according to our configuration), immediately attempt to bring the VPN back online.
        - After a certain timeout (failed_retry_timeout), if we are still Failed, 


    Failed -> Online: not possible (Failed -> Pending instead)
    Failed -> Replica: not currently possible

Offline:
    the VPN connection is "administratively" offline or entered the Failed state and was successfully replaced
    by a replica.

    A remote peer's VPN will also be marked 'Offline' when the peer's site has been marked Offline.
    In this case the state of the VPN is really unknown and pending reconnection with the peer.
</pre>
