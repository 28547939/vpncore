

import argparse
import logging
import yaml
import asyncio
import sys

from dynvpn.node import node

local_defaults = {

    'failed_status_timeout': 0,

    'local_vpn_check_interval': 10,
    'local_vpn_check_timeout': 3,
    'local_vpn_check_retries': 1,

    'pull_interval': 30,
    'pull_timeout': 10,
}

async def main():

    prs=argparse.ArgumentParser(
        prog='',
        description='',
    )

    prs.add_argument('--site-id', required=True)
    prs.add_argument('--local-config', required=True, default='local.yml')
    prs.add_argument('--global-config', required=True, default='global.yml')
    args=vars(prs.parse_args())


    fmt=logging.Formatter(
        fmt='[%(asctime)s] [%(module)s] %(message)s',
        datefmt='%Y-%m-%d_%H-%M-%S.%f'
    ) 
    logger=logging.getLogger('dynvpn')
    logger.setLevel(logging.DEBUG)
    h=logging.StreamHandler()
    h.setFormatter(fmt)
    logger.addHandler(h)

    with open(args["local_config"], 'rb') as f:
        local_config=yaml.safe_load(f)

    with open(args["global_config"], 'rb') as f:
        global_config=yaml.safe_load(f)

    for k, default in local_defaults.items():
        if k not in local_config:
            local_config[k]=default

    instance = node(args['site_id'], local_config, global_config, logger)
    try:
        await instance.start()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == 'dynvpn':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())