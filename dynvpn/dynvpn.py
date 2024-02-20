#!/usr/local/etc/python3.11


import sys
import os
import subprocess
import asyncio
import aiohttp
from aiohttp import web

from enum import Enum, auto

from collections import deque

from ipaddress import IPv4Address, IPv4Network, ip_address
import yaml
import json
import datetime

from typing import Optional, Dict, Tuple, List
from dataclasses import dataclass

import argparse
import logging

global_logger : logging.Logger

def log(): 
    pass

class status(Enum):

    def __str__(self):
        return self.name
    def __json__(self):
        return json.dumps(self.name)

# see README.md in this directory for information about these states and their transitions
class vpn_status(status):
    Online = auto()
    Replica = auto()
    Pending = auto()
    Failed = auto()
    Offline = auto()

def str_to_vpn_status(s : str) -> vpn_status:
    return vpn_status.__getattr__(s)

class site_status(status):

    # the site is online and its VPNs 
    Online = auto()
    # the site is coming online for the first time or transitioning from being online
    # the site advertises this status to its peers
    Pending = auto()
    # remote peers mark a site as Offline when the site's peer can't be reached
    Offline = auto()


"""
an actual vpn object is coupled with a site_id, so a vpn object represents a record of the vpn on that
specific site.
a (string) vpn_id is used in other contexts 
"""
@dataclass()
class vpn():
    id : str
    site_id : str

    # local_addr should be None unless site_id is the local site
    local_addr : Optional[IPv4Address]
    anycast_addr : IPv4Address

    status : vpn_status

    def set_status(self, s : vpn_status):
        self.status=s

@dataclass()
class site():
    id : str
    vpn : Dict[str, vpn]

    # IP that the remote dynvpn instance is listening on
    # for example, a 'dummy' bridge inside the ipsec jail (see documentation)
    peer_addr : IPv4Address
    peer_port : int

    # the IP assigned to the container/jail bridge 
    # needed when adding the anycast route locally
    gateway_addr : IPv4Address

    # IP assigned to ipsec jail
    # needed  TODO where?
    #ipsec_addr : IPv4Address

    # whether we are able to communicate with this peer, regardless of the status
    # of its VPNs
    status: site_status

    """
    implement failover (if applicable) after no response from the
    peer (site's dynvpn instance)

    these are None only if this site is the local site
    """
    pull_interval : Optional[int]
    pull_timeout : Optional[int]

    # not yet implemented
    #timeout_retries : Optional[int]

    def resolve_vpn_anycast(self, id : str) -> Optional[IPv4Address]:
        try:
            return self.vpn[id].anycast_addr
        except KeyError:
            global_logger.warn(f'resolve_vpn_anycast({id}): not found')
            return None

    def resolve_vpn_local(self, id : str) -> Optional[IPv4Address]:
        try:
            return self.vpn[id].local_addr
        except KeyError:
            global_logger.warn(f'resolve_vpn_local({id}): not found')
            return None


