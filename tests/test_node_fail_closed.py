"""Offline proof of managed-node isolation and fail-closed control flow."""
import asyncio
import json
import os
import socket
import time
from uuid import uuid4

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.node_agent.managed_xray import ManagedXray, validate_managed_config
from bot.node_agent.service import NodeAgent, create_app
from bot.node_agent.store import LeaseStore
from bot.node_agent.xray_cli import XrayCLI, XrayCommandError


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def config_file(tmp_path, api_port, inbound_port):
    path = tmp_path / 'managed.json'
    path.write_text(json.dumps({
        'log': {'loglevel': 'warning', 'access': 'none'},
        'api': {'tag': 'api', 'listen': f'127.0.0.1:{api_port}',
                'services': ['HandlerService']},
        'inbounds': [{'tag': 'kuda-managed', 'listen': '127.0.0.1',
                      'port': inbound_port, 'protocol': 'vless',
                      'settings': {'decryption': 'none', 'clients': []},
                      'streamSettings': {'network': 'tcp', 'security': 'reality',
                                         'realitySettings': {'privateKey': 'test-only'}}}],
        'outbounds': [{'tag': 'direct', 'protocol': 'freedom'}],
    }))
    path.chmod(0o600)
    return path


def test_managed_config_rejects_static_users_and_reserved_ports(tmp_path):
    api_port, inbound_port = free_port(), free_port()
    path = config_file(tmp_path, api_port, inbound_port)
    cli = XrayCLI('/fake/xray', f'127.0.0.1:{api_port}', 'kuda-managed', inbound_port)
    validate_managed_config(str(path), cli)
    data = json.loads(path.read_text())
    data['inbounds'][0]['settings']['clients'] = [{'id': str(uuid4())}]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='isolated'):
        validate_managed_config(str(path), cli)
    data['inbounds'][0]['settings'] = {
        'decryption': 'none', 'clients': [], 'users': [{'id': str(uuid4())}]}
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='isolated'):
        validate_managed_config(str(path), cli)
    data['inbounds'][0]['settings'].pop('users')
    path.write_text(json.dumps(data))
    data['inbounds'][0]['settings']['clients'] = []
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='isolated'):
        validate_managed_config(str(path), cli, {inbound_port})
    path.chmod(0o644)
    with pytest.raises(ValueError, match='private'):
        validate_managed_config(str(path), cli)


def test_xray_api_rejects_unexpected_static_user():
    class Response(XrayCLI):
        async def _command(self, *args):
            return b'{"users":[{"email":"unmanaged@example.test"}]}'
    cli = Response('/fake/xray', '127.0.0.1:10085', 'kuda-managed', 9443)
    with pytest.raises(XrayCommandError, match='Invalid Xray API response'):
        asyncio.run(cli.managed_users())


def test_revocation_and_expiry_restart_only_managed_xray(tmp_path, monkeypatch):
    class Xray:
        def __init__(self):
            self.users = set()
            self.connections = set()
            self.restarts = 0
        @staticmethod
        def email(value):
            return 'kuda-' + str(value)
        async def managed_users(self):
            return self.users.copy()
        async def add(self, value):
            self.users.add(self.email(value))
        async def add_many(self, values):
            for value in values:
                await self.add(value)
        async def remove(self, value):
            self.users.remove(self.email(value))
        async def restart(self):
            self.restarts += 1
            self.connections.clear()
            self.users.clear()

    async def scenario():
        now = int(time.time())
        monkeypatch.setattr('bot.node_agent.service.time.time', lambda: now)
        store = LeaseStore(str(tmp_path / 'leases.db'))
        xray = Xray()
        first, second = uuid4(), uuid4()
        agent = NodeAgent(store, xray, restart_xray=xray.restart, max_users=2)
        await agent.apply(first, 1, True, now + 30)
        await agent.apply(second, 1, True, now + 5)
        with pytest.raises(ValueError, match='limit'):
            await agent.apply(uuid4(), 1, True, now + 30)
        assert not agent.failed.is_set()
        xray.connections.update((first, second))
        await agent.apply(first, 2, False, None)
        assert xray.restarts == 1 and not xray.connections
        assert xray.users == {xray.email(second)}
        assert first not in store.desired(now)
        monkeypatch.setattr('bot.node_agent.service.time.time', lambda: now + 6)
        await agent.reconcile()
        assert xray.restarts == 2 and not xray.users
        store.close()

    asyncio.run(scenario())


