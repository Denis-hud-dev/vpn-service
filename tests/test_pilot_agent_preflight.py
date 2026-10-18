"""mTLS contract checks use a disposable loopback agent, never JustHost."""
import asyncio
import json
import ssl
from uuid import uuid4

import pytest
from aiohttp import web

from scripts.pilot_agent_preflight import verify
from tests.test_xray_real import free_port, make_certs


def test_pilot_tunnel_requires_mtls_and_exact_loopback_node(tmp_path):
    async def run():
        ca = make_certs(tmp_path)
        node_id = uuid4()
        port = free_port()
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_cert_chain(str(tmp_path / 'server.crt'), str(tmp_path / 'server.key'))
        context.load_verify_locations(cafile=str(ca))
        app = web.Application()
        async def health(request):
            return web.json_response({'healthy': True})
        app.router.add_get('/v1/health', health)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, '127.0.0.1', port, ssl_context=context).start()
        config = tmp_path / 'gateway.json'
        entry = {'url': f'https://localhost:{port}', 'ca': str(ca),
                 'cert': str(tmp_path / 'client.crt'), 'key': str(tmp_path / 'client.key')}
        config.write_text(json.dumps({str(node_id): entry}))
        config.chmod(0o600)
        try:
            await verify(str(config), node_id, port)
            with pytest.raises(ValueError, match='selected node'):
                await verify(str(config), uuid4(), port)
            entry['url'] = f'https://example.org:{port}'
            config.write_text(json.dumps({str(node_id): entry}))
            with pytest.raises(ValueError, match='local SSH tunnel'):
                await verify(str(config), node_id, port)
            entry['url'] = f'https://localhost:{port}'
            config.write_text(json.dumps({str(node_id): entry}))
            config.chmod(0o644)
            with pytest.raises(ValueError, match='permissions'):
                await verify(str(config), node_id, port)
        finally:
            await runner.cleanup()
    asyncio.run(run())
