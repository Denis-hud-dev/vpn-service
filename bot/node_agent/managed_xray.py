"""Own the disposable, dedicated Xray process used by a managed VPN node.

The existing static JustHost listener must run in a different service.  A
systemd unit with KillMode=control-group is required so a SIGKILL of the agent
also kills its Xray child.
"""
import asyncio
import errno
import json
import socket
from pathlib import Path

from bot.node_agent.xray_cli import XrayCLI, XrayCommandError


def validate_managed_config(path: str, cli: XrayCLI,
                            reserved_ports: set[int] | None = None) -> None:
    file = Path(path)
    if (file.is_symlink() or not file.is_file() or file.stat().st_mode & 0o077):
        raise ValueError('Managed Xray config must be a private regular file')
    try:
        config = json.loads(file.read_text())
        inbounds = config['inbounds']
        inbound, = inbounds
        api = config['api']
        settings = inbound['settings']
        stream = inbound['streamSettings']
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError('Invalid managed Xray config') from None
    if (not isinstance(config, dict) or not isinstance(inbound, dict)
            or inbound.get('tag') != cli.tag or inbound.get('port') != cli.port
            or inbound.get('protocol') != 'vless'
            or not isinstance(settings, dict)
            or set(settings) != {'decryption', 'clients'}
            or settings.get('clients') != []
            or settings.get('decryption') != 'none'
            or not isinstance(stream, dict) or stream.get('network') != 'tcp'
            or stream.get('security') != 'reality'
            or not isinstance(api, dict) or api.get('listen') != cli.address
            or api.get('services') != ['HandlerService']
            or not isinstance(config.get('log'), dict)
            or config['log'].get('access') != 'none'
            or cli.port in ({443, 8443} | (reserved_ports or set()))):
        raise ValueError('Managed Xray config is not isolated or starts with users')


class ManagedXray:
    def __init__(self, binary: str, config: str, cli: XrayCLI,
                 *, reserved_ports: set[int] | None = None):
        self.binary, self.config, self.cli = binary, config, cli
        # Existing listeners cannot be opted out of protection by an env typo.
        self.reserved_ports = {443, 8443} | (reserved_ports or set())
        self.process = None

    async def start(self):
        if self.process is not None:
            raise RuntimeError('Managed Xray already started')
        validate_managed_config(self.config, self.cli, self.reserved_ports)
        # Never attach to an already-running Xray API (in particular the static
        # 8443 service) if the operator accidentally reuses a port.
        api_port = int(self.cli.address.rsplit(':', 1)[1])
        for family, address, port in (
                (socket.AF_INET, '127.0.0.1', api_port),
                (socket.AF_INET, '0.0.0.0', self.cli.port),
                (socket.AF_INET6, '::', self.cli.port)):
            try:
                with socket.socket(family, socket.SOCK_STREAM) as sock:
                    # A previous managed process can leave accepted TCP sockets
                    # in TIME_WAIT after a revoke-triggered restart.
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    sock.bind((address, port))
            except OSError as exc:
                if family == socket.AF_INET6 and exc.errno in (
                        errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL):
                    continue
                raise RuntimeError('Managed Xray port is already occupied') from None
        check = await asyncio.create_subprocess_exec(
            self.binary, 'run', '-test', '-c', self.config,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            if await asyncio.wait_for(check.wait(), timeout=10) != 0:
                raise RuntimeError('Managed Xray config rejected')
        except (TimeoutError, asyncio.CancelledError) as exc:
            check.kill()
            await check.wait()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise RuntimeError('Managed Xray config check timed out') from None
        self.process = await asyncio.create_subprocess_exec(
            self.binary, 'run', '-c', self.config,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            for _ in range(100):
                if self.process.returncode is not None:
                    raise RuntimeError('Managed Xray exited before readiness')
                try:
                    users = await asyncio.wait_for(self.cli.managed_users(), timeout=1)
                    if users:
                        raise RuntimeError('Managed Xray did not start empty')
                    if self.process.returncode is not None:
                        raise RuntimeError('Managed Xray exited before readiness')
                    return
                except (XrayCommandError, OSError, TimeoutError):
                    await asyncio.sleep(.1)
            raise RuntimeError('Managed Xray API unavailable')
        except BaseException:
            await self.stop()
            raise

    async def stop(self):
        process, self.process = self.process, None
        if process is None:
            return
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(process.wait(), timeout=4)
        except TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()

    async def restart(self):
        # A restart closes every established session of this managed inbound.
        # Healthy users reconnect with unchanged UUIDs after reconciliation.
        await self.stop()
        await self.start()
