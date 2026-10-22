"""Use the pinned Xray HandlerService CLI over node-local loopback only."""
import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from uuid import UUID


class XrayCommandError(RuntimeError):
    pass


class XrayCLI:
    def __init__(self, binary: str, api_address: str, inbound_tag: str,
                 inbound_port: int, flow: str = 'xtls-rprx-vision'):
        if not re.fullmatch(r'127\.0\.0\.1:\d{1,5}', api_address):
            raise ValueError('Xray API must bind loopback')
        if not 1 <= int(api_address.rsplit(':', 1)[1]) <= 65535:
            raise ValueError('Invalid Xray API port')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', inbound_tag):
            raise ValueError('Invalid managed inbound tag')
        if not 1 <= inbound_port <= 65535:
            raise ValueError('Invalid inbound port')
        if flow not in ('xtls-rprx-vision', ''):
            raise ValueError('Unsupported VLESS flow')
        self.binary, self.address, self.tag, self.port = binary, api_address, inbound_tag, inbound_port
        self.flow = flow

    async def _command(self, *args):
        try:
            proc = await asyncio.create_subprocess_exec(self.binary, 'api', *args,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=4)
            except TimeoutError:
                proc.kill()
                await proc.communicate()
                raise XrayCommandError('Xray API timeout') from None
        except OSError:
            raise XrayCommandError('Xray binary unavailable') from None
        if proc.returncode != 0 or len(stdout) > 1024 * 1024:
            # Xray stdout/stderr may include account credentials.
            raise XrayCommandError('Xray API operation failed')
        return stdout.decode('utf-8')

    @staticmethod
    def email(credential: UUID):
        return 'kuda-' + str(credential)

    async def managed_users(self):
        raw = await self._command('inbounduser', '--server='+self.address,
                                  '-tag='+self.tag)
        try:
            payload = json.loads(raw)
            # HandlerService serializes an empty list as `{}`. Missing users
            # therefore means empty only when the top-level value is an object.
            if not isinstance(payload, dict):
                raise ValueError()
            users = payload.get('users', [])
            if not isinstance(users, list):
                raise ValueError()
            result = set()
            for user in users:
                email = user['email']
                if (not isinstance(email, str) or
                        not re.fullmatch(r'kuda-[0-9a-f-]{36}', email) or
                        self.email(UUID(email.removeprefix('kuda-'))) != email):
                    # This process is dedicated and starts with no static users.
                    # An unexpected account must make the node unhealthy.
                    raise ValueError()
                result.add(email)
            return result
        except (ValueError, TypeError, KeyError):
            raise XrayCommandError('Invalid Xray API response') from None

    async def add(self, credential: UUID):
        await self.add_many([credential])

    async def add_many(self, credentials):
        # adu reads a normal Xray config. Create it in a private temporary file,
        # keep command output only in memory, then unlink it in all cases.
        credentials = list(credentials)
        if not credentials:
            return
        payload = {'inbounds': [{'tag': self.tag, 'port': self.port,
            'protocol': 'vless', 'settings': {'decryption': 'none',
                'clients': [{'id': str(credential), 'email': self.email(credential),
                             'flow': self.flow} for credential in credentials]}}]}
        path = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', prefix='kuda-xray-user-',
                                             suffix='.json', delete=False) as file:
                path = file.name
                json.dump(payload, file)
            os.chmod(path, 0o600)
            await self._command('adu', '--server='+self.address, path)
        finally:
            if path:
                Path(path).unlink(missing_ok=True)

    async def remove(self, credential: UUID):
        await self._command('rmu', '--server='+self.address,
                            '-tag='+self.tag, self.email(credential))

    async def user_stats(self) -> dict[UUID, tuple[int | None, int | None]]:
        """Read per-user counters locally without putting UUIDs in argv/logs.

        Missing directions remain None, not zero. StatsService and per-user
        policy must be enabled on a dedicated test process before use.
        """
        raw = await self._command('statsquery', '--server='+self.address,
                                  '-pattern', 'user>>>kuda-')
        try:
            payload = json.loads(raw)
            stats = payload.get('stat', [])
            if not isinstance(stats, list):
                raise ValueError()
            values: dict[UUID, list[int | None]] = {}
            for item in stats:
                name, value = item['name'], item['value']
                match = re.fullmatch(r'user>>>(kuda-[0-9a-f-]{36})>>>traffic>>>(uplink|downlink)', name)
                if (match is None or type(value) is not int or value < 0):
                    raise ValueError()
                credential = UUID(match.group(1).removeprefix('kuda-'))
                if self.email(credential) != match.group(1):
                    raise ValueError()
                pair = values.setdefault(credential, [None, None])
                index = 0 if match.group(2) == 'uplink' else 1
                if pair[index] is not None:
                    raise ValueError()
                pair[index] = value
            return {credential: (pair[0], pair[1]) for credential, pair in values.items()}
        except (ValueError, TypeError, KeyError, AttributeError):
            raise XrayCommandError('Invalid Xray statistics response') from None
