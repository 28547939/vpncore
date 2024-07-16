
import aiohttp
from aiohttp import web
import json
import asyncio

from dynvpn.common import vpn_status_t, site_status_t, vpn_t, site_t, str_to_vpn_status_t
import dynvpn.processor as processor

class http_component():
    def __init__(self, node):
        self.node=node

class client(http_component):
# TODO singleton

    async def push_state(self, site : site_t, state : str):

        timeout=aiohttp.ClientTimeout(total=float(site.pull_timeout.seconds))

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f'http://{site.peer_addr}:{site.peer_port}/peer/push_state', 
                    data=state
                ) as resp:

                    if resp.status == 200:
                        return
                    else:
                        self.node._logger.error(f'error response from {site.id}: {resp.status}: {resp.text}')

        except aiohttp.ClientError as e:
            self.node._logger.warning(f'push_state({site.id}): failed to connect: {e}') 

    
    async def pull_state(self, site : site_t, handler):

        pull_timeout=aiohttp.ClientTimeout(total=site.pull_timeout.seconds)
        try:
            async with aiohttp.ClientSession(timeout=pull_timeout) as session:
                async with session.get(f'http://{site.peer_addr}:{site.peer_port}/peer/pull_state', 
                    data=json.dumps({'site_id': self.node.site_id})) as resp:

                    #self.node._logger.debug(f'pull_state({site.id}): got response {resp.status} from {site.peer_addr}')

                    if resp.status == 200:
                        await self.node.handle_site_status(site.id, site_status_t.Online)

                        data=await resp.content.read()
                        state=self.node._decode_state(data)
                        state=state['state']

                        #for (vpn_id, status) in state['vpn'].items():
                        for (vpn_id, status) in state[site.id]['vpn'].items():
                            handler(site.id, vpn_id, status)
                    else:
                        await self.node.handle_site_status(site.id, site_status_t.Offline)

                        
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if isinstance(e, asyncio.TimeoutError):
                estr='timed out'
            else:
                estr=str(e)
            self.node._logger.warning(f'pull_state({site.id}): failed to connect: {estr}') 
            await self.node.handle_site_status(site.id, site_status_t.Offline)

class server(http_component):

    async def pull_handler(self, request, match):
        self.node._logger.debug(f'received pull_state from {request.remote}')
        req_data=json.loads(await request.content.read())
        site_id=req_data['site_id']

        if self.node.sites[site_id].status != site_status_t.Admin_offline:
            await self.node.handle_site_status(site_id, site_status_t.Online)
            # for now, encode "the entire" state (no arguments), until we have a more 
            # sophisticated format for exchanging data between peers
            #return self.node._encode_state(self.node.site_id)
            return self.node._encode_state()
        else:
            self.node._logger.warning(f'ignoring pull_state from {request.remote}: state is Admin_offline')

    async def push_handler(self, request, match):
        self.node._logger.debug(f'received push_state from {request.remote}')
        data=await request.content.read()
        try:
            state=self.node._decode_state(data)
        except json.JSONDecodeError as e:
            self.node._logger.error(f'push_handler: JSONDecodeError: {e} (data={data})')

        site_id=state['id']
        state=state['state']

        if self.node.sites[site_id].status != site_status_t.Admin_offline:
            await self.node.handle_site_status(site_id, site_status_t.Online)

            for (vpn_id, status) in state[site_id]['vpn'].items():
                processor.peer_vpn_status_first.instance.add(site_id, vpn_id, status)
        else:
            self.node._logger.warning(f'ignoring push_state from {request.remote}: state is Admin_offline')

        return {}



    
    """
    restart a VPN which is online locally
    """
    async def restart_handler(self, request, match):
        self.node._logger.debug(f'received restart from {request.remote}')
        data=await request.content.read()
        vpn_id=match["id"]

        if self.node.sites[self.node.site_id].vpn[vpn_id].status != vpn_status_t.Online:
            return {
                'error': f'VPN {vpn_id} is not online'
            }

        try:
            await self.node._set_local_vpn_offline(vpn_id, False)
        except Exception:
            return {
                'error': f'VPN {vpn_id} is not configured'
            }

        await asyncio.sleep(1)

        r=await self.node._set_local_vpn_online(vpn_id)

        if r == True:
            self.node._logger.debug(f'restart: completed: {vpn_id} Online')
            return {}
        else:
            return {
                'error': 'failed'
            }

    """
    site sets itself offline 
    wait until state is broadcast to all peers
    then set all VPNs offline (no broadcast)
    """
    async def shutdown_handler(self, request, match):
        for vpn_id, _ in self.node.sites[self.node.site_id].vpn.items():
            await self.node._set_local_vpn_offline(vpn_id)
            await self.node._set_status(vpn_id, vpn_status_t.Offline)

        self.node.sites[self.node.site_id].status=site_status_t.Offline

    """
    bring the VPN online at the local site, which causes any 

    TODO - ideally, communicate with existing online peer to set that peer offline first (without broadcast)
    """
    async def vpn_online_handler(self, request, match):
        if 'id' in match:
            await self.node.vpn_online(match['id'], True)
        else:
            return { 'error': 'missing required key: id' }

    async def vpn_offline_handler(self, request, match):
        if 'id' in match:
            await self.node.vpn_offline(match['id'], True)
            return {}
        else:
            return { 'error': 'missing required key: id' }

    async def vpn_replica_handler(self, request, match):
        if 'id' in match:
            vpn_id=match['id']
            await self.node.vpn_offline(vpn_id, True, vpn_status_t.Replica)
            return {}
        else:
            return { 'error': 'missing required key: id' }

    # like pull_state but user-facing instead of peer-facing
    async def dump_state_handler(self, request, match):
        self.node._logger.debug(f'received dump_state from {request.remote}')
        return self.node._encode_state()

    async def task_state_handler(self, request, match):
        ret={}

        for t in self.node.tasks:
            x=ret[t.get_name()]={
                'frames': []
            }

            fs=t.get_stack()
            for frame in fs:
                c=frame.f_code
                #finfo={}
                #finfo['code_info']=

                x['frames'].append( (c.co_filename, frame.f_lineno, c.co_qualname) )

        
        return ret


            





    # TODO API access / HTTP functionality in separate module as it expands
    async def start(self):
            
        # TODO properly handle 404
        router=aiohttp.web.UrlDispatcher()
        router.add_get('/peer/pull_state', self.pull_handler)
        router.add_post('/peer/push_state', self.push_handler)
        router.add_post('/vpn/restart/{id}', self.restart_handler)
        router.add_post('/shutdown', self.shutdown_handler)
        router.add_post('/vpn/set_online/{id}', self.vpn_online_handler)
        router.add_post('/vpn/set_offline/{id}', self.vpn_offline_handler)
        router.add_post('/vpn/set_replica/{id}', self.vpn_replica_handler)
        router.add_get('/dump_state', self.dump_state_handler)
        router.add_get('/task_state', self.task_state_handler)

        async def handler(request):
            match=await router.resolve(request)
            respdata=await match.handler(request, match)
            if type(respdata) == str:
                resptext=respdata
            else:
                resptext=json.dumps(respdata, indent=4)

            resptext += "\n"

            return aiohttp.web.Response(text=resptext)
            # TODO exceptions


        server = web.Server(handler)
        runner = web.ServerRunner(server)
        await runner.setup()
        x = web.TCPSite(runner, str(self.node._server_addr), self.node._server_port)
        await x.start()