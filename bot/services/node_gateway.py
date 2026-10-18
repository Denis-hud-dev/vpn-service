"""Central mTLS client. Node routing comes only from a private trusted config."""
import json
import os
import ssl
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID
import aiohttp


class NodeGatewayError(RuntimeError):
    pass


class HTTPSNodeGateway:
    def __init__(self, config_path: str):
        file = Path(config_path)
        if file.stat().st_mode & 0o077:
            raise ValueError('Node gateway config permissions must be private')
        data = json.loads(file.read_text())
        if not isinstance(data, dict):
            raise ValueError('Invalid node gateway configuration')
        self.nodes = {}
        for key, entry in data.items():
            UUID(key)
            if not isinstance(entry, dict) or set(entry) != {'url','ca','cert','key'}:
                raise ValueError('Invalid node gateway configuration')
            url = urlsplit(entry['url'])
            if (url.scheme != 'https' or not url.hostname or url.username or url.password
                    or url.path not in ('','/') or url.query or url.fragment):
                raise ValueError('Node endpoint requires HTTPS origin')
            if Path(entry['key']).stat().st_mode & 0o077:
                raise ValueError('Node client key permissions must be private')
            context = ssl.create_default_context(cafile=entry['ca'])
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(entry['cert'], entry['key'])
            self.nodes[key] = (entry['url'].rstrip('/'), context)

    async def probe(self, node) -> bool:
        entry = self.nodes.get(str(node.id))
        if entry is None:
            return False
        origin, context = entry
        try:
            timeout = aiohttp.ClientTimeout(total=4, connect=2)
            async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
                async with session.get(origin+'/v1/health', ssl=context) as response:
                    if response.status != 200:
                        return False
                    return await response.json() == {'healthy': True}
        except (aiohttp.ClientError, TimeoutError, ssl.SSLError, ValueError):
            return False

    async def apply(self, node, credential: UUID, *, enabled: bool,
                    expires_at: datetime | None, revision: int):
        entry = self.nodes.get(str(node.id))
        if entry is None:
            raise NodeGatewayError('Node agent not configured')
        origin, context = entry
        payload = {'credential': str(credential), 'revision': revision,
                   'enabled': enabled,
                   'expires_at': int(expires_at.timestamp()) if expires_at else None}
        try:
            # A revoke ACK waits for the dedicated Xray process to restart and
            # rehydrate surviving users, so the health-probe timeout is too short.
            timeout = aiohttp.ClientTimeout(total=25, connect=2)
            async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
                async with session.put(origin+'/v1/access', json=payload,
                                       ssl=context) as response:
                    if response.status != 200:
                        raise NodeGatewayError('Node did not confirm operation')
                    body = await response.json()
                    if body != {'applied': True}:
                        raise NodeGatewayError('Invalid node acknowledgement')
        except (aiohttp.ClientError, TimeoutError, ssl.SSLError):
            # URLs, certificates and response bodies must not enter logs.
            raise NodeGatewayError('Node unreachable or TLS rejected') from None
