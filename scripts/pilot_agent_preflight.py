"""Read-only mTLS health check for one loopback SSH tunnel to the pilot agent.

Never sends /v1/access. Run only with a private config and a separately opened
local tunnel; no production or existing UUID is modified.
"""
import asyncio
import json
import ssl
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID

import aiohttp

from bot.services.node_gateway import HTTPSNodeGateway


async def verify(config_path: str, node_id: UUID, local_port: int = 19444) -> None:
    gateway = HTTPSNodeGateway(config_path)
    if set(gateway.nodes) != {str(node_id)}:
        raise ValueError('Pilot gateway must contain exactly the selected node')
    origin, _ = gateway.nodes[str(node_id)]
    url = urlsplit(origin)
    if url.hostname not in {'localhost', '127.0.0.1'} or url.port != local_port:
        raise ValueError('Pilot gateway must use the local SSH tunnel only')
    if not await gateway.probe(SimpleNamespace(id=node_id)):
        raise RuntimeError('Authenticated pilot health check failed')

    # Only after a successful authenticated probe, verify that the same
    # endpoint does not expose health to a client lacking its certificate.
    data = json.loads(Path(config_path).read_text())
    unauthenticated = ssl.create_default_context(cafile=data[str(node_id)]['ca'])
    timeout = aiohttp.ClientTimeout(total=4, connect=2)
    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            async with session.get(origin + '/v1/health', ssl=unauthenticated) as response:
                if response.status == 200:
                    raise RuntimeError('Pilot agent accepted a client without mTLS')
    except (aiohttp.ClientError, TimeoutError, ssl.SSLError):
        pass  # Expected TLS rejection after the authenticated probe succeeded.


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit('Usage: python -m scripts.pilot_agent_preflight PRIVATE_CONFIG NODE_UUID')
    try:
        asyncio.run(verify(sys.argv[1], UUID(sys.argv[2])))
    except (ValueError, RuntimeError, OSError) as exc:
        raise SystemExit(f'Pilot agent preflight failed: {exc}') from None
    print('Pilot agent: mTLS authenticated; unauthenticated client rejected; access unchanged.')


if __name__ == '__main__':
    main()
