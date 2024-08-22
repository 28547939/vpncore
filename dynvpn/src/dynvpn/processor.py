

import asyncio
import logging
import traceback
from collections import deque


from dynvpn.common import vpn_status_t, site_status_t, vpn_t, site_t, str_to_vpn_status_t, \
    replica_mode_t

"""
when we have a method which accepts a stream of incoming events, it may sometimes be useful 
to temporarily pause processing and queue incoming events until processing is resumed.

this "processor" class does this, passing de-queued items to the given handler
currently this is used for handle_peer_vpn_status, where we disable processing at startup
"""
class processor():

    # singleton, where we ignore args/kwargs (which are eventually passed to __init__)
    def __new__(cls, *args, **kwargs):
        if not hasattr(cls, "instance"):
            cls.instance=super(processor, cls).__new__(cls)
        return cls.instance

    def __init__(self, node):
        self.pending_items=asyncio.Event()
        # argument lists
        self.items=deque()
        self.active=False
        self.discard=False
        self.logger=node._logger
        self.node=node

    async def handler(self):
        raise NotImplementedError

    async def start(self):
        while True:
            while len(self.items) > 0:
                if not self.active:
                    break

                try:
                    (args, kwargs)=self.items.pop()
                    await self.handler(*args, **kwargs)
                except Exception as e:
                    self.logger.warning(f'processor caught exception: {e}')
                    print(traceback.format_exc())

            
            self.pending_items.clear()
            await self.pending_items.wait()
        
    def add(self, *args, **kwargs):
        if self.discard is False:
            self.items.append( (args, kwargs) )

            if self.active:
                self.pending_items.set()

    def set_discard(self, tf):
        self.discard=tf
    
    def activate(self):
        self.active=True
        self.logger.debug('processor %s activated' % type(self))
        if len(self.items) > 0:
            self.pending_items.set()
    
    def deactivate(self):
        self.active=False


class peer_vpn_status_first(processor):
    """
    handle items that were received by handle_peer_state
    """
    async def handler(self, site_id : str, vname : str, status : vpn_status_t):


        if vname not in self.node.sites[site_id].vpn:
            self.logger.warning(f'peer_vpn_status_first: vpn {vname} not configured for site {site_id}')
            return

        remote_vpn=self.node.sites[site_id].vpn[vname]
        previous_status=remote_vpn.status

        remote_vpn.status=status

        #self.logger.debug(f'peer_vpn_status_first({vname}@{site_id}): {status}')


        # no state change is needed if this status is already recorded
        if status == previous_status:
            return

        self.logger.info(f'peer_vpn_status_first({vname}@{site_id}): {previous_status} -> {status}')

        peer_vpn_status_second.instance.add(site_id, vname, status, previous_status)


class peer_vpn_status_second(processor):
    async def handler(self, site_id : str, vname : str, status : vpn_status_t, previous_status : vpn_status_t):
        vs=vpn_status_t
        if vname in self.node.replica_priority:
            rp=self.node.replica_priority[vname]
        else:
            rp=None

        # if a VPN is unavailable, check the replica list to see if we need to take any
        # action (failover)
        match (previous_status, status):
            case (vs.Online, vs.Failed) | (vs.Pending, vs.Failed) | (_, vs.Offline):

                if rp is None:
                    self.logger.info(f'peer_vpn_status_second({vname}@{site_id}): peer status Offline: VPN not present in replica_priority, discarding this update')
                    return

                # come online if the offline self.node is directly above us, including when the the self.node is last
                # in the list and we are first
                if self.node.site_id in rp:

                    # TODO overall design for replica distance and replica priority to be updated based on 
                    # observed behavior

                    # among other things, _replica_distance will check that we are in Replica state
                    rd=self.node._replica_distance(site_id, self.node.site_id, vname)
                    self.logger.info(f'peer_vpn_status_second({vname}@{site_id}): peer status Offline: rd={rd}')

                    (d, rp)=rd
                        
                    #condition=\
                    #    d == 1 or \
                    #    (self.node.replica_priority[vname][0] == self.node.site_id and self.node.replica_priority[vname][-1] == site_id)

                    if self.node._local_vpn_obj(vname).status == vpn_status_t.Replica:
                        if d == 1 or len(rp) == 0:
                            await self.node.vpn_online(vname)
                            return
                else:
                    self.logger.info(f'peer_vpn_status_second({vname}@{site_id}): peer status Offline: local site not configured as Replica (skipping) (rp={rp})')

            case (_, vs.Online):

                #d=self.node._replica_distance(site_id, self.node.site_id, vname)
                #self.logger.info(f'peer_vpn_status_second({vname}@{site_id}): peer status Online: replica distance is {d}')

                #if d is None:
                #    self.logger.info(f'peer_vpn_status_second({vname}@{site_id}): replica_priority={self.node.replica_priority[vname]}')
                #    return

                # Pending -> Replica (or Offline)
                # Online -> Replica (or Offline)
                if (vpn := self.node.get_local_vpn(vname)) is not None and vpn.status in \
                    [ vs.Pending, vs.Online ]:

                    if self.node.replica_mode != replica_mode_t.Disabled:
                        # currently, transition to Replica regardless of whether we are higher in the replica list
                        # this enables us to also manually bring a VPN to the Online state elsewhere

                        await self.node.vpn_offline(vname, True, vs.Replica)
                    else:
                        await self.node.vpn_offline(vname, True, vs.Offline)
                else:
                    return


            # currently there is no need to handle these
            case (_, vs.Replica):
                pass

            case (_, vs.Pending):
                pass

            # illegal transitions
            case (vs.Replica, vs.Failed) | (vs.Offline, vs.Failed):
                self.logger.warning(f'peer_vpn_status_second({vname}@{site_id}): illegal transition or missed a transition')

            case _:
                raise ValueError()

