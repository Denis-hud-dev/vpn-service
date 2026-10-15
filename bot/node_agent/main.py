"""Per-node process: Xray API loopback, external control only via mTLS."""
import asyncio
import fcntl
import logging
import os
import ssl
from pathlib import Path
from aiohttp import web
from bot.node_agent.service import NodeAgent, create_app
from bot.node_agent.store import LeaseStore
from bot.node_agent.xray_cli import XrayCLI


def main():
    logging.basicConfig(level=logging.WARNING)
    state = Path(os.environ['NODE_AGENT_STATE'])
    state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if state.parent.stat().st_mode & 0o077:
        raise ValueError('Node state directory must be private')
    lockfile = open(state.parent/'agent.lock', 'a+')
    os.chmod(state.parent/'agent.lock', 0o600)
    fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
    store = LeaseStore(str(state))
    xray = XrayCLI(os.environ['XRAY_BINARY'], os.environ['XRAY_API_ADDRESS'],
                   os.environ['XRAY_INBOUND_TAG'], int(os.environ['XRAY_INBOUND_PORT']))
    agent = NodeAgent(store, xray)
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_mode = ssl.CERT_REQUIRED
    if Path(os.environ['NODE_AGENT_KEY']).stat().st_mode & 0o077:
        raise ValueError('Node agent key must be private')
    context.load_cert_chain(os.environ['NODE_AGENT_CERT'], os.environ['NODE_AGENT_KEY'])
    context.load_verify_locations(cafile=os.environ['NODE_AGENT_CLIENT_CA'])
    app = create_app(agent)
    async def lifecycle(app):
        task = asyncio.create_task(agent.loop())
        try: yield
        finally:
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass
            store.close()
    app.cleanup_ctx.append(lifecycle)
    web.run_app(app, host=os.environ.get('NODE_AGENT_LISTEN', '127.0.0.1'),
                port=int(os.environ.get('NODE_AGENT_PORT', '19443')),
                ssl_context=context, access_log=None, print=None)


if __name__ == '__main__':
    main()