def test_failed_revoke_is_not_acknowledged_or_healthy(tmp_path, caplog):
    class Xray:
        users = set()
        @staticmethod
        def email(value):
            return 'kuda-' + str(value)
        async def managed_users(self):
            return self.users.copy()
        async def add_many(self, values):
            self.users.update(self.email(value) for value in values)
        async def remove(self, value):
            self.users.remove(self.email(value))

    async def scenario():
        store = LeaseStore(str(tmp_path / 'leases.db'))
        xray = Xray()
        async def failed_restart():
            raise RuntimeError('secret endpoint: do not log')
        agent = NodeAgent(store, xray, restart_xray=failed_restart)
        credential = uuid4()
        await agent.apply(credential, 1, True, int(time.time()) + 30)
        client = TestClient(TestServer(create_app(agent)))
        await client.start_server()
        try:
            response = await client.put('/v1/access', json={
                'credential': str(credential), 'revision': 2,
                'enabled': False, 'expires_at': None})
            assert response.status == 503
            assert await response.json() == {'applied': False}
        finally:
            await client.close()
        assert 'secret endpoint' not in caplog.text
        assert str(credential) not in caplog.text
        assert agent.failed.is_set()
        assert await agent.health() is False
        with pytest.raises(RuntimeError, match='enforcement unavailable'):
            await agent.loop(interval=.01)
        assert credential not in store.desired(int(time.time()))
        store.close()

    asyncio.run(scenario())


def test_owned_process_restart_closes_open_connections(tmp_path):
    """A disposable listener models an established tunnel in a child process."""
    async def scenario():
        api_port, inbound_port = free_port(), free_port()
        path = config_file(tmp_path, api_port, inbound_port)
        binary = tmp_path / 'fake-xray'
        binary.write_text('''#!/usr/bin/env python3
import json, selectors, socket, sys
if '-test' in sys.argv:
    sys.exit(0)
config=json.load(open(sys.argv[-1]))
ports=[int(config['api']['listen'].rsplit(':',1)[1]),config['inbounds'][0]['port']]
selector=selectors.DefaultSelector()
for port in ports:
    sock=socket.socket();sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    sock.bind(('127.0.0.1',port));sock.listen()
    selector.register(sock,selectors.EVENT_READ)
connections=[]
while True:
    for key,_ in selector.select():
        connection,_=key.fileobj.accept();connections.append(connection)
''')
        binary.chmod(0o700)

        class CLI:
            address = f'127.0.0.1:{api_port}'
            tag = 'kuda-managed'
            port = inbound_port
            async def managed_users(self):
                reader, writer = await asyncio.open_connection('127.0.0.1', api_port)
                writer.close()
                await writer.wait_closed()
                return set()

        managed = ManagedXray(str(binary), str(path), CLI())
        await managed.start()
        old = managed.process
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', inbound_port)
            await managed.restart()
            assert old.returncode is not None
            assert managed.process.pid != old.pid
            assert await asyncio.wait_for(reader.read(1), timeout=2) == b''
            writer.close()
            await writer.wait_closed()
        finally:
            await managed.stop()
        assert managed.process is None

    asyncio.run(scenario())


@pytest.mark.skipif(not os.environ.get('TEST_XRAY_BINARY'),
                    reason='Trusted local Xray binary not configured')
def test_real_managed_xray_rehydrates_and_revokes(tmp_path):
    """Disposable real HandlerService; no production host or client traffic."""
    import subprocess

    binary = os.environ['TEST_XRAY_BINARY']
    keys = subprocess.run([binary, 'x25519'], capture_output=True, text=True,
                          check=True, timeout=10).stdout
    private = dict(line.split(':', 1) for line in keys.splitlines() if ':' in line)['PrivateKey'].strip()

    async def scenario():
        api_port, inbound_port = free_port(), free_port()
        path = config_file(tmp_path, api_port, inbound_port)
        config = json.loads(path.read_text())
        config['inbounds'][0]['streamSettings']['realitySettings'] = {
            'dest': 'www.cloudflare.com:443', 'serverNames': ['www.cloudflare.com'],
            'privateKey': private, 'shortIds': ['abcd']}
        path.write_text(json.dumps(config))
        cli = XrayCLI(binary, f'127.0.0.1:{api_port}', 'kuda-managed', inbound_port)
        managed = ManagedXray(binary, str(path), cli)
        store = LeaseStore(str(tmp_path / 'state' / 'leases.db'))
        first, second = uuid4(), uuid4()
        try:
            await managed.start()
            agent = NodeAgent(store, cli, restart_xray=managed.restart)
            now = int(time.time())
            await agent.apply(first, 1, True, now + 90)
            await agent.apply(second, 1, True, now + 90)
            assert await cli.managed_users() == {cli.email(first), cli.email(second)}
            initial_pid = managed.process.pid
            await agent.apply(first, 2, False, None)
            assert managed.process.pid != initial_pid
            assert await cli.managed_users() == {cli.email(second)}
            await managed.stop()
            await managed.start()
            recovered = NodeAgent(store, cli, restart_xray=managed.restart)
            await recovered.reconcile()
            assert await cli.managed_users() == {cli.email(second)}
            await recovered.apply(second, 2, False, None)
            assert await cli.managed_users() == set()
        finally:
            await managed.stop()
            store.close()

    asyncio.run(scenario())
