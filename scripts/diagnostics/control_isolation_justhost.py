"""One-shot, loopback-only Xray isolation probe for an already owned test VPS.

Run over verified SSH as ``python3 - < this_file``. It creates disposable
configs under a private temporary directory, never uses systemd, public ports,
production UUIDs or subscriptions, and always stops its own child processes.
Output contains only aggregate counters, PIDs and memory figures.
"""
import asyncio
import json
import os
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

XRAY = '/usr/local/bin/xray'


def command(*args):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=10, check=False)
    if result.returncode:
        raise RuntimeError('isolated Xray command failed')
    return result.stdout


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def config(path, data):
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    command(XRAY, 'run', '-test', '-c', str(path))


async def ready(address):
    for _ in range(80):
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', address)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            await asyncio.sleep(.05)
    raise RuntimeError('disposable listener did not start')


async def stop(process):
    if process.returncode is None:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 3)
        except TimeoutError:
            process.kill()
            await process.wait()


async def echo(reader, writer):
    try:
        while data := await reader.read(8192):
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def server(root, label, credential):
    api, inbound = port(), port()
    keys = dict(line.split(':', 1) for line in command(XRAY, 'x25519').splitlines()
                if ':' in line)
    public = keys.get('Password (PublicKey)', keys.get('PublicKey', '')).strip()
    if len(public) != 43:
        raise RuntimeError('unexpected local Xray key format')
    tag = 'probe-' + label
    payload = {
        'log': {'loglevel': 'warning', 'access': 'none'},
        'api': {'tag': 'api', 'listen': f'127.0.0.1:{api}',
                'services': ['StatsService']},
        'stats': {},
        'policy': {'levels': {'0': {'statsUserUplink': True,
                                    'statsUserDownlink': True}}},
        'inbounds': [{
            'tag': tag, 'listen': '127.0.0.1', 'port': inbound,
            'protocol': 'vless', 'settings': {'decryption': 'none', 'clients': [{
                'id': str(credential), 'email': 'kuda-' + str(credential),
                'flow': 'xtls-rprx-vision'}]},
            'streamSettings': {'network': 'tcp', 'security': 'reality',
                'realitySettings': {'dest': 'www.cloudflare.com:443',
                    'serverNames': ['www.cloudflare.com'],
                    'privateKey': keys['PrivateKey'].strip(),
                    'shortIds': ['abcd']}}}],
        'outbounds': [{'tag': 'direct', 'protocol': 'freedom'}]}
    path = root / f'{label}-server.json'
    config(path, payload)
    proc = await asyncio.create_subprocess_exec(XRAY, 'run', '-c', str(path),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await ready(api)
    return proc, inbound, api, public


async def shared_server(root, credentials):
    """One disposable inbound with two users; no production listener involved."""
    api, inbound = port(), port()
    keys = dict(line.split(':', 1) for line in command(XRAY, 'x25519').splitlines()
                if ':' in line)
    public = keys.get('Password (PublicKey)', keys.get('PublicKey', '')).strip()
    if len(public) != 43:
        raise RuntimeError('unexpected local Xray key format')
    payload = {
        'log': {'loglevel': 'warning', 'access': 'none'},
        'api': {'tag': 'api', 'listen': f'127.0.0.1:{api}',
                'services': ['HandlerService', 'StatsService']},
        'stats': {},
        'policy': {'levels': {'0': {'statsUserUplink': True,
                                    'statsUserDownlink': True}}},
        'inbounds': [{
            'tag': 'probe-shared', 'listen': '127.0.0.1', 'port': inbound,
            'protocol': 'vless', 'settings': {'decryption': 'none',
                'clients': [{'id': str(credential), 'email': email,
                             'flow': 'xtls-rprx-vision'}
                            for email, credential in credentials.items()]},
            'streamSettings': {'network': 'tcp', 'security': 'reality',
                'realitySettings': {'dest': 'www.cloudflare.com:443',
                    'serverNames': ['www.cloudflare.com'],
                    'privateKey': keys['PrivateKey'].strip(),
                    'shortIds': ['abcd']}}}],
        'outbounds': [{'tag': 'direct', 'protocol': 'freedom'}]}
    path = root / 'shared-server.json'
    config(path, payload)
    proc = await asyncio.create_subprocess_exec(XRAY, 'run', '-c', str(path),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await ready(api)
    return proc, inbound, api, public


async def client(root, label, credential, inbound, public):
    socks = port()
    payload = {
        'log': {'loglevel': 'warning', 'access': 'none'},
        'inbounds': [{'tag': 'socks', 'listen': '127.0.0.1', 'port': socks,
                      'protocol': 'socks', 'settings': {'auth': 'noauth', 'udp': False}}],
        'outbounds': [{'tag': 'remote', 'protocol': 'vless', 'settings': {'vnext': [{
            'address': '127.0.0.1', 'port': inbound, 'users': [{
                'id': str(credential), 'encryption': 'none',
                'flow': 'xtls-rprx-vision'}]}]},
            'streamSettings': {'network': 'tcp', 'security': 'reality',
                'realitySettings': {'serverName': 'www.cloudflare.com',
                    'fingerprint': 'chrome', 'password': public,
                    'shortId': 'abcd'}}}]}
    path = root / f'{label}-client.json'
    config(path, payload)
    proc = await asyncio.create_subprocess_exec(XRAY, 'run', '-c', str(path),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await ready(socks)
    return proc, socks


async def session(socks, target, cert):
    reader, writer = await asyncio.open_connection('127.0.0.1', socks)
    writer.write(b'\x05\x01\x00')
    await writer.drain()
    assert await reader.readexactly(2) == b'\x05\x00'
    writer.write(b'\x05\x01\x00\x01\x7f\x00\x00\x01' + struct.pack('!H', target))
    await writer.drain()
    assert (await reader.readexactly(10))[:2] == b'\x05\x00'
    await writer.start_tls(ssl.create_default_context(cafile=str(cert)),
                           server_hostname='localhost')
    return reader, writer


async def transfer(link, size):
    reader, writer = link
    payload = b'X' * size
    writer.write(payload)
    await writer.drain()
    assert await asyncio.wait_for(reader.readexactly(size), 4) == payload


def counters(api, credential):
    raw = command(XRAY, 'api', 'statsquery', '--server='+f'127.0.0.1:{api}',
                  '-pattern', 'user>>>kuda-')
    payload = json.loads(raw)
    result = {}
    prefix = f'user>>>kuda-{credential}>>>traffic>>>'
    for item in payload.get('stat', []):
        if item['name'].startswith(prefix):
            result[item['name'].removeprefix(prefix)] = int(item['value'])
    return result


def named_counters(api, email):
    raw = command(XRAY, 'api', 'statsquery', '--server='+f'127.0.0.1:{api}',
                  '-pattern', 'user>>>probe-')
    result = {}
    prefix = f'user>>>{email}>>>traffic>>>'
    for item in json.loads(raw).get('stat', []):
        if item['name'].startswith(prefix):
            result[item['name'].removeprefix(prefix)] = int(item['value'])
    return result


async def grown_counters(api, credential, *, uplink=0, downlink=0,
                         require_downlink=True):
    # StatsService updates may lag the completed application read slightly.
    for _ in range(30):
        sample = counters(api, credential)
        if (sample.get('uplink', 0) > uplink and
                (not require_downlink or sample.get('downlink', 0) > downlink)):
            return sample
        await asyncio.sleep(.1)
    raise RuntimeError(f'per-profile counters did not advance in both directions: '
        f'baseline=({uplink},{downlink}), observed='
        f"({sample.get('uplink')},{sample.get('downlink')})")


def pss_kib(pid):
    # PSS shares common mapped pages fairly; RSS would overstate N-process cost.
    for line in Path(f'/proc/{pid}/smaps_rollup').read_text().splitlines():
        if line.startswith('Pss:'):
            return int(line.split()[1])
    raise RuntimeError('PSS unavailable')


async def main():
    os.umask(0o077)
    if not Path(XRAY).is_file():
        raise RuntimeError('trusted installed Xray binary missing')
    before = {p for p in (443, 8443, 9443, 10443)}
    processes, links = [], []
    with tempfile.TemporaryDirectory(prefix='kudavpn-control-isolation-') as temporary:
        root = Path(temporary)
        command('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                '-days', '1', '-keyout', str(root/'origin.key'),
                '-out', str(root/'origin.crt'), '-subj', '/CN=localhost',
                '-addext', 'subjectAltName=DNS:localhost')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(root/'origin.crt'), str(root/'origin.key'))
        origin = await asyncio.start_server(echo, '127.0.0.1', 0, ssl=context)
        origin_port = origin.sockets[0].getsockname()[1]
        try:
            a_id, b_id = uuid4(), uuid4()
            a, a_port, a_api, a_public = await server(root, 'a', a_id)
            processes.append(a)
            b, b_port, b_api, b_public = await server(root, 'b', b_id)
            processes.append(b)
            ca, a_socks = await client(root, 'a', a_id, a_port, a_public)
            processes.append(ca)
            cb, b_socks = await client(root, 'b', b_id, b_port, b_public)
            processes.append(cb)
            assert not before.intersection({a_port, a_api, b_port, b_api,
                                            a_socks, b_socks, origin_port})
            link_a = await session(a_socks, origin_port, root/'origin.crt')
            link_b = await session(b_socks, origin_port, root/'origin.crt')
            links.extend((link_a, link_b))
            await transfer(link_a, 8192)
            await transfer(link_b, 4096)
            first_a = await grown_counters(a_api, a_id)
            first_b = await grown_counters(b_api, b_id)
            assert first_a['uplink'] >= 8192 and first_a['downlink'] >= 8192
            assert first_b['uplink'] >= 4096 and first_b['downlink'] >= 4096
            await transfer(link_a, 2048)
            second_a = await grown_counters(a_api, a_id,
                uplink=first_a['uplink'], downlink=first_a['downlink'],
                require_downlink=False)
            second_b = counters(b_api, b_id)
            assert second_a['uplink'] > first_a['uplink']
            assert second_a['downlink'] >= first_a['downlink']
            assert second_b == first_b
            await transfer(link_b, 3072)
            third_b = await grown_counters(b_api, b_id,
                uplink=second_b['uplink'], downlink=second_b['downlink'],
                require_downlink=False)
            third_a = counters(a_api, a_id)
            assert third_a == second_a
            assert third_b['uplink'] > second_b['uplink']
            assert third_b['downlink'] >= second_b['downlink']
            memory = {'a_pss_kib': pss_kib(a.pid), 'b_pss_kib': pss_kib(b.pid)}
            b_pid = b.pid
            await stop(a)
            try:
                await transfer(link_a, 1024)
            except (AssertionError, OSError, TimeoutError, asyncio.IncompleteReadError):
                pass
            else:
                raise AssertionError('stopped A tunnel still carries traffic')
            await transfer(link_b, 5120)
            assert b.returncode is None and b.pid == b_pid
            final_b = await grown_counters(b_api, b_id,
                uplink=third_b['uplink'], downlink=third_b['downlink'],
                require_downlink=False)
            assert final_b['uplink'] > third_b['uplink']
            assert final_b['downlink'] >= third_b['downlink']
            # Determine whether the downlink counter catches up on TLS close.
            link_b[1].close()
            await link_b[1].wait_closed()
            after_close_b = final_b
            for _ in range(30):
                after_close_b = counters(b_api, b_id)
                if after_close_b.get('downlink', 0) > final_b['downlink']:
                    break
                await asyncio.sleep(.1)
            print(json.dumps({'result': 'pass', 'separate_tls_sessions': 2,
                'a_active_tunnel_terminated': True,
                'b_active_tunnel_survived': True,
                'b_pid_unchanged': True,
                'counter_isolation': True,
                'downlink_live_growth_seen': {
                    'a': second_a['downlink'] > first_a['downlink'],
                    'b': third_b['downlink'] > second_b['downlink']},
                'downlink_post_close_growth_seen':
                    after_close_b['downlink'] > final_b['downlink'],
                'a_bytes_before_stop': second_a,
                'b_bytes_after_stop': final_b,
                'b_bytes_after_close': after_close_b,
                'memory': memory}, sort_keys=True))
        finally:
            for reader, writer in links:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 2)
                except (OSError, TimeoutError):
                    pass
            for proc in reversed(processes):
                await stop(proc)
            origin.close()
            await origin.wait_closed()


async def shared_main():
    """Reproduce whether RemoveUser closes A while preserving B."""
    os.umask(0o077)
    if not Path(XRAY).is_file():
        raise RuntimeError('trusted installed Xray binary missing')
    processes, links = [], []
    with tempfile.TemporaryDirectory(prefix='kudavpn-control-shared-') as temporary:
        root = Path(temporary)
        command('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                '-days', '1', '-keyout', str(root/'origin.key'),
                '-out', str(root/'origin.crt'), '-subj', '/CN=localhost',
                '-addext', 'subjectAltName=DNS:localhost')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(root/'origin.crt'), str(root/'origin.key'))
        origin = await asyncio.start_server(echo, '127.0.0.1', 0, ssl=context)
        try:
            a_id, b_id = uuid4(), uuid4()
            common, inbound, api, public = await shared_server(root,
                {'probe-a': a_id, 'probe-b': b_id})
            processes.append(common)
            ca, socks_a = await client(root, 'shared-a', a_id, inbound, public)
            cb, socks_b = await client(root, 'shared-b', b_id, inbound, public)
            processes.extend((ca, cb))
            target = origin.sockets[0].getsockname()[1]
            if {inbound, api, socks_a, socks_b, target} & {443, 8443, 9443, 10443}:
                raise RuntimeError('reserved port collision')
            link_a = await session(socks_a, target, root/'origin.crt')
            link_b = await session(socks_b, target, root/'origin.crt')
            links.extend((link_a, link_b))
            await transfer(link_a, 8192)
            await transfer(link_b, 4096)
            first_a, first_b = named_counters(api, 'probe-a'), named_counters(api, 'probe-b')
            if first_a.get('uplink', 0) < 8192 or first_b.get('uplink', 0) < 4096:
                raise RuntimeError('per-user uplink counters unavailable')
            pid = common.pid
            command(XRAY, 'api', 'rmu', '--server='+f'127.0.0.1:{api}',
                    '-tag=probe-shared', 'probe-a')
            users = json.loads(command(XRAY, 'api', 'inbounduser',
                '--server='+f'127.0.0.1:{api}', '-tag=probe-shared')).get('users', [])
            emails = {item['email'] for item in users}
            if emails != {'probe-b'}:
                raise RuntimeError('unexpected admission registry after removal')
            try:
                new_a = await asyncio.wait_for(session(socks_a, target,
                    root/'origin.crt'), 4)
            except (AssertionError, OSError, TimeoutError, ssl.SSLError,
                    asyncio.IncompleteReadError):
                new_a_denied = True
            else:
                new_a[1].close()
                await new_a[1].wait_closed()
                raise RuntimeError('new A connection was accepted after removal')
            # Existing sessions remain independent of the authentication list.
            # This negative proof is essential: a successful API call is not a
            # successful active-session revocation.
            await transfer(link_b, 2048)
            await transfer(link_a, 2048)
            if common.returncode is not None or common.pid != pid:
                raise RuntimeError('shared process unexpectedly restarted')
            after_a, after_b = named_counters(api, 'probe-a'), named_counters(api, 'probe-b')
            if after_a.get('uplink', 0) <= first_a.get('uplink', 0):
                raise RuntimeError('A counter did not advance after removal')
            if after_b.get('uplink', 0) <= first_b.get('uplink', 0):
                raise RuntimeError('B counter did not advance after removal')
            print(json.dumps({
                'result': 'unsafe_for_per_device_revocation',
                'remove_user_call_succeeded': True,
                'new_a_connection_denied': new_a_denied,
                'a_active_tunnel_still_transferred': True,
                'b_active_tunnel_survived': True,
                'shared_pid_unchanged': True,
                'separate_counters_advanced': True,
                'shared_pss_kib': pss_kib(pid),
            }, sort_keys=True))
        finally:
            for reader, writer in links:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 2)
                except (OSError, TimeoutError):
                    pass
            for process in reversed(processes):
                await stop(process)
            origin.close()
            await origin.wait_closed()


if __name__ == '__main__':
    if len(sys.argv) == 1:
        asyncio.run(main())
    elif sys.argv == ['-', 'shared'] or sys.argv[1:] == ['shared']:
        asyncio.run(shared_main())
    else:
        raise SystemExit('usage: python3 - [shared]')
