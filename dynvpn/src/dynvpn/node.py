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
    dynvpn_lock

import dynvpn.processor as processor
from dynvpn import dynvpn_http
from dynvpn.task_manager import task_manager

def log(): 
    pass


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

    # vpn_id -> list of site_ids in descending order of replica
    replica : Dict[str, List[str]]


    def __init__(self, this_site_id : str, local_config, global_config, logger : logging.Logger):

        self.site_id = this_site_id
        self._logger=logger
        self._script_path=local_config['script_path']

        sites_config=global_config['sites']
        anycast_addr=global_config['anycast_addr']
        self.sites={}

        self.local_config = local_config

        self.task_manager=task_manager(self._logger)

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
            self.sites[site_id]=site_t.load(self, site_id, site_config, anycast_addr)
            self.sites[site_id].status = site_status_t.Pending
            for (_, vpn) in self.sites[site_id].vpn.items(): 
                vpn.status = vpn_status_t.Pending

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

        async def local_vpns():
            for k in self.sites[self.site_id].vpn.keys():
                yield k

        vs=vpn_status_t

        processor.peer_vpn_status_first.instance.activate()

        # prevent updates from peers (push state, pull state) from triggering any 
        # responses or other effects while we initialize
        processor.peer_vpn_status_second.instance.set_discard(True)

        # protect vpns from any operations (such as setting online, offline) while we initialize
        for vpn in self.sites[self.site_id].vpn.values():
            await vpn.lock.lock()

        # TODO potentially factor out these local functions

        # only consider peers which are reachable
        #def prio(vpn_id):
        #    return list(filter(lambda s_id: self.sites[s_id].status != site_status.Offline, self.replica_priority[vpn_id]))
        
        def currently_online(vpn_id):
            return list(filter(lambda s: 
                s.id != self.site_id and \
                vpn_id in s.vpn and \
                s.vpn[vpn_id].status == vpn_status_t.Online, 
            self.sites.values()))

        # at this point, all local VPNs' states have been initialized to Pending

        phase1_online=set()

        # first pass - check for local VPNs with existing online connections
        async for vpn_id in local_vpns():
            #await self._set_status(vpn_id, vs.Offline, False)
            # TODO race: peer might find this offline status during startup 
            # better to keep this state locally here and keep all status on pending

            #pr=prio(vpn_id)

            if await self.check_local_vpn_process(vpn_id):
                self._logger.info(f'start(): {vpn_id}: process exists at startup, checking connectivity')
                if await self.check_local_vpn_connectivity(vpn_id):
                    self._logger.info(f'start(): {vpn_id}: connectivity check succeeded, status -> Pending')
                    # already online - it may be allowed to remain online after startup
                    phase1_online.add(vpn_id)

                    #await self._set_status(vpn_id, vs.Pending, False)
                else:
                    self._logger.info(f'start(): {vpn_id}: connectivity check failed, killing stale process')
                    await self._set_local_vpn_offline(vpn_id)

            #if self.site_id == pr[0]:
            #    self._logger.info(f'start(): {vpn_id}: local site has priority, status -> Pending')
            #    await self._set_status(vpn_id, vs.Pendin, False)


        # wait for other nodes 
        # TODO - need to use a barrier
        #await asyncio.sleep(float(self.local_config['start_delay']))

        # for nodes which are already established, we will get an idea of the state of the network before taking
        #   any action.
        for (site_id, site) in self.sites.items():
            if site_id != self.site_id:
                await self.pull_state(site_id)

        # second pass - if existing peers don't have the VPN in Online status, allow our VPN to stay online:
        #
        # for a given vpn_id, prioritize sites which already have it in Online status; even over the highest priority site
        # if multiple connections come online due to a partition or startup race, then one of them will end up transitioning to Replica - 
        #   doesn't matter which
        async for vpn_id in local_vpns():
            #if self.get_local_vpn(vpn_id).status != vs.Pending:
            if vpn_id not in phase1_online:
                continue
                
            phase1_online.remove(vpn_id)

            if len(currently_online(vpn_id)) == 0:
                self._logger.info(f'start(): {vpn_id}: no other replicas online, maintaining Online state')
                #self._logger.debug(f'start(): {vpn_id}: {self.sites}')
                #await self._set_status(vpn_id, vs.Online, False)
                await self.vpn_online(vpn_id, False, timeout_throw=False, lock=False)
            else:
                resultstr=f'start(): {vpn_id}: peer is online, taking ours offline; '
                if self.replica_mode == replica_mode_t.Auto:
                    resultstr += f'status -> Replica (replica_mode=Auto)'
                    await self._set_status(vpn_id, vs.Replica, False)
                else:
                    resultstr += f'status -> Offline (replica_mode={self.replica_mode})'
                    await self._set_status(vpn_id, vs.Offline, False)

                self._logger.info(resultstr)

                # another node has already reported this VPN being online - need to set ours
                # offline in case the underlying OpenVPN connection is online
                await self._set_local_vpn_offline(vpn_id)

        await asyncio.sleep(1)

        # third pass to check any further VPNs detected online earlier, or others where we are first in the replica list
        async for vpn_id in local_vpns():
            try:
                rp=self.replica_priority[vpn_id]
            except KeyError:
                self._logger.warning(f'vpn {vpn_id} was present in local VPN list, but not in priority list - skipping')

            #if not ( (rp[0] == self.site_id and current_status == vs.Pending) or vpn_id in phase1_online ):
            if not self.get_local_vpn(vpn_id).status == vs.Pending:
                continue

            # if we're the highest priority, only bring the VPN online at startup if it's not online elsewhere
            if len(currently_online(vpn_id)) == 0:

                if self.site_id == rp[0]:
                    self._logger.info(f'start: {vpn_id}: local VPN is first in priority list, with no peers in Online state - setting online (list={rp})')

                    # Second argument False: do not push this state to peers, to avoid noise during startup
                    # peers will learn of it when they run pull_state on us
                    # (note that currently, if vpn_online fails, it will push that)
                    #
                    # if it's already online, we will detect this and use the existing session/connection
                    await self.vpn_online(vpn_id, False, timeout_throw=False, lock=False)
                # don't take any action if we're not first - if we're Online, we can stay Online
                else:
                    pass
            else:
                # peer has come Online first / was already Online when we started
                if await self.check_local_vpn_connectivity(vpn_id) or await self.check_local_vpn_process(vpn_id):
                    self._logger.info(f'start(): {vpn_id}: peer is already online, stopping our connection')
                    await self._set_local_vpn_offline(vpn_id)

                if self.replica_mode == replica_mode_t.Auto:
                    await self._set_status(vpn_id, vs.Replica, False)
                else:
                    await self._set_status(vpn_id, vs.Offline, False)

        # any remaining local VPNs are set to Replica status (or Offline if replica_mode is not Auto)
        async for vpn_id in local_vpns():
            if self.replica_mode == replica_mode_t.Auto:
                if self.get_local_vpn(vpn_id).status == vs.Offline:
                    await self._set_status(vpn_id, vs.Replica, False)
            else:
                if self.get_local_vpn(vpn_id).status == vs.Pending:
                    await self._set_status(vpn_id, vs.Offline, False)

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


    async def start_check_vpn_task(self, vpn_id, iter=None) -> None:

        async def f(vpn_id, iter):
            while iter is None or (iter := iter-1) >= 0:
                
                if self.get_local_vpn(vpn_id).status not in  [ vpn_status_t.Online, vpn_status_t.Pending ]:
                    # the VPN may have been manually set offline locally
                    self._logger.info(f'check_vpn_task({vpn_id}): VPN is not Online or Pending, exiting task')
                    return

                await asyncio.sleep(float(self.local_config['local_vpn_check_interval']))
                result=await self.check_local_vpn_connectivity(vpn_id)

                if result == False:
                    self._logger.info(f'check_vpn_task({vpn_id}): failure detected, setting Failed status and exiting')
                    self.task_manager.add(
                        self.handle_local_failure(vpn_id),
                        f'handle_local_failure({vpn_id})'
                    )
                    return

        name=f'check-vpn_{vpn_id}'
        if self.task_manager.find(name) != None:
            self._logger.warning(f'start_check_vpn_task: task exists for {vpn_id}')
            return


        self._logger.debug(f'start_check_vpn_task: starting task for {vpn_id}')
        self.task_manager.add(
            f(vpn_id, iter), 
            name
        )


    def get_local_vpn(self, vpn_id : str):
        try:
            return self.sites[self.site_id].vpn[vpn_id]
        except KeyError:
            return None

    """
    ==========================================================================================================
    """



    """
    Used by any part of the program to update the status of a local VPN (e.g. when coming online
    or failing). 
    
    Unless broadcast=False, it will trigger an update to all peers
    """
    async def _set_status(self, vpn_id : str, s : vpn_status_t, broadcast=True):
        self.sites[self.site_id].vpn[vpn_id].set_status(s)
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
    
    TODO - possibly encapsulate every major operation like this in a separate class with timeout and
    failure handling, and cleanup
    -> may be necessary: right now, timeout_wrap is not aware of the locks used by vpn_online, which 
        should be unlocked on timeout
    """
    async def vpn_online(self, vpn_id : str, broadcast : bool = True, timeout_throw=True, lock=True):

        L=self.get_local_vpn(vpn_id).lock
        if lock == True:
            # TODO separate lock wrapper class to more easily trace 
            self._logger.debug(f'vpn_online({vpn_id}): locking')
            await L.lock()


        try:
            success=await self._vpn_online_impl(vpn_id, broadcast, timeout_throw=True)
            if L.locked():
                L.unlock()
            if success is False:
                return False
            return True
        except TimeoutError:
            await self._set_local_vpn_offline(vpn_id, True)
            await self._set_status(vpn_id, vpn_status_t.Failed, broadcast)

            if L.locked():
                L.unlock()

            if timeout_throw is True:
                raise
            return False
        
    """
    this timeout includes any necessary calls to handle_local_failure, which will essentially retry
    vpn_online again, and so on. that recursion, between vpn_online and handle_local_failure, is still
    controlled by the outermost timeout (this one)
    """
    @timeout_wrap
    async def _vpn_online_impl(self, vpn_id : str, broadcast : bool = True) -> Optional[bool]:
        vs=vpn_status_t

        #self._logger.info(f'vpn_online({vpn_id}): status={self.get_local_vpn(vpn_id).status}')

        if self.get_local_vpn(vpn_id).status == vs.Online:
            self._logger.info(f'vpn_online({vpn_id}): already Online, skipping')
            return True
        
        # if there is already an openvpn process running, the VPN is likely already online, 
        # in which case we don't want to bring up a duplicate connection
        if await self.check_local_vpn_process(vpn_id) == True:
            if await self.check_local_vpn_connectivity(vpn_id) == True:
                self._logger.info(f'vpn_online({vpn_id}): container is already online, setting Online state')
                await self._set_status(vpn_id, vs.Online, broadcast)
                # will check first for an existing task
                await self.start_check_vpn_task(vpn_id)

                return
            else:
                self._logger.info(f'vpn_online({vpn_id}): container has stale process')
                # False: don't remove the route
                await self._set_local_vpn_offline(vpn_id, False)

        try:
            if self.site_id not in self.replica_priority[vpn_id]:
                self._logger.error(f'vpn_online({vpn_id}): this site is not present on the replica list')
                return None
        except KeyError:
            self._logger.warning(f'vpn_online({vpn_id}): not present in priority list, aborting')

        await self._set_status(vpn_id, vs.Pending, broadcast)
        success=await self._set_local_vpn_online(vpn_id)
        if success == True:
            await self._set_status(vpn_id, vs.Online, broadcast)

            # using an existing task group seems to block all other tasks / taskgroups
            #async with self._local_vpn_check_tg as tg:
            #    tg.create_task(self.check_vpn_task(vpn_id), name=vpn_id)

            # begin periodic online check for this VPN
            await self.start_check_vpn_task(vpn_id)
        else:
            await self.handle_local_failure(vpn_id, broadcast=broadcast)

        return success

    # TODO separate `vpn_replica` function to set state to Replica, to match the API functions set_*
    async def vpn_offline(self, vpn_id : str, broadcast : bool = True,  \
                s : vpn_status_t = vpn_status_t.Offline,
                lock=True
    ):
        vs=vpn_status_t

        L=self.get_local_vpn(vpn_id).lock
        if lock == True:
            self._logger.debug(f'vpn_online({vpn_id}): locking')
            await L.lock()

        if (t := self.task_manager.find(f'check-vpn_{vpn_id}')) is not None:
            self._logger.debug(f'vpn_offline({vpn_id}): canceled check-vpn task {t.get_name()}')
            t.cancel()
        else:
            if self.get_local_vpn(vpn_id).status == vs.Online:
                self._logger.error(f'vpn_offline({vpn_id}): could not find check-vpn task')

        self._logger.info(f'vpn_offline({vpn_id}): setting status to {s}')

        # if we're set to Replica, check if we need to come Online
        # TODO in the future, better to have an event listener or to run these updates through a `processor`
        if s == vs.Replica:
            currently_online= \
                list(filter(lambda site_id: 
                    site_id != self.site_id and  \
                    self.sites[site_id].status == site_status_t.Online and \
                    self.sites[site_id].vpn[vpn_id].status == vs.Online,
                self.sites.keys()))
            if len(currently_online) == 0:

                self._logger.error(f'vpn_offline({vpn_id}): from Replica, setting Online since no peers Online')
                await self.vpn_online(vpn_id, broadcast, lock=False)

            else:
                await self._set_status(vpn_id, s, broadcast)

        else:
            await self._set_local_vpn_offline(vpn_id)
            await self._set_status(vpn_id, s, broadcast)

        if lock == True:
            L.unlock()


    """
    ==========================================================================================================
    non-async internal helper functions
    """

    def _replica_eligible(self, vpn_id, 
        site_state_restrict : List[vpn_status_t] = [site_status_t.Online],
        vpn_state_restrict : List[vpn_status_t] = [vpn_status_t.Replica]
    ) -> List[str]:

        rp=self.replica_priority[vpn_id]
        rp=list(filter(lambda site_id:  \
                ( 
                    self.sites[site_id].status in site_state_restrict
                        if len(site_state_restrict) > 0
                        else True
                )
                and ( 
                    self.sites[site_id].vpn[vpn_id].status in vpn_state_restrict 
                        if len(vpn_state_restrict) > 0
                        else True
                ),
            rp
        ))

        return rp



    """
    return number of indices which separate sites s1 and s2 in the replica list for vpn_id
    positive if s1 has higher replica priority than s2, otherwise negative
        except: if s1 is last in the list and s2 is first, return 1 (as if s2 follows directly after s1)
    """
    def _replica_distance(self, s1, s2, vpn_id,  
        site_state_restrict : List[vpn_status_t] = [site_status_t.Online],
        vpn_state_restrict : List[vpn_status_t] = [vpn_status_t.Replica]
    ) -> Optional[int]:
        rp=self._replica_eligible(vpn_id, site_state_restrict, vpn_state_restrict)

        def f():
            try:
                p1=rp.index(s1)
                p2=rp.index(s2)

                if p1 == len(rp) - 1 and p2 == 0:
                    return 1
                else:
                    return p2 - p1
            except ValueError:
                return None
            except KeyError:
                return None
        
        return (f(), rp)

        # TODO error log


    """
    find an online site which is configured for the specified VPN, and which also satisfied the specified condition
    """
    def _eligible_failover(self, vpn_id : str, cond=lambda _: True):
        return list(filter(
            lambda site_id: 
                self.sites[site_id].status == site_status_t.Online and \
                site_id in self.replica_priority[vpn_id] and \
                cond(site_id),
            self.replica_priority[vpn_id]
        ))

    def _local_vpn_obj(self, vpn_id : str):
        try:
            return self.sites[self.site_id].vpn[vpn_id]
        except KeyError:
            self._logger.error(f'local VPN not found: {vpn_id}')
            return None

    # convert state to JSON
    # used for transmission of our state to a peer, or for dumping state on all peers to a client
    # if site_id is None, include all sites
    #def _encode_state(self, site_id=None):
    def _encode_state(self, site_id=None):
        def site_state(site_id):
            return dict({
                'id': site_id,
                'vpn': {
                    vpn_id: str(v.status) for (vpn_id, v) in self.sites[site_id].vpn.items()
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
                for (vpn_id, _) in site.vpn.items():
                    # count this as a "pull" for the purpose of 
                    processor.peer_vpn_status_first.instance.add(site_id, vpn_id, vpn_status_t.Offline)
                return
            case _:
                pass

    """
    called when we attempt to bring a VPN online but fail (Pending -> Failed), or
    when an online VPN fails (Online -> Failed)

    failure doesn't stop the check-vpn task, which 
    """
    async def handle_local_failure(self, vpn_id : str, broadcast=True):
        await self._set_status(vpn_id, vpn_status_t.Failed, broadcast=broadcast)

        # restart immediately if there are no other available sites with that VPN
        if len(self._eligible_failover(vpn_id)) == 0:
            await self._set_local_vpn_offline(vpn_id, remove_route=False)

            self._logger.warning(f'vpn_online({vpn_id}): failed but eligible_failover is empty - retrying')
            await self.vpn_online(vpn_id, timeout_throw=True, broadcast=broadcast)

        else:
            await self._set_local_vpn_offline(vpn_id, remove_route=True)
            timeout=self.local_config['failed_status_timeout'] \
                if 'failed_status_timeout' in self.local_config else 0

            if timeout > 0:
                while True:
                    # eventually clear our Failed status, since underlying conditions may have changed
                    await asyncio.sleep(timeout)

                    for _, site in self.sites.items():
                        if site.vpn[vpn_id].status == vpn_status_t.Online:
                            await self._set_status(vpn_id, vpn_status_t.Offline)
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
    async def check_local_vpn_process(self, vpn_id : str) -> bool:
        v=self._local_vpn_obj(vpn_id)

        (ret, stdout, stderr)=await self._cmd(
            os.path.join(self._script_path, 'check-pid.sh'),
            str(vpn_id),
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
    async def check_local_vpn_connectivity(self, vpn_id : str) -> bool:
        # TODO check it's a local vpn
        v=self._local_vpn_obj(vpn_id)

        for _ in range(-1, self.local_config['local_vpn_check_retries']):

            (ret, stdout, stderr)=await self._cmd(
                os.path.join(self._script_path, 'vpn-check-online.sh'),
                str(v.local_addr),
                str(self.local_config['local_vpn_check_timeout']),

                # for testing purposes
                str(vpn_id),
            )

            if ret == 0:
                return True

        self._logger.info(f'check_local_vpn_connectivity({vpn_id}): detected not online: stdout={stdout} stderr={stderr}')
        return False



    """
    stop any running openvpn process and optionally remove any existing anycast route
    does not change status
    """
    async def _set_local_vpn_offline(self, vpn_id : str, remove_route : bool=True):
        v=self.get_local_vpn(vpn_id)

        if v is None:
            raise Exception()
            # TODO specific exception classes once we have a better idea of common exceptions to be thrown throughout the program

        # also removes PID file
        (ret, stdout, stderr)=await self._cmd(
            os.path.join(self._script_path, f'vpn-set-offline.sh'),
            vpn_id,
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
    async def _set_local_vpn_online(self, vpn_id : str, add_route : bool=True) -> bool:
        # TODO loop for retries

        v=self._local_vpn_obj(vpn_id)

        (ret, stdout, stderr)=await self._cmd(
            os.path.join(self._script_path, f'vpn-set-online.sh'),
            v.id,
            str(v.local_addr),
            self.local_config["local_vpn_dir"],
            self.site_id
        )

        if ret != 0:
            stderr_enc=stderr.decode('utf-8')
            stdout_enc=stderr.decode('utf-8')
            self._logger.error(f'_set_local_vpn_online({vpn_id}): online script failed (stdout={stdout_enc}, stderr={stderr_enc})')
            return False

        sleep_time=self.local_config['online_check_delay']
        self._logger.info(f'_set_local_vpn_online({vpn_id}): waiting {sleep_time} seconds before connectivity check')
        await asyncio.sleep(sleep_time)

        success=await self.check_local_vpn_connectivity(vpn_id)

        if success == True:
            if add_route == True:
                (ret, stdout, stderr)=await self._cmd(
                    os.path.join(self._script_path, f'add-vpn-route.sh'),
                    str(v.anycast_addr),
                    # second argument is ignored in the case of route deletion
                    str(self.sites[self.site_id].gateway_addr)
                )

                if ret != 0:
                    self._logger.error(f'_set_local_vpn_online({vpn_id}): route add script failed: stderr={stderr} stdout={stdout}')
                    return False

            return True

        else:
            self._logger.error(f'_set_local_vpn_online({vpn_id}): connectivity check failed, returning False')
            return False

