"""Fail-closed entry point for an isolated, agent-owned Xray process.

Run this and its Xray child in ONE systemd service with KillMode=control-group.
Never point XRAY_CONFIG at the existing static JustHost TCP/8443 service.
"""
import asyncio
import fcntl
import logging
import os
import ssl
from pathlib import Path

from aiohttp import web

from bot.node_agent.managed_xray import ManagedXray
from bot.node_agent.service import NodeAgent, create_app
from bot.node_agent.store import LeaseStore
from bot.node_agent.xray_cli import XrayCLI


async def run_node():
    state = Path(os.environ['NODE_AGENT_STATE'])
    state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if state.parent.stat().st_mode & 0o077:
        raise ValueError('Node state directory must be private')
    lockfile = open(state.parent / 'agent.lock', 'a+')
    os.chmod(state.parent / 'agent.lock', 0o600)
    fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)

    key = Path(os.environ['NODE_AGENT_KEY'])
    if key.is_symlink() or key.stat().st_mode & 0o077:
        raise ValueError('Node agent key must be private')
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_cert_chain(os.environ['NODE_AGENT_CERT'], str(key))
    context.load_verify_locations(cafile=os.environ['NODE_AGENT_CLIENT_CA'])

    binary = os.environ['XRAY_BINARY']
    cli = XrayCLI(binary, os.environ['XRAY_API_ADDRESS'],
                  os.environ['XRAY_INBOUND_TAG'], int(os.environ['XRAY_INBOUND_PORT']))
    reserved = {int(value) for value in os.environ.get(
        'XRAY_RESERVED_PORTS', '443,8443').split(',')}
    managed = ManagedXray(binary, os.environ['XRAY_CONFIG'], cli,
                          reserved_ports=reserved)
    store = LeaseStore(str(state))
    runner = None
    try:
        await managed.start()  # Xray config has zero clients before this point.
        agent = NodeAgent(store, cli, restart_xray=managed.restart,
                          max_users=int(os.environ.get('NODE_AGENT_MAX_USERS', '1000')))
        await agent.reconcile()  # Restore only unexpired local leases.
        runner = web.AppRunner(create_app(agent), access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, host=os.environ.get('NODE_AGENT_LISTEN', '127.0.0.1'),
                           port=int(os.environ.get('NODE_AGENT_PORT', '19443')),
                           ssl_context=context)
        await site.start()
        await agent.loop()  # Any enforcement failure exits, stopping Xray below.
    finally:
        try:
            if runner is not None:
                await runner.cleanup()
        finally:
            try:
                await managed.stop()
            finally:
                store.close()
                lockfile.close()


def main():
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run_node())


if __name__ == '__main__':
    main()
