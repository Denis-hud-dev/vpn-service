"""Disposable loopback-only Xray experiment; never touches JustHost."""
import asyncio
import json
import os
import ssl
import struct
from pathlib import Path
from uuid import uuid4

import pytest

from bot.node_agent.xray_cli import XrayCLI
from tests.test_xray_real import free_port, make_certs, run, wait_tcp

XRAY = os.getenv('TEST_XRAY_BINARY')
pytestmark = pytest.mark.skipif(not XRAY, reason='Trusted local Xray binary not configured')


def private_json(path: Path, payload):
    path.write_text(json.dumps(payload))
    path.chmod(0o600)
    run(XRAY, 'run', '-test', '-c', str(path))


async def process(config):
    proc = await asyncio.create_subprocess_exec(XRAY, 'run', '-c', str(config),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    return proc


async def stop(proc):
    if proc and proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 4)
        except TimeoutError:
            proc.kill()
            await proc.wait()


async def echo(reader, writer):
    try:
        while payload := await reader.read(4096):
            writer.write(payload)
            await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def socks_session(port, destination, ca_path):
    reader, writer = await asyncio.open_connection('127.0.0.1', port)
    writer.write(b'\x05\x01\x00')
    await writer.drain()
    assert await reader.readexactly(2) == b'\x05\x00'
    writer.write(b'\x05\x01\x00\x01\x7f\x00\x00\x01' + struct.pack('!H', destination))
    await writer.drain()
    response = await reader.readexactly(10)
    assert response[:2] == b'\x05\x00'
    context = ssl.create_default_context(cafile=str(ca_path))
    await writer.start_tls(context, server_hostname='localhost')
    return reader, writer


async def round_trip(session, payload):
    reader, writer = session
    writer.write(payload)
    await writer.drain()
    assert await asyncio.wait_for(reader.readexactly(len(payload)), 4) == payload


async def make_server(root, name, *, stats=False):
    api_port, inbound_port = free_port(), free_port()
    keys = dict(line.split(':', 1) for line in run(XRAY, 'x25519').splitlines() if ':' in line)
    private = keys['PrivateKey'].strip()
    public = keys.get('Password (PublicKey)', keys.get('PublicKey', '')).strip()
    assert len(public) == 43
    services = ['HandlerService', 'StatsService'] if stats else ['HandlerService']
    config = {'log': {'loglevel': 'warning', 'access': 'none'},
        'api': {'tag': 'api', 'listen': f'127.0.0.1:{api_port}', 'services': services},
        'inbounds': [{'tag': name, 'listen': '127.0.0.1', 'port': inbound_port,
                      'protocol': 'vless', 'settings': {'decryption': 'none', 'clients': []},
                      'streamSettings': {'network': 'tcp', 'security': 'reality',
                                         'realitySettings': {'dest': 'www.cloudflare.com:443',
                                            'serverNames': ['www.cloudflare.com'],
                                            'privateKey': private, 'shortIds': ['abcd']}}}],
        'outbounds': [{'tag': 'direct', 'protocol': 'freedom'}]}
    if stats:
        config['stats'] = {}
        config['policy'] = {'levels': {'0': {'statsUserUplink': True,
                                           'statsUserDownlink': True}}}
    path = root / f'{name}.json'
    private_json(path, config)
    proc = await process(path)
    await wait_tcp(api_port)
    cli = XrayCLI(XRAY, f'127.0.0.1:{api_port}', name, inbound_port)
    return proc, cli, inbound_port, public


async def make_client(root, name, credential, inbound_port, public):
    socks = free_port()
    config = {'log': {'loglevel': 'warning', 'access': 'none'},
        'inbounds': [{'tag': 'socks', 'listen': '127.0.0.1', 'port': socks,
                      'protocol': 'socks', 'settings': {'auth': 'noauth', 'udp': False}}],
        'outbounds': [{'tag': 'remote', 'protocol': 'vless', 'settings': {'vnext': [
            {'address': '127.0.0.1', 'port': inbound_port, 'users': [
                {'id': str(credential), 'encryption': 'none', 'flow': 'xtls-rprx-vision'}]}]},
            'streamSettings': {'network': 'tcp', 'security': 'reality',
                               'realitySettings': {'serverName': 'www.cloudflare.com',
                                   'fingerprint': 'chrome', 'password': public,
                                   'shortId': 'abcd'}}}]}
    path = root / f'{name}.json'
    private_json(path, config)
    proc = await process(path)
    await wait_tcp(socks)
    return proc, socks


