
from enum import Enum, auto
from typing import Optional, Dict, Tuple, List

import datetime
import functools
import asyncio

from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, ip_address
import logging
import json

global_logger : logging.Logger

class json_encoder(json.JSONEncoder):
    def default(self, x):
        return str(x)

class enum_base(Enum):
    def __str__(self):
        return self.name
    def __json__(self):
        return json.dumps(self.name)

class replica_mode_t(enum_base):
    Auto = auto()
    Manual = auto()
    Disabled = auto()

def str_to_replica_mode_t(s : str) -> replica_mode_t:

    match s:
        case 'Auto':
            return replica_mode_t.Auto
        case 'Manual':
            return replica_mode_t.Manual
        case 'Disabled':
            return replica_mode_t.Disabled
        case _:
            raise Exception(
                f'replica_mode config setting must be one of Auto, Manual, or Dislabed, '
                + f'but was {s}'
            )

class status(enum_base):
    pass


# see README.md in this directory for information about these states and their transitions
class vpn_status_t(status):
    Online = auto()
    Replica = auto()
    Pending = auto()
    Failed = auto()
    Offline = auto()

def str_to_vpn_status_t(s : str) -> vpn_status_t:
    return vpn_status_t.__getattr__(s)

class site_status_t(status):

    # the site is online and its VPNs 
    Online = auto()
    # the site is coming online for the first time or transitioning from being online
    # the site advertises this status to its peers
    Pending = auto()
    # remote peers mark a site as Offline when the site's peer can't be reached
    Offline = auto()
    #
    Admin_offline = auto()


class lock_status_t(enum_base):
    Locked = auto()
    Unlocked = auto()


# TODO
class dynvpn_exception(Exception):
    pass

"""
TODO contextmanager with optional lock to match what we see in node.vpn_*
    currently can't use asyncio contextmanager without checking lock argument first
"""
class dynvpn_lock():
    def __init__(self, trace=False, name=None) -> None:
        self._lock=asyncio.Lock()
        self.locked_task : Optional[str]=None
        self._trace=trace
        self._name=name
        self._logger=logging.getLogger('dynvpn')

    def _logtrace(self, method, str):
        self._logger.debug(f'dynvpn_lock[name={self._name}]: {method}: {str}')


    async def lock(self):
        if self._lock.locked() and self.locked_task == (tname := asyncio.current_task().get_name()):
            if self._trace:
                self._logtrace('lock', f'task {tname} already has the lock')
            return
        else:
            if self._trace:
                self._logtrace('lock', f'task {asyncio.current_task().get_name()} waiting')
            await self._lock.acquire()
            if self._trace:
                self._logtrace('lock', f'task {asyncio.current_task().get_name()} acquired')
            self.locked_task = asyncio.current_task().get_name()

    def unlock(self):
        if self._lock.locked():
            tname=asyncio.current_task().get_name()
            if self.locked_task != tname:
                raise Exception(
                    f'dynvpn_lock.unlock: current task {tname} cannot '
                    + f'unlock lock, locked by {self.locked_task}'
                )
            else:
                if self._trace:
                    self._logtrace('lock', f'task {tname} unlocked')

                self.locked_task=None
                self._lock.release()

    def locked(self):
        return self._lock.locked()

    def get_status(self):
        
        if self._lock.locked():
            return {
                'status': lock_status_t.Locked,
                'task': self.locked_task,
            }
        else:
            return {
                'status': lock_status_t.Unlocked,
                'task': self.locked_task,
            }


# represents a VPN container on a specific host
@dataclass()
class vpn_t():

    # the numeric ID remains abstract from the standpoint of the dynvpn instance
    # however, it's also true that currently, all VPN containers are named "dynvpn<id>",
    #   where <id> is its unique numeric ID.
    # we do not need the numeric ID in this program.
    name : str
    site_id : str

    local_addr : IPv4Address
    anycast_addr : IPv4Address

    # only relevant for local VPNs
    lock : Optional[dynvpn_lock]

    # a VPN should always be initialized to Pending status
    status : vpn_status_t = vpn_status_t.Pending

    def set_status(self, s : vpn_status_t):
        self.status=s

    @staticmethod
    def vname(vpn_id : int) -> str:
        if isinstance(vpn_id, int):
            return 'dynvpn%d' % vpn_id
        else:
            raise TypeError(f'vname was passed "{vpn_id}" but requires an argument of type int')


@dataclass()
class site_t():
    id : str
    vpn : Dict[str, vpn_t]

    # IP that the remote dynvpn instance is listening on
    # for example, a 'dummy' bridge inside the ipsec jail (see documentation)
    peer_addr : IPv4Address
    peer_port : int

    # the IP assigned to the container/jail bridge 
    # needed when adding the anycast route locally
    gateway_addr : IPv4Address

    # whether we are able to communicate with this peer, regardless of the status
    # of its VPNs
    status: site_status_t

    """
    implement failover (if applicable) after no response from the
    peer (site's dynvpn instance)

    these are None only if this site is the local site
    """
    pull_interval : Optional[int]
    pull_timeout : Optional[int]
    pull_retries : Optional[int]

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



    # local_vpn_config is the `vpn` key in the local.yml config
    # site_config is the site's entry from the `sites` key in the global.yml config
    @staticmethod
    def load(node, site_id, site_config, global_config):
        # separate map for VPNs for each peer, even though each vpn object is initialized to be identical
        vpns={}

        for vpn_id in site_config['vpn']:
            vname=vpn_t.vname(int(vpn_id))

            # the ipaddress library already includes support for this operation
            # TODO catch invalid value / exception
            anycast_addr=ip_address(global_config['vpn_anycast_addr_base']) + vpn_id
            local_addr=ip_address(site_config['vpn_local_addr_base']) + vpn_id

            if site_id == node.site_id:
                L=dynvpn_lock(trace=True, name=vname)
            else:
                L=None

            vpn_obj=vpn_t(
                name=vname,
                site_id=site_id,
                local_addr=ip_address(local_addr),
                anycast_addr=ip_address(anycast_addr),
                lock=L
            )

            vpns[vname]=vpn_obj

        if site_id != node.local_config['site_id']:
            pull_interval = datetime.timedelta(seconds=node.local_config['pull_interval'])
            pull_timeout = datetime.timedelta(seconds=node.local_config['pull_timeout'])
            pull_retries = node.local_config['pull_retries']
        else:
            pull_interval=None
            pull_timeout=None
            pull_retries=None

        return site_t(
            site_id,
            peer_addr=ip_address(site_config['peer_addr']),
            peer_port=int(site_config['peer_port']),
            gateway_addr=ip_address(site_config['gateway_addr']),
            vpn=vpns,
            pull_interval=pull_interval,
            pull_timeout=pull_timeout,
            pull_retries=pull_retries,
            status=site_status_t.Pending
        )
