
import aiohttp
from aiohttp import web
import json
import asyncio

from dynvpn.common import   \
    vpn_status_t, site_status_t, vpn_t,  \
    site_t, str_to_vpn_status_t, replica_mode_t, str_to_replica_mode_t, \
    json_encoder, dynvpn_exception

import dynvpn.processor as processor


"""
TODO
"""
class http_response():
    pass

class http_error(http_response):
    pass

class http_ok(http_response):
    pass


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
        retries_completed=-1

        async def handle_failure():
            nonlocal retries_completed
            retries_completed += 1

            if site.pull_retries is not None and site.pull_retries > retries_completed:
                # retry immediately
                self.node._logger.info(f'pull_state({site.id}): retrying '+
                    f'({retries_completed+1}/{site.pull_retries})'
                )
                await do_pull()
            else:
                await self.node.handle_site_status(site.id, site_status_t.Offline)


        async def do_pull():

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
                            await handle_failure()

                            
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if isinstance(e, asyncio.TimeoutError):
                    estr='timed out'
                else:
                    estr=str(e)
                self.node._logger.warning(f'pull_state({site.id}): failed to connect: {estr}') 
                await handle_failure()

        await do_pull()

"""
TODO - pass through structured return values from underlying interface in node.py to here
refactor request handling/response

Ok -> empty response with optional message
Error -> { "error": "msg" }

possibly use exceptions in node.py
"""
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
            try:
                await self.node.vpn_online(match['id'], True, timeout_throw=True)
                return {}
            except TimeoutError:
                return { 'error': 'timed out' }
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
            if self.node.replica_mode in [ replica_mode_t.Auto, replica_mode_t.Manual ]:
                try:
                    await self.node.vpn_replica(vpn_id, True)
                    return {}
                except dynvpn_exception as e:
                    return {
                        'error': str(e)
                    }
            else:
                return { 'error': 
                    f'refused: replica_mode is set to {self.node.replica_mode}, ' 
                    +'but needs to be Auto or Manual'
                }
        else:
            return { 'error': 'missing required key: id' }

    # like pull_state but user-facing instead of peer-facing
    async def node_state_handler(self, request, match):
        return self.node._encode_state()

    async def debug_state_handler(self, request, match):
        ret={}

        for tname in self.node.task_manager.list():
            t=self.node.task_manager.find(tname)
            x=ret[tname]={
                'frames': []
            }

            fs=t.get_stack()
            for frame in fs:
                c=frame.f_code
                #finfo={}
                #finfo['code_info']=

                x['frames'].append( (c.co_filename, frame.f_lineno, c.co_qualname) )

            if len(fs) == 0:
                x.update({
                    #'coro': t.get_coro(),
                    'done': t.done(),
                    'cancelled': t.cancelled(),
                    'cancelling': t.cancelling()
                })

        ret['locks']={}
        for vpn_id, vpn in self.node.sites[self.node.site_id].vpn.items():
            ret['locks'][vpn_id]=vpn.lock.get_status()
        
        return ret


    async def replica_mode_handler(self, request, match):
        if 'value' in match:
            try:
                self.node.replica_mode=str_to_replica_mode_t(match['value'])
                return {}
            except Exception as e:
                return { 'error': str(e) }
            


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
        router.add_get('/node_state', self.node_state_handler)
        router.add_get('/debug_state', self.debug_state_handler)
        router.add_post('/set_replica_mode/{value}', self.replica_mode_handler)

        async def handler(request):
            match=await router.resolve(request)
            respdata=await match.handler(request, match)
            if type(respdata) == str:
                resptext=respdata
            else:
                resptext=json.dumps(respdata, indent=4, cls=json_encoder)

            resptext += "\n"

            return aiohttp.web.Response(text=resptext)
            # TODO exceptions


        server = web.Server(handler)
        runner = web.ServerRunner(server)
        await runner.setup()
        x = web.TCPSite(runner, str(self.node._server_addr), self.node._server_port)
        await x.start()