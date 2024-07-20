

import asyncio
import logging
import traceback
from collections import deque


from dynvpn.common import vpn_status_t, site_status_t, vpn_t, site_t, str_to_vpn_status_t

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
    async def handler(self, site_id : str, vpn_id : str, status : vpn_status_t):


        if vpn_id not in self.node.sites[site_id].vpn:
            self.logger.warning(f'peer_vpn_status_first: vpn {vpn_id} not configured for site {site_id}')
            return

        remote_vpn=self.node.sites[site_id].vpn[vpn_id]
        previous_status=remote_vpn.status

        remote_vpn.status=status

        #self.logger.debug(f'peer_vpn_status_first({vpn_id}@{site_id}): {status}')


        # no state change is needed if this status is already recorded
        if status == previous_status:
            return

        self.logger.info(f'peer_vpn_status_first({vpn_id}@{site_id}): {previous_status} -> {status}')

        peer_vpn_status_second.instance.add(site_id, vpn_id, status, previous_status)


class peer_vpn_status_second(processor):
    async def handler(self, site_id : str, vpn_id : str, status : vpn_status_t, previous_status : vpn_status_t):
        vs=vpn_status_t
        rp=self.node.replica_priority[vpn_id]

        # if a VPN is unavailable, check the replica list to see if we need to take any
        # action (failover)
        match (previous_status, status):
            case (vs.Online, vs.Failed) | (vs.Pending, vs.Failed) | (_, vs.Offline):

                # come online if the offline self.node is directly above us, including when the the self.node is last
                # in the list and we are first
                if self.node.site_id in rp:

                    # among other things, _replica_distance will check that we are in Replica state
                    rd=self.node._replica_distance(site_id, self.node.site_id, vpn_id)
                    self.logger.info(f'peer_vpn_status_second({vpn_id}@{site_id}): peer status Offline: rd={rd}')

                    (d, rp)=rd
                        
                    #condition=\
                    #    d == 1 or \
                    #    (self.node.replica_priority[vpn_id][0] == self.node.site_id and self.node.replica_priority[vpn_id][-1] == site_id)

                    # TODO  d is none when there are no other online sites

                    if d == 1 or len(rp) == 0:
                        await self.node.vpn_online(vpn_id)
                        return
                else:
                    self.logger.info(f'peer_vpn_status_second({vpn_id}@{site_id}): peer status Offline: local site not configured as Replica (skipping) (rp={rp})')

            case (_, vs.Online):

                #d=self.node._replica_distance(site_id, self.node.site_id, vpn_id)
                #self.logger.info(f'peer_vpn_status_second({vpn_id}@{site_id}): peer status Online: replica distance is {d}')

                #if d is None:
                #    self.logger.info(f'peer_vpn_status_second({vpn_id}@{site_id}): replica_priority={self.node.replica_priority[vpn_id]}')
                #    return

                # currently, transition to Replica regardless of whether we are higher in the replica list
                # this enables us to also manually bring a VPN to the Online state elsewhere

                # Pending -> Replica
                # Online -> Replica
                if self.node.get_local_vpn(vpn_id).status in \
                    [ vs.Pending, vs.Online ]:

                    await self.node.vpn_offline(vpn_id, True, vs.Replica)
                else:
                    return


            # currently there is no need to handle these
            case (_, vs.Replica):
                pass

            case (_, vs.Pending):
                pass

            # illegal transitions
            case (vs.Replica, vs.Failed):
                self.logger.warning(f'peer_vpn_status_second({vpn_id}@{site_id}): illegal transition or missed a transition')

            case _:
                raise ValueError()

