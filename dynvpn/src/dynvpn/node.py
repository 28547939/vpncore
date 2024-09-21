import sys
import os
import subprocess
import functools
import asyncio

import traceback


import json
import datetime

from typing import Optional, Dict, Tuple, List

import logging



from dynvpn.common import  \
    vpn_status_t, site_status_t, vpn_t, site_t, str_to_vpn_status_t, \
    replica_mode_t, str_to_replica_mode_t, \
    dynvpn_lock, dynvpn_exception

import dynvpn.processor as processor
from dynvpn import dynvpn_http
from dynvpn.task_manager import task_manager

def log(): 
    pass

"""
this program is useable, and structurally starting to grow from "prototype" status 
to being something more thoughtfully designed. WIP
"""


def timeout_wrap(f, throw_default=False):
    @functools.wraps(f)
    async def w(node, *args, timeout_throw=None, timeout=None, **kwargs):
        if timeout_throw is None:
            timeout_throw=throw_default

        if timeout is None:
            timeout=node.local_config['default_timeout']

        try:
            async with asyncio.timeout(timeout):
                return await f(node, *args, **kwargs)
        except TimeoutError:
            node._logger.warning(f.__name__ +f': timed out after {timeout} seconds')
            if timeout_throw is True:
                raise

    return w


class node():
    sites : Dict[str, site_t]

    # vname -> list of site_ids in descending order of replica
    replica : Dict[str, List[str]]


    def __init__(self, this_site_id : str, local_config, global_config, logger : logging.Logger):

        self.site_id = this_site_id
        self._logger=logger
        self._script_path=local_config['script_path']

        sites_config=global_config['sites']
        self.sites={}

        self.local_config = local_config
        self.global_config = global_config

        # for now, pass node object into task_manager
        # later, a custom task class which has access to relevant state
        self.task_manager=task_manager(self, self._logger)

        self.processors=dict()

        self.replica_mode=str_to_replica_mode_t(local_config['replica_mode'])

        self.http_client = dynvpn_http.client(self)
        self.http_server = dynvpn_http.server(self)

        self.task_manager.add(
            processor.peer_vpn_status_first(self).start(),
            'peer_vpn_status_first.start'
        )
        self.task_manager.add(
            processor.peer_vpn_status_second(self).start(),
            'peer_vpn_status_second.start'
        )

        for (site_id, site_config) in sites_config.items():
            self.sites[site_id]=site_t.load(self, site_id, site_config, global_config)
            self.sites[site_id].status = site_status_t.Pending

        if this_site_id not in self.sites:
            raise Exception("local site {this_site_id} not present in site config")

        self._server_addr=self.sites[this_site_id].peer_addr
        self._server_port=self.sites[this_site_id].peer_port
        self.replica_priority=global_config['replica_priority']


    async def start(self):

        self.task_manager.add(
            self._do_start(),
            'start'
        )

        await self.task_manager.run()
        

    """
    Entry point to the instance after instantiation
    """
    async def _do_start(self):
        # make our state available to other peers and listen for push_state
        await self.http_server.start()

        local_vpns=self.sites[self.site_id].vpn.keys()

        vs=vpn_status_t

        processor.peer_vpn_status_first.instance.activate()

        # prevent updates from peers (push state, pull state) from triggering any 
        # responses or other effects while we initialize
        processor.peer_vpn_status_second.instance.set_discard(True)

        # protect vpns from any operations (such as setting online, offline) while we initialize
        for vpn in self.sites[self.site_id].vpn.values():
            await vpn.lock.lock()

        def currently_online(vname):
            return list(filter(lambda s: 
                s.id != self.site_id and \
                vname in s.vpn and \
                s.vpn[vname].status == vpn_status_t.Online, 
            self.sites.values()))

        # at this point, all local VPNs' states have been initialized to Pending

        phase1_online=set()


        # first pass - check for local VPNs with existing online connections
        async def phase1(vname):

            if await self.check_local_vpn_process(vname):
                self._logger.info(f'start(): {vname}: process exists at startup, checking connectivity')
                if await self.check_local_vpn_connectivity(vname):
                    self._logger.info(f'start(): {vname}: connectivity check succeeded')
                    # already online - it may be allowed to remain online after startup
                    phase1_online.add(vname)
                else:
                    self._logger.info(f'start(): {vname}: connectivity check failed, killing stale process')
                    await self._set_local_vpn_offline(vname)


        # second pass - if existing peers don't have the VPN in Online status, allow our VPN to stay online:
        #
        # for a given vname, prioritize sites which already have it in Online status; even over the highest priority site
        # if multiple connections come online due to a partition or startup race, then one of them will end up transitioning to Replica - 
        #   doesn't matter which
        async def phase2(vname):
            #if self.get_local_vpn(vname).status != vs.Pending:
            if vname not in phase1_online:
                return
                
            phase1_online.remove(vname)

            if len(currently_online(vname)) == 0:
                self._logger.info(f'start(): {vname}: no other replicas online, maintaining Online state')
                await self.vpn_online(vname, False, timeout_throw=False, lock=False)
            else:
                resultstr=f'start(): {vname}: peer is online, taking ours offline; '
                if self.replica_mode == replica_mode_t.Auto:
                    resultstr += f'status -> Replica (replica_mode=Auto)'
                    await self._set_status(vname, vs.Replica, False)
                else:
                    resultstr += f'status -> Offline (replica_mode={self.replica_mode})'
                    await self._set_status(vname, vs.Offline, False)

                self._logger.info(resultstr)

                # another node has already reported this VPN being online - need to set ours
                # offline in case the underlying OpenVPN connection is online
                await self._set_local_vpn_offline(vname)


        # third pass to check any further VPNs detected online earlier, or others where we are first in the replica list
        async def phase3(vname):
            if vname in self.replica_priority:
                rp=self.replica_priority[vname]
            else:
                self._logger.warning(f'vpn {vname} was present in local VPN list, but not in priority list')
                rp=None

            #if not ( (rp[0] == self.site_id and current_status == vs.Pending) or vname in phase1_online ):
            if not self.get_local_vpn(vname).status == vs.Pending:
                return

            # if we're the highest priority, only bring the VPN online at startup if it's not online elsewhere
            if len(currently_online(vname)) == 0:

                if rp is not None and self.site_id == rp[0]:
                    self._logger.info(f'start: {vname}: local VPN is first in priority list, with no peers in Online state - setting online (list={rp})')

                    # Second argument False: do not push this state to peers, to avoid noise during startup
                    # peers will learn of it when they run pull_state on us
                    # (note that currently, if vpn_online fails, it will push that)
                    #
                    # if it's already online, we will detect this and use the existing session/connection
                    await self.vpn_online(vname, False, timeout_throw=False, lock=False)
                # don't take any action if we're not first - if we're Online, we can stay Online
                else:
                    pass
            else:
                # peer has come Online first / was already Online when we started
                if await self.check_local_vpn_connectivity(vname) or await self.check_local_vpn_process(vname):
                    self._logger.info(f'start(): {vname}: peer is already online, stopping our connection')
                    await self._set_local_vpn_offline(vname)

                if self.replica_mode == replica_mode_t.Auto:
                    await self._set_status(vname, vs.Replica, False)
                else:
                    await self._set_status(vname, vs.Offline, False)

        # any remaining local VPNs are set to Replica status (or Offline if replica_mode is not Auto)
        async def phase4(vname):
            if self.replica_mode == replica_mode_t.Auto:
                if self.get_local_vpn(vname).status in [ vs.Offline, vs.Pending ]:
                    await self._set_status(vname, vs.Replica, False)
            else:
                if self.get_local_vpn(vname).status == vs.Pending:
                    await self._set_status(vname, vs.Offline, False)

        await self.task_manager.iter_add_wait(local_vpns, phase1, 'start-phase1')

        # for nodes which are already established, we will get an idea of the state of the network before taking
        #   any action.
        for (site_id, site) in self.sites.items():
            if site_id != self.site_id:
                await self.pull_state(site_id)

        await self.task_manager.iter_add_wait(local_vpns, phase2, 'start-phase2')
        await self.task_manager.iter_add_wait(local_vpns, phase3, 'start-phase3')
        await self.task_manager.iter_add_wait(local_vpns, phase4, 'start-phase4')

        await asyncio.sleep(1)

        for vpn in self.sites[self.site_id].vpn.values():
            vpn.lock.unlock()
        processor.peer_vpn_status_second.instance.activate()
        processor.peer_vpn_status_second.instance.set_discard(False)

        for (site_id, _) in self.sites.items():
            if site_id != self.site_id:
                self.task_manager.add(self.pull_state_task(site_id), f'{site_id}_pull-state')



    async def pull_state_task(self, site_id):
        try:
            while True:
                if self.sites[self.site_id].status == site_status_t.Offline:
                    self._logger.info('pull_state_task: detected local site Offline, exiting')
                    return

                await asyncio.sleep(float(self.sites[site_id].pull_interval.seconds))
                await self.pull_state(site_id)
        except Exception as e:
            print(e)
            # TODO check that this shows the KeyError


    async def start_check_vpn_task(self, vname, iter=None) -> None:

        async def f(vname, iter):
            while iter is None or (iter := iter-1) >= 0:
                
                if self.get_local_vpn(vname).status not in  [ vpn_status_t.Online, vpn_status_t.Pending ]:
                    # the VPN may have been manually set offline locally
                    self._logger.info(f'check_vpn_task({vname}): VPN is not Online or Pending, exiting task')
                    return

                await asyncio.sleep(float(self.local_config['local_vpn_check_interval']))
                result=await self.check_local_vpn_connectivity(vname)

                if result == False:
                    self._logger.info(f'check_vpn_task({vname}): failure detected, initiating retries')
                    
                    self.task_manager.add(
                        self.failure_retry(vname, retries=self.local_config['failure_retries']),
                        f'failure_retry({vname})'
                    )
                    return

        name=f'check-vpn_{vname}'
        if self.task_manager.find(name) != None:
            self._logger.warning(f'start_check_vpn_task: task exists for {vname}')
            return

        self._logger.debug(f'start_check_vpn_task: starting task for {vname}')
        self.task_manager.add(
            f(vname, iter), 
            name
        )

    """
    returns True if the task was successfully found and stopped, False otherwise
    """
    async def stop_check_vpn_task(self, vname : str) -> bool:
        vs=vpn_status_t

        if (t := self.task_manager.find(f'check-vpn_{vname}')) is not None:
            self._logger.debug(f'vpn_offline({vname}): canceled check-vpn task {t.get_name()}')
            t.cancel()
            return True
        else:
            if self.get_local_vpn(vname).status == vs.Online:
                self._logger.error(f'vpn_offline({vname}): could not find check-vpn task')
            return False

    """
    """
    def stop_retries(self, vname : str):
        ts=self.task_manager.list()
        for tname in ts:
            if tname.startswith(f'failure_retry({vname})') and tname != asyncio.current_task().get_name():
                t = self.task_manager.find(tname)
                t.cancel()


    def get_local_vpn(self, vname : str):
        try:
            return self.sites[self.site_id].vpn[vname]
        except KeyError:
            self._logger.warning(f'get_local_vpn: KeyError: {vname} ; keys={self.sites[self.site_id].vpn.keys()}')
            return None

    """
    ==========================================================================================================
    """



    """
    Used by any part of the program to update the status of a local VPN (e.g. when coming online
    or failing). 
    
    Unless broadcast=False, it will trigger an update to all peers
    """
    async def _set_status(self, vname : str, s : vpn_status_t, broadcast=True):
        self.sites[self.site_id].vpn[vname].set_status(s)
        if broadcast:
            await self.broadcast_state()
        
        return True


    """
    call push_state on all peers
    """
    async def broadcast_state(self):
        for (id, _) in self.sites.items():
            if id != self.site_id: 
                await self.push_state(id)

    """
    send a copy of our state to a peer when there's a change
    """
    async def push_state(self, site_id : str):
        try:
            site=self.sites[site_id]

            # don't try to push to an offline - generates noise
            # when the site comes back online it will be detected by either a scheduled call to pull_state,
            # or it will be detected by the site calling pull_state on us
            if site.status == site_status_t.Offline:
                self._logger.info(f'push_state({site_id}): site is offline, skipping')
                return

            await self.http_client.push_state(site, self._encode_state(self.site_id))


        except KeyError:
            self._logger.error('push_state: unknown peer {site_id}')

    """
    check that a peer is online, and save their state
    if the peer is unreacahble, it's marked Offline
    """
    async def pull_state(self, site_id : str): 
        try:
            site=self.sites[site_id]
        except KeyError as e:
            self._logger.error(f'pull_state({site_id}) failed: {e}')
            return None


        def handler(*args):
            processor.peer_vpn_status_first.instance.add(*args)

        await self.http_client.pull_state(site, handler)


    """
    timeout handling:
        treated as a failure, so the timeout (currently set by default_timeout) should be high enough 
        to avoid accidentally prematurely shutting down a VPN connection which was just taking time 
        to start 

    TODO custom task class that handles timeouts and provides locks and other state
    """
    async def vpn_online(self, vname : str, broadcast : bool = True, timeout_throw=True, lock=True, retries=0):

        self.stop_retries(vname)

        L=self.get_local_vpn(vname).lock
        if lock == True:
            self._logger.debug(f'vpn_online({vname}): locking')
            await L.lock()


        try:
            success=await self._vpn_online_impl(vname, broadcast, timeout_throw=True, retries=retries)
            #if L.locked():
            if lock == True:
                L.unlock()
            if success is False:
                return False
            return True
        except TimeoutError:
            await self._set_local_vpn_offline(vname, True)
            await self._set_status(vname, vpn_status_t.Failed, broadcast)

            #if L.locked():
            if lock == True:
                L.unlock()

            if timeout_throw is True:
                raise
            return False
        
    async def _vpn_online_impl(self, vname : str, broadcast : bool = True, retries=0) -> Optional[bool]:
        vs=vpn_status_t

        #self._logger.info(f'vpn_online({vname}): status={self.get_local_vpn(vname).status}')

        if self.get_local_vpn(vname).status == vs.Online:
            self._logger.info(f'vpn_online({vname}): already Online, skipping')
            return True
        
        # if there is already an openvpn process running, the VPN is likely already online, 
        # in which case we don't want to bring up a duplicate connection
        if await self.check_local_vpn_process(vname) == True:
            if await self.check_local_vpn_connectivity(vname) == True:
                self._logger.info(f'vpn_online({vname}): container is already online, setting Online state')
                await self._set_status(vname, vs.Online, broadcast)
                # will check first for an existing task
                await self.start_check_vpn_task(vname)

                return
            else:
                self._logger.info(f'vpn_online({vname}): container has stale process')
                # False: don't remove the route
                await self._set_local_vpn_offline(vname, False)


        await self._set_status(vname, vs.Pending, broadcast)
        success=await self._set_local_vpn_online(vname)
        if success == True:
            await self._set_status(vname, vs.Online, broadcast)

            # using an existing task group seems to block all other tasks / taskgroups
            #async with self._local_vpn_check_tg as tg:
            #    tg.create_task(self.check_vpn_task(vname), name=vname)

            # begin periodic online check for this VPN
            await self.start_check_vpn_task(vname)
        else:

            # scheduling it as a separate task allows us to apply our timeout (TODO) to each retry
            # separately, and also gives an opportunity for another task to acquire the lock
            self.task_manager.add(
                # retries is decremented in failure_retry
                self.failure_retry(vname, broadcast=broadcast, retries=retries),
                f'failure_retry({vname}) retries={retries}'
            )

        return success

    """ 
    TODO refactor vpn_*, possibly wrap in a class for timeout and lock handling
    """

    async def vpn_offline(self, vname : str, broadcast : bool = True, lock=True):
        vs=vpn_status_t

        self.stop_retries(vname)

        L=self.get_local_vpn(vname).lock
        if lock == True:
            self._logger.debug(f'vpn_online({vname}): locking')
            await L.lock()

        await self.stop_check_vpn_task(vname)

        self._logger.info(f'vpn_offline({vname}): setting status to Offline')

        await self._set_local_vpn_offline(vname)
        await self._set_status(vname, vs.Offline, broadcast)

        if lock == True:
            L.unlock()

    async def vpn_replica(self, vname : str, broadcast : bool = True, lock=True) -> bool:
        vs=vpn_status_t

        self.stop_retries(vname)

        L=self.get_local_vpn(vname).lock
        if lock == True:
            self._logger.debug(f'vpn_replica({vname}): locking')
            await L.lock()

        if self._replica_configured(vname):
            await self.stop_check_vpn_task(vname)
            self._logger.info(f'vpn_replica({vname}): setting status to Replica')

            # if we're set to Replica, check if we need to come Online
            # TODO in the future, better to have an event listener or to run these updates through a `processor`
            currently_online= \
                list(filter(lambda site_id: 
                    site_id != self.site_id and  \
                    self.sites[site_id].status == site_status_t.Online and \
                    self.sites[site_id].vpn[vname].status == vs.Online,
                self.sites.keys()))
            if len(currently_online) == 0:

                self._logger.error(f'vpn_offline({vname}): from Replica, setting Online since no peers Online')
                await self.vpn_online(vname, broadcast, lock=False)

            else:
                await self._set_status(vname, vs.Replica, broadcast)
            
            L.unlock()
        else:
            L.unlock()
            raise dynvpn_exception(f'we are not configured as a replica for {vname}')



    """
    ==========================================================================================================
    non-async internal helper functions
    """

    """
    check whether we are configured to act as a replica for the given VPN

    """
    def _replica_configured(self, vname : str):
        try:
            if self.site_id in self.replica_priority[vname]:
                return True
            else:
                return False
        except KeyError:
            self._logger.warning(f'_replica_configured({vname}): VPN not present in priority list')

    """
    return number of indices which separate sites s1 and s2 in the replica list for vname
    positive if s1 has higher replica priority than s2, otherwise negative
        except: if s1 is last in the list and s2 is first, return 1 (as if s2 follows directly after s1)

    return None if 
    """
    def _replica_distance(self, s1, s2, vname,  
        site_state_restrict : List[vpn_status_t] = [site_status_t.Online],
        vpn_state_restrict : List[vpn_status_t] = [vpn_status_t.Replica]
    ) -> Optional[int]:
        rp=self._find_sites(vname, vpn_state_restrict, site_state_restrict)
        
        if vname in self.replica_priority:
            rp=self.replica_priority[vname]
        else:
            self._logger.debug(f'find_sites returning None because {vname} is not in RP list')
            return None

        def f():
            try:
                p1=rp.index(s1)
                p2=rp.index(s2)

                if p1 == len(rp) - 1 and p2 == 0:
                    return 1
                else:
                    return p2 - p1
            except ValueError as e:
                self._logger.error(f'_replica_distance encountered ValueError: {e}')
                return None
            except KeyError:
                return None
        
        return (f(), rp)

        # TODO error log


    """
    find an online site which is configured for the specified VPN, and which also satisfied the specified condition

    this method is used by a peer which has a failed VPN to check whether it needs to auto-retry (if no peer 
        Replicas available) or allow failover to a peer (if there are peer Replicas available)
    """
    def _find_sites(self, vname : str, 
        vpn_state_restrict : List[vpn_status_t] = [vpn_status_t.Replica],
        site_state_restrict : List[vpn_status_t] = [site_status_t.Online],
    ) -> List[str]:

        
        ret=[]

        for site_id, site in self.sites.items():
            if site_id not in self.sites:
                self._logger.warning(f'find_sites: site {site_id} not configured locally')
                continue

            if \
                self.sites[site_id].status == site_status_t.Online and \
                vname in self.sites[site_id].vpn and \
                self.sites[site_id].vpn[vname].status == vpn_status_t.Replica and \
                ( 
                    self.sites[site_id].status in site_state_restrict
                        if len(site_state_restrict) > 0
                        else True
                ) \
                and ( 
                    self.sites[site_id].vpn[vname].status in vpn_state_restrict 
                        if len(vpn_state_restrict) > 0
                        else True
                ):

                ret.append(site_id)

        return ret

    def _local_vpn_obj(self, vname : str):
        try:
            return self.sites[self.site_id].vpn[vname]
        except KeyError:
            self._logger.error(f'local VPN not found: {vname}')
            return None

    # convert state to JSON
    # used for transmission of our state to a peer, or for dumping state on all peers to a client
    # if site_id is None, include all sites
    def _encode_state(self, site_id=None):
        def site_state(site_id):
            return dict({
                'id': site_id,
                'vpn': {
                    vname: str(v.status) for (vname, v) in self.sites[site_id].vpn.items()
                }
            })

        #if site_id is None:
        #    ret={
        #        s_id: site_state(s_id) for s_id, s in self.sites.items()
        #    }
        #else:
        #    ret=site_state(self.site_id)

        state={
            'id': self.site_id,
            'replica_mode': str(self.replica_mode),
            'state': {
                s_id: site_state(s_id) for s_id, s in self.sites.items()
            }
        }

        return json.dumps(state, indent=4)

    def _decode_state(self, data : str) -> Dict:
        d=json.loads(data)

        try:
            for _, site_state in d['state'].items():

                site_state['vpn'] = {
                    k: str_to_vpn_status_t(status_str)
                    for (k, status_str) in site_state['vpn'].items()
                }

            return d
        except KeyError:
            self._logger.error(f'_decode_state failed: invalid data: %s' % data.encode('utf-8'))
            return None



    """
    ================================================================================================
    State/status change handlers
    """


    async def handle_site_status(self, site_id : str, status : site_status_t):
        ss=site_status_t

        site=self.sites[site_id]
        previous_status=site.status
        site.status=status
        self._logger.debug(f'handle_site_status({site_id}): {previous_status} -> {status}')

        match (previous_status, status):
            case (ss.Pending, ss.Offline) | (ss.Online, ss.Offline) | (_, ss.Admin_offline):
                for (vname, _) in site.vpn.items():
                    # count this as a "pull" for the purpose of 
                    processor.peer_vpn_status_first.instance.add(site_id, vname, vpn_status_t.Offline)
                return
            case _:
                pass

    """

    """
    async def failure_retry(self, vname : str, broadcast=True, retries=0):
        vs=vpn_status_t
        vpn = self.get_local_vpn(vname)

        await vpn.lock.lock()
        # fragile, but good enough for now
        if vpn.status not in [ vs.Online, vs.Pending ]:
            self._logger.debug(f'failure_retry({vname}): aborting since VPN status changed')
            return

        await self._set_status(vname, vpn_status_t.Pending, broadcast=broadcast)

        # retry immediately if there are no other available sites with that VPN in Replica (or Online) state
        # in theory there should not any others in Online state - but if there is, we should not restart
        #  
        # note that we are able to check for replicas, and restart, even if we are not configured for replicas
        #   for this VPN
        if  len(self._find_sites(vname, [ vs.Replica, vs.Online ])) == 0 and retries != 0:

            # TODO: option whether to start retrying forever so long as there are no peers with the VPN available

            await self._set_local_vpn_offline(vname, remove_route=False)

            self._logger.warning(f'vpn_online({vname}): failed but no peers in Replica or Online state - retrying')
            if retries > 0:
                retries -= 1

            await self.vpn_online(vname, timeout_throw=True, broadcast=broadcast, retries=retries)

        # no more retries available, so enter Failed status 
        else:

            await self._set_status(vname, vpn_status_t.Failed, broadcast=broadcast)
            await self._set_local_vpn_offline(vname, remove_route=True)
            timeout=self.local_config['failed_status_timeout'] \
                if 'failed_status_timeout' in self.local_config else 0

            if timeout > 0:
                while True:
                    # eventually clear our Failed status, since underlying conditions may have changed
                    await asyncio.sleep(timeout)

                    for _, site in self.sites.items():
                        if site.id == self.site_id:
                            if site.vpn[vname].status != vs.Failed:
                                return
                            else:
                                continue

                        if site.vpn[vname].status == vpn_status_t.Online:
                            await self._set_status(vname, vpn_status_t.Offline)
                            return




    """
    ================================================================================================
    Local access methods

    These methods use local shell scripts to interact with the local VPN containers
    How exactly the scripts accomplish this remains abstract from the point of view of this program
    """

    async def _cmd(self, *args):
        self._logger.info('_cmd(%s)' % [*args])
        proc_obj=await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await proc_obj.communicate()
        return (proc_obj.returncode, stdout, stderr)



    """
    check if the local VPN process is running (this will usually, but not necessarily, mean that
    we also have connectivity)
    """
    async def check_local_vpn_process(self, vname : str) -> bool:
        v=self._local_vpn_obj(vname)

        (ret, stdout, stderr)=await self._cmd(
            os.path.join(self._script_path, 'check-pid.sh'),
            str(vname),
            str(v.local_addr),
            self.local_config["local_vpn_dir"]
        )

        if ret == 0:
            return True
        else:
            return False


    """
    ssh into the VPN container to verify connectivity
    """
    async def check_local_vpn_connectivity(self, vname : str) -> bool:
        # TODO check it's a local vpn
        v=self._local_vpn_obj(vname)

        for _ in range(-1, self.local_config['local_vpn_check_retries']):

            (ret, stdout, stderr)=await self._cmd(
                os.path.join(self._script_path, 'vpn-check-online.sh'),
                str(v.local_addr),
                str(self.local_config['local_vpn_check_timeout']),

                # for testing purposes
                str(vname),
            )

            if ret == 0:
                return True

        self._logger.info(f'check_local_vpn_connectivity({vname}): detected not online: stdout={stdout} stderr={stderr}')
        return False



    """
    stop any running openvpn process and optionally remove any existing anycast route
    does not change status
    """
    async def _set_local_vpn_offline(self, vname : str, remove_route : bool=True):
        v=self.get_local_vpn(vname)

        if v is None:
            raise Exception()
            # TODO specific exception classes once we have a better idea of common exceptions to be thrown throughout the program

        # also removes PID file
        (ret, stdout, stderr)=await self._cmd(
            os.path.join(self._script_path, f'vpn-set-offline.sh'),
            vname,
            str(v.local_addr),
            self.local_config["local_vpn_dir"]
        )

        if remove_route:
            (ret, stdout, stderr)=await self._cmd(
                os.path.join(self._script_path, f'delete-vpn-route.sh'),
                str(v.anycast_addr),
            )

        # TODO error handling

    """
        Start the local VPN session by calling the appropriate shell script, then check
        for connectivity
    """
    async def _set_local_vpn_online(self, vname : str, add_route : bool=True) -> bool:
        # TODO loop for retries

        v=self._local_vpn_obj(vname)

        (ret, stdout, stderr)=await self._cmd(
            os.path.join(self._script_path, f'vpn-set-online.sh'),
            v.name,
            str(v.local_addr),
            self.local_config["local_vpn_dir"],
            self.site_id,
            str(self.sites[self.site_id].gateway_addr)
        )

        if ret != 0:
            stderr_enc=stderr.decode('utf-8')
            stdout_enc=stderr.decode('utf-8')
            self._logger.error(f'_set_local_vpn_online({vname}): online script failed (stdout={stdout_enc}, stderr={stderr_enc})')
            return False

        sleep_time=self.local_config['online_check_delay']
        self._logger.info(f'_set_local_vpn_online({vname}): waiting {sleep_time} seconds before connectivity check')
        await asyncio.sleep(sleep_time)

        success=await self.check_local_vpn_connectivity(vname)

        if success == True:
            if add_route == True:
                (ret, stdout, stderr)=await self._cmd(
                    os.path.join(self._script_path, f'add-vpn-route.sh'),
                    str(v.anycast_addr),
                    # second argument is ignored in the case of route deletion
                    str(self.sites[self.site_id].gateway_addr)
                )

                if ret != 0:
                    self._logger.error(f'_set_local_vpn_online({vname}): route add script failed: stderr={stderr} stdout={stdout}')
                    return False

            return True

        else:
            self._logger.error(f'_set_local_vpn_online({vname}): connectivity check failed, returning False')
            return False

