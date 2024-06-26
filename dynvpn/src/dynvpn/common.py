
from enum import Enum, auto
from typing import Optional, Dict, Tuple, List

import datetime

from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, ip_address
import logging
import json

global_logger : logging.Logger

class status(Enum):

    def __str__(self):
        return self.name
    def __json__(self):
        return json.dumps(self.name)

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

# represents a VPN container on a specific host
@dataclass()
class vpn_t():
    id : str
    site_id : str

    # local_addr should be None unless site_id is the local site
    local_addr : Optional[IPv4Address]
    anycast_addr : IPv4Address

    status : vpn_status_t

    def set_status(self, s : vpn_status_t):
        self.status=s

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


    # local_vpn_config is the `vpn` key in the local.yml config
    # site_config is the site's entry from the `sites` key in the global.yml config
    @staticmethod
    def load(node, site_id, site_config, local_vpn_config):
        # separate map for VPNs for each peer, even though each vpn object is initialized to be identical
        vpns={}

        for vpn_id in site_config['vpn']:

            if vpn_id not in local_vpn_config:
                node._logger.info(f"Not tracking remote VPN on {site_id} which is not configured locally: {vpn_id}")
                continue

            if len(local_vpn_config[vpn_id]) != 2:
                node._logger.error(f"Local VPN %s provided invalid arguments, skipping: {node.local_config['vpn'][vpn_id]}")
                continue

            (local_addr, anycast_addr)=local_vpn_config[vpn_id]

            vpn_obj=vpn_t(
                id=vpn_id,
                site_id=site_id,
                status=vpn_status_t.Pending,
                local_addr=local_addr,
                anycast_addr=anycast_addr
            )
            
            for (i, k) in zip([0, 1], ['local_addr', 'anycast_addr']):
                setattr(
                    vpn_obj, k, 
                    # first argument is local addr, second is anycast addr
                    ip_address(node.local_config['vpn'][vpn_id][i])
                )

            vpns[vpn_id]=vpn_obj

        if site_id != node.local_config['site_id']:
            pull_interval = datetime.timedelta(seconds=node.local_config['timers'][site_id][0])
            pull_timeout = datetime.timedelta(seconds=node.local_config['timers'][site_id][1])
        else:
            pull_interval=None
            pull_timeout=None

        return site_t(
            site_id,
            peer_addr=ip_address(site_config['peer_addr']),
            peer_port=int(site_config['peer_port']),
            gateway_addr=ip_address(site_config['gateway_addr']),
            vpn=vpns,
            pull_interval=pull_interval,
            pull_timeout=pull_timeout,
            status=site_status_t.Pending
        )