class instance():
    sites : Dict[str, site]

    # vpn_id -> list of site_ids in descending order of replica
    replica : Dict[str, List[str]]

    def _load_site(self, site_id, site_config, local_vpn_config):
        # separate map for VPNs for each peer, even though each vpn object is initialized to be identical
        vpns={}

        for vpn_id in site_config['vpn']:
            try:
                (local_addr, anycast_addr)=local_vpn_config[vpn_id]
            except KeyError:
                self._logger.info(f'VPN {vpn_id} present on {site_id} not configured for local site - skipping')
                pass
            # TODO catch exception of not enough elements

            if vpn_id not in self.local_config['vpn']:
                self.logger.info(f"Not tracking remote VPN on {site_id} which is not configured locally: {vpn_id}")
                continue

            vpn_obj=vpn(
                id=vpn_id,
                site_id=site_id,
                status=vpn_status.Pending,
                local_addr=local_addr,
                anycast_addr=anycast_addr
            )

            
            if len(self.local_config['vpn'][vpn_id]) != 2:
                self.logger.error(f"Local VPN %s provided invalid arguments, skipping: {self.local_config['vpn'][vpn_id]}")
                continue
            
            for (i, k) in zip([0, 1], ['local_addr', 'anycast_addr']):
                setattr(
                    vpn_obj, k, 
                    # first argument is local addr, second is anycast addr
                    ip_address(self.local_config['vpn'][vpn_id][i])
                )

            vpns[vpn_id]=vpn_obj

        if site_id != self.local_config['site_id']:
            pull_interval = datetime.timedelta(seconds=self.local_config['timers'][site_id][0])
            pull_timeout = datetime.timedelta(seconds=self.local_config['timers'][site_id][1])
        else:
            pull_interval=None
            pull_timeout=None

        self.sites[site_id] = site(
            site_id,
            peer_addr=ip_address(site_config['peer_addr']),
            peer_port=int(site_config['peer_port']),
            gateway_addr=ip_address(site_config['gateway_addr']),
            vpn=vpns,
            pull_interval=pull_interval,
            pull_timeout=pull_timeout,
            status=site_status.Pending
        )


    def __init__(self, this_site_id : str, local_config, global_config, logger : logging.Logger):

        self.site_id = this_site_id
        self._logger=logger
        self._script_path=local_config['script_path']

        sites_config=global_config['sites']
        self.sites={}

        self.local_config = local_config

        self.unprocessed_status_updates = deque()
        self.ready = False


        for (site_id, site) in sites_config.items():
            self._load_site(site_id, site, self.local_config['vpn'])
            self.sites[site_id].status = site_status.Pending
            for (_, vpn) in self.sites[site_id].vpn.items(): 
                vpn.status = vpn_status.Pending

        if this_site_id not in self.sites:
            raise Exception("local site {this_site_id} not present in site config")

        self._server_addr=self.sites[this_site_id].peer_addr
        self._server_port=self.sites[this_site_id].peer_port
        self.replica_priority=global_config['replica_priority']


    async def start_http_server(self):
        async def pull_handler(request):
            self._logger.info(f'received pull_state from {request.remote}')
            req_data=json.loads(await request.content.read())
            await self.handle_site_status(req_data['site_id'], site_status.Online)

            return aiohttp.web.Response(text=self._encode_state())

        async def push_handler(request):
            self._logger.info(f'received push_state from {request.remote}')
            data=await request.content.read()
            try:
                state=self._decode_state(data)
            except json.JSONDecodeError as e:
                self._logger.error(f'push_handler: JSONDecodeError: {e} (data={data})')

            await self.handle_site_status(state['id'], site_status.Online)
            await self.handle_peer_state(state)

            return aiohttp.web.Response(text='{}')

        router=aiohttp.web.UrlDispatcher()
        router.add_routes([
            web.get('/pull_state', pull_handler),
            web.post('/push_state', push_handler),
        ])
        async def handler(request):
            match=await router.resolve(request)
            return await match.handler(request)
            # TODO exceptions


        server = web.Server(handler)
        runner = web.ServerRunner(server)
        await runner.setup()
        x = web.TCPSite(runner, str(self._server_addr), self._server_port)
        await x.start()

    async def pull_state_task(self, site_id):
        while True:
            await asyncio.sleep(float(self.sites[site_id].pull_interval.seconds))
            await self.pull_state(site_id)

    async def check_vpn_task(self, vpn_id) -> None:
        while True:
            if self.get_local_vpn(vpn_id).status != vpn_status.Online:
                # this usually means that there have been multiple calls to vpn_online, close together
                self._logger.warning(f'check_vpn_task({vpn_id}): VPN is not Online, exiting task')
                return

            await asyncio.sleep(float(self.local_config['local_vpn_check_interval']))
            result=await self.check_local_vpn_connectivity(vpn_id)

            if result == False:
                self._logger.info(f'check_vpn_task({vpn_id}): failure detected, setting Failed status and exiting')
                await self.handle_local_failure(vpn_id)
                return
        

    """
    Entry point to the instance after instantiation
    """
    async def start(self):
        # wait for online_delay, then start all VPNs where we have first replica

        # make our state available to other peers and listen for push_state
        await self.start_http_server()

        # wait for other nodes to come online (TODO random jitter)
        await asyncio.sleep(float(self.local_config['start_delay']))

        # initial state pull from peers
        for (site_id, site) in self.sites.items():
            if site_id != self.site_id:
                await self.pull_state(site_id)

        for (vpn_id, _) in self.sites[self.site_id].vpn.items():
            # update status without broadcasting yet
            await self._set_status(vpn_id, vpn_status.Pending, False)

            prio=self.replica_priority[vpn_id]
            if self.site_id == prio[0]:

                # only bring the VPN online at startup if it's not online elsewhere
                if len(list(filter(lambda s: s.vpn[vpn_id].status == vpn_status.Online, self.sites.values()))) == 0:
                    self._logger.info(f'local VPN is first in priority list, with no replicas available - setting online (list={prio})')

                    # False argument - do not push this state to peers, to avoid noise during startup
                    # peers will learn of it when they run pull_state on us
                    # (note that currently, if vpn_online fails, it will push that)
                    await self.vpn_online(vpn_id, False)


        self.ready=True

        # begin periodic state pull from peers
        # TODO review Task, TaskGroup documentation to ensure they're handled cleanly through their entire lifecycle
        async with asyncio.TaskGroup() as tg:
            for (site_id, _) in self.sites.items():
                if site_id != self.site_id:
                    tg.create_task(self.pull_state_task(site_id))

        #await asyncio.sleep(self.local_config.start_delay)


    def get_local_vpn(self, vpn_id : str):
        return self.sites[self.site_id].vpn[vpn_id]

    # convert out state to JSON for transmission to a peer
    def _encode_state(self):
        s=dict({
            'id': self.site_id,
            'vpn': {
                vpn_id: str(v.status) for (vpn_id, v) in self.sites[self.site_id].vpn.items()
            }
        })
        return json.dumps(s)

    def _decode_state(self, data : str) -> Dict:
        d=json.loads(data)

        d['vpn'] = {
            k: str_to_vpn_status(status_str)
            for (k, status_str) in d['vpn'].items()
        }

        return d


    """
    Used by any part of the program to update the status of a local VPN (e.g. when coming online
    or failing). 
    
    Unless broadcast=False, it will trigger an update to all peers
    """
    async def _set_status(self, vpn_id : str, s : vpn_status, broadcast=True):
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
            if site.status == site_status.Offline:
                self._logger.info(f'push_state({site_id}): site is offline, skipping')
                return

            timeout=aiohttp.ClientTimeout(total=float(site.pull_timeout.seconds))

            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(f'http://{site.peer_addr}:{site.peer_port}/push_state', data=self._encode_state()) as resp:
                        if resp.status == 200:
                            return
                        else:
                            self._logger.error(f'error response from {site.id}: {resp.status}: {resp.text}')

            except aiohttp.ClientError as e:
                self._logger.warning(f'push_state({site_id}): failed to connect: {e}') 

        except KeyError:
            self._logger.error('push_state: unknown peer {site_id}')

    """
    check that a peer is online
    """
    async def pull_state(self, site_id : str): 
        try:
            site=self.sites[site_id]
        except KeyError as e:
            self._logger.error(f'pull_state({site_id}) failed: {e}')
            return None

        pull_timeout=aiohttp.ClientTimeout(total=site.pull_timeout.seconds)
        try:
            async with aiohttp.ClientSession(timeout=pull_timeout) as session:
                async with session.get(f'http://{site.peer_addr}:{site.peer_port}/pull_state', data=json.dumps({'site_id': self.site_id})) as resp:
                    self._logger.info(f'pull_state({site_id}): got response {resp.status} from {site.peer_addr}')

                    if resp.status == 200:
                        await self.handle_site_status(site_id, site_status.Online)
                    else:
                        await self.handle_site_status(site_id, site_status.Offline)

                    data=await resp.content.read()
                    await self.handle_peer_state(self._decode_state(data))
                        
        except aiohttp.ClientError as e:
            self._logger.warning(f'pull_state({site_id}): failed to connect: {e}') 
            await self.handle_site_status(site_id, site_status.Offline)

    def _local_vpn_obj(self, vpn_id : str):
        try:
            return self.sites[self.site_id].vpn[vpn_id]
        except KeyError:
            self._logger.error(f'local VPN not found: {vpn_id}')
            return None

    async def _cmd(self, *args):
        self._logger.info('running command: %s' % [*args])
        proc_obj=await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await proc_obj.communicate()
        return (proc_obj.returncode, stdout, stderr)


    """
        Start the local VPN session by calling the appropriate shell script, then check
        for connectivity
    """
    async def _set_local_vpn_online(self, vpn_id : str) -> bool:
        # TODO loop for retries

        v=self._local_vpn_obj(vpn_id)

        (ret, stdout, stderr)=await self._cmd(
            os.path.join(self._script_path, f'vpn-set-online.sh'),
            v.id,
            str(v.local_addr),
            self.local_config["local_vpn_dir"]
        )

        if ret != 0:
            self._logger.error(f'_set_local_vpn_online({vpn_id}): online script failed (stdout={stdout}, stderr={stderr})')
            return False

        sleep_time=5
        self._logger.info(f'waiting {sleep_time} seconds before connectivity check')
        await asyncio.sleep(sleep_time)

        success=await self.check_local_vpn_connectivity(vpn_id)

        #if ret != 0:
        #    self._logger.error('{stderr}')
        
        if success == True:
            (ret, stdout, stderr)=await self._cmd(
                os.path.join(self._script_path, f'add-vpn-route.sh'),
                str(v.anycast_addr),
                # second argument is ignored in the case of route deletion
                str(self.sites[self.site_id].gateway_addr)
            )

            if ret != 0:
                self._logger.error('_set_local_vpn_online({vpn_id}): route add script failed: stderr={stderr} stdout={stdout}')
                return False

            return True

        else:
            self._logger.error('_set_local_vpn_online({vpn_id}): connectivity check failed, returning False')
            return False

        #else:
        #    self._handle_local_failure(v)


    """
    stop any running openvpn process and remove any existing anycast route
    """
    async def _set_local_vpn_offline(self, vpn_id : str):
        v=self.get_local_vpn(vpn_id)
        (ret, stdout, stderr)=await self._cmd(
            os.path.join(self._script_path, f'vpn-set-offline.sh'),
            str(v.local_addr),
        )

        (ret, stdout, stderr)=await self._cmd(
            os.path.join(self._script_path, f'delete-vpn-route.sh'),
            str(v.anycast_addr),
        )

        # TODO error handling


    async def vpn_online(self, vpn_id : str, broadcast : bool = True) -> Optional[bool]:
        vs=vpn_status

        #self._logger.info(f'vpn_online({vpn_id}): status={self.get_local_vpn(vpn_id).status}')

        if self.get_local_vpn(vpn_id).status == vs.Online:
            self._logger.info(f'vpn_online({vpn_id}): already Online, skipping')
            return True

        if self.site_id not in self.replica_priority[vpn_id]:
            # TODO do this only if 'strict replica' is configured
            self._logger.error('vpn_online({vpn_id}): this site is not present on the replica list')
            return None


        await self._set_status(vpn_id, vs.Pending, broadcast)
        success=await self._set_local_vpn_online(vpn_id)
        if success == True:
            await self._set_status(vpn_id, vs.Online, broadcast)



            # using an existing task group seems to block all other tasks / taskgroups
            #async with self._local_vpn_check_tg as tg:
            #    tg.create_task(self.check_vpn_task(vpn_id), name=vpn_id)

            # begin periodic online check for this VPN
            asyncio.create_task(self.check_vpn_task(vpn_id), name=f'{vpn_id}-check')

        else:
            # currently, this will broadcast state to peers, even if our broadcast argument is False
            await self.handle_local_failure(vpn_id)



    async def handle_site_status(self, site_id : str, status : site_status):
        ss=site_status

        site=self.sites[site_id]
        previous_status=site.status
        site.status=status

        match (previous_status, status):
            case (ss.Pending, ss.Offline) | (ss.Online, ss.Offline):
                for (vpn_id, _) in site.vpn.items():
                    await self.handle_peer_vpn_status(site.id, vpn_id, vpn_status.Offline)
                return
            case _:
                pass


    """
    return number of indices which separate sites s1 and s2 in the replica list for vpn_id
    positive if s1 has higher replica than s2, otherwise negative
    if either s1 or s2 is not present in the replica list, return None
    """
    def _replica_distance(self, s1, s2, vpn_id) -> Optional[int]:
        try:
            p1=self.replica_priority[vpn_id].index(s1)
            p2=self.replica_priority[vpn_id].index(s2)

            return p2 - p1
        except ValueError:
            return None
        except KeyError:
            return None

    def _eligible_failover(self, vpn_id : str, cond=lambda _: True):
        return list(filter(
            lambda site_id: 
                self.sites[site_id].status == site_status.Online and \
                site_id in self.replica_priority[vpn_id] and \
                cond(site_id),
                #self._replica_distance(self.site_id, site_id, vpn_id) is not None,
            self.replica_priority[vpn_id]
        ))


    async def handle_local_failure(self, vpn_id : str):
        await self._set_status(vpn_id, vpn_status.Failed)

        # restart immediately if there are no other available sites with that VPN
        if len(self._eligible_failover(vpn_id)) == 0:
            self._logger.warning(f'vpn_online({vpn_id}): failed but eligible_failover is empty - retrying')
            await self.vpn_online(vpn_id)
        else:
            # eventually clear our Failed status, since underlying conditions may have changed
            await asyncio.sleep(self.local_config['failed_status_timeout'])
            await self._set_status(vpn_id, vpn_status.Offline)


    async def handle_peer_state(self, state : Dict) -> None:
        site_id=state['id']
        self._logger.info(f'handle_peer_state({site_id}): {state["vpn"]})')
        for (vpn_id, status) in state['vpn'].items():
            await self.handle_peer_vpn_status(site_id, vpn_id, status)

    """
    Main entry point for handling state changes on peers
    """
    async def handle_peer_vpn_status(self, site_id : str, vpn_id : str, status : vpn_status):
        vs=vpn_status

        pri=self.replica_priority
        remote_vpn=self.sites[site_id].vpn[vpn_id]
        previous_status=remote_vpn.status

        # take no action if we are not 'ready' after initial startup
        if self.ready != True:
            self._logger.info(f'handle_peer_vpn_status({vpn_id}@{site_id}): ignoring status because ready !=  True ({previous_status} -> {status})')
            self.unprocessed_status_updates.append( (site_id, vpn_id, status) )
            return
        elif self.ready == True:
            if len(self.unprocessed_status_updates) > 0:
                args=self.unprocessed_status_updates.pop()
                # handle all previously queued status updates in order before handling the present one
                await self.handle_peer_vpn_status(*args)

                # TODO race condition which disturbs the order, if a status update occurs while this
                # call stack is being built


        remote_vpn.status=status

        # no state change is needed if this status is already recorded
        if status == previous_status:
            return

        self._logger.info(f'handle_peer_vpn_status({vpn_id}@{site_id}): {previous_status} -> {status}')


        # if a VPN is unavailable, check the replica list to see if we need to take any
        # action (failover)
        match (previous_status, status):
            case (vs.Online, vs.Failed) | (vs.Pending, vs.Failed) | (_, vs.Offline):
                d=self._replica_distance(site_id, self.site_id, vpn_id)

                # take no action if we're not listed on the replica list
                if d is None:
                    return

                # come online if the offline node is directly above us, including when the the node is last
                # in the list and we are first
                # TODO handle the case where there are offline sites between us and failed site
                else:
                    if d == 1 or (self.replica_priority[vpn_id][0] == self.site_id and self.replica_priority[vpn_id][-1] == site_id):
                        await self.vpn_online(vpn_id)
                        return

            case (_, vs.Online):

                d=self._replica_distance(site_id, self.site_id, vpn_id)
                self._logger.info(f'handle_peer_vpn_status({vpn_id}@{site_id}): Online: replica distance is {d}')

                if d is None:
                    return

                # currently, transition to Replica regardless of whether we are higher in the replica list
                else:
                    # Pending -> Replica
                    # Online -> Replica
                    if self.get_local_vpn(vpn_id).status in \
                        [ vs.Pending, vs.Online ]:

                        await self._set_local_vpn_offline(vpn_id)
                        await self._set_status(vpn_id, vs.Replica)
                    else:
                        return


            # currently there is no need to handle these
            case (_, vs.Replica):
                pass

            case (_, vs.Pending):
                pass

            # illegal transitions
            case (vs.Replica, vs.Failed):
                self._logger.warning(f'handle_peer_vpn_status({vpn_id}@{site_id}): illegal transition or missed a transition')

            case _:
                raise ValueError()

    """
    ssh into the VPN container to verify connectivity
    """
    async def check_local_vpn_connectivity(self, vpn_id : str) -> bool:
        # TODO check it's a local vpn
        v=self._local_vpn_obj(vpn_id)

        (ret, stdout, stderr)=await self._cmd(
            os.path.join(self._script_path, 'vpn-check-online.sh'),
            str(v.local_addr),
        )

        if ret == 0:
            return True
        else:
            self._logger.info(f'check_local_vpn_connectivity({vpn_id}): detected not online: stdout={stdout} stderr={stderr}')
            return False

    """
    called when we attempt to bring a VPN online but fail (Pending -> Failed), or
    when an online VPN fails (Online -> Failed)
    """
    def _handle_local_failure(self, v : vpn):
        self._set_status(v.id, vpn_status.Failed)

async def main():

    prs=argparse.ArgumentParser(
        prog='',
        description='',
    )

    prs.add_argument('--site-id', required=True)
    #prs.add_argument('--local-config', required=True)
    args=vars(prs.parse_args())


    fmt=logging.Formatter(
        datefmt='%Y-%m-%d_%H-%M-%S.%f'
    ) 
    logger=logging.getLogger('dynvpn')
    logger.setLevel(logging.DEBUG)
    h=logging.StreamHandler()
    h.setFormatter(fmt)
    logger.addHandler(h)

    with open('./local.yml', 'rb') as f:
        local_config=yaml.safe_load(f)

    with open('./global.yml', 'rb') as f:
        global_config=yaml.safe_load(f)

    i = instance(args['site_id'], local_config, global_config, logger)
    await i.start()


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