async def stat(cli, credential, direction):
    name = f'user>>>{cli.email(credential)}>>>traffic>>>{direction}'
    raw = await cli._command('stats', '--server='+cli.address, '-name', name)
    payload = json.loads(raw)
    value = payload.get('stat', {}).get('value')
    return int(value) if value is not None else None


def test_two_profiles_shared_xray_and_independent_xray(tmp_path):
    async def scenario():
        make_certs(tmp_path)
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(str(tmp_path/'server.crt'), str(tmp_path/'server.key'))
        origin = await asyncio.start_server(echo, '127.0.0.1', 0, ssl=server_context)
        origin_port = origin.sockets[0].getsockname()[1]
        processes, sessions = [], []
        try:
            # A shared process can keep B alive when RemoveUser(A) is called,
            # but this alone does not prove A's established tunnel is closed.
            common, cli, inbound, public = await make_server(tmp_path, 'common', stats=True)
            processes.append(common)
            a, b = uuid4(), uuid4()
            await cli.add(a)
            await cli.add(b)
            ca, port_a = await make_client(tmp_path, 'client-a', a, inbound, public)
            cb, port_b = await make_client(tmp_path, 'client-b', b, inbound, public)
            processes.extend((ca, cb))
            session_a = await socks_session(port_a, origin_port, tmp_path/'ca.crt')
            session_b = await socks_session(port_b, origin_port, tmp_path/'ca.crt')
            sessions.extend((session_a, session_b))
            await round_trip(session_a, b'A' * 2048)
            await round_trip(session_b, b'B' * 4096)
            # Real per-UUID counters, not fabricated subscription values.
            assert await stat(cli, a, 'uplink') >= 2048
            assert await stat(cli, b, 'uplink') >= 4096
            assert await stat(cli, a, 'downlink') >= 2048
            assert await stat(cli, b, 'downlink') >= 4096
            counters = await cli.user_stats()
            assert counters[a][0] >= 2048 and counters[a][1] >= 2048
            assert counters[b][0] >= 4096 and counters[b][1] >= 4096
            pid = common.pid
            await cli.remove(a)
            assert common.returncode is None and common.pid == pid
            await round_trip(session_b, b'B survives removal')
            # If A's existing stream still works, RemoveUser is insufficient
            # for the requested immediate per-device disconnect.
            a_still_open = False
            try:
                await round_trip(session_a, b'A after removal')
                a_still_open = True
            except (AssertionError, OSError, TimeoutError, asyncio.IncompleteReadError):
                pass
            assert cli.email(a) not in await cli.managed_users()
            assert cli.email(b) in await cli.managed_users()
            # Separate processes: terminating A's process cannot terminate B.
            isolated_a, cli_a, port_isolated_a, public_a = await make_server(tmp_path, 'isolated-a')
            isolated_b, cli_b, port_isolated_b, public_b = await make_server(tmp_path, 'isolated-b')
            processes.extend((isolated_a, isolated_b))
            ia, ib = uuid4(), uuid4()
            await cli_a.add(ia)
            await cli_b.add(ib)
            client_ia, socks_ia = await make_client(tmp_path, 'client-ia', ia, port_isolated_a, public_a)
            client_ib, socks_ib = await make_client(tmp_path, 'client-ib', ib, port_isolated_b, public_b)
            processes.extend((client_ia, client_ib))
            link_ia = await socks_session(socks_ia, origin_port, tmp_path/'ca.crt')
            link_ib = await socks_session(socks_ib, origin_port, tmp_path/'ca.crt')
            sessions.extend((link_ia, link_ib))
            await round_trip(link_ia, b'isolated A before')
            await round_trip(link_ib, b'isolated B before')
            other_pid = isolated_b.pid
            await stop(isolated_a)
            assert isolated_b.returncode is None and isolated_b.pid == other_pid
            await round_trip(link_ib, b'isolated B after A stop')
            try:
                await round_trip(link_ia, b'isolated A after stop')
            except (AssertionError, OSError, TimeoutError, asyncio.IncompleteReadError):
                pass
            else:
                raise AssertionError('Stopped profile A still transferred data')
            # A successful RemoveUser call must not be mistaken for a closed
            # established tunnel. Pin the observed unsafe behavior as a
            # regression guard before any per-device UI can be enabled.
            return a_still_open
        finally:
            for reader, writer in sessions:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 2)
                except (OSError, TimeoutError):
                    pass
            for proc in reversed(processes):
                await stop(proc)
            origin.close()
            await origin.wait_closed()
    a_open = asyncio.run(scenario())
    assert a_open, 'Xray behavior changed: re-evaluate per-UUID active-session termination'
