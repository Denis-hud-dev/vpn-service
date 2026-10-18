"""Mutually authenticated HTTPS agent and independent lease enforcement."""
import asyncio
import logging
import time
from uuid import UUID
from aiohttp import web
from bot.node_agent.store import LeaseStore, LeaseConflict
from bot.node_agent.xray_cli import XrayCLI

logger = logging.getLogger(__name__)


class NodeAgent:
    def __init__(self, store: LeaseStore, xray: XrayCLI, *, restart_xray=None,
                 max_users: int = 1000):
        if max_users < 1:
            raise ValueError('Node user limit must be positive')
        self.store, self.xray = store, xray
        self.restart_xray = restart_xray
        self.max_users = max_users
        self.lock = asyncio.Lock()
        self.failed = asyncio.Event()

    async def reconcile(self):
        async with self.lock:
            try:
                await self._reconcile_locked_unlocked()
            except (Exception, asyncio.CancelledError):
                self.failed.set()
                raise

    async def health(self) -> bool:
        # A reachable API is insufficient if Xray still has revoked users.
        async with self.lock:
            if self.failed.is_set():
                return False
            current = await asyncio.wait_for(self.xray.managed_users(), timeout=3)
            wanted = {self.xray.email(value) for value in self.store.desired(int(time.time()))}
            if current != wanted:
                return False
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection('127.0.0.1', self.xray.port), timeout=1)
            writer.close()
            await writer.wait_closed()
            return True

    async def apply(self, credential: UUID, revision: int, enabled: bool, expires_at: int | None):
        now = int(time.time())
        if revision < 1 or (enabled and (expires_at is None or expires_at <= now
                or expires_at > now + 125)) or (not enabled and expires_at is not None):
            raise ValueError('Invalid lease')
        async with self.lock:
            if self.failed.is_set():
                raise RuntimeError('Node enforcement unavailable')
            if (enabled and credential not in self.store.desired(now)
                    and len(self.store.desired(now)) >= self.max_users):
                raise ValueError('Node user limit reached')
            self.store.apply(credential, revision, enabled, expires_at, now)
            try:
                await self._reconcile_locked_unlocked()
            except (Exception, asyncio.CancelledError):
                self.failed.set()
                raise

    async def _reconcile_locked_unlocked(self):
        # Avoid reacquiring the lock from apply().
        current = await self.xray.managed_users()
        wanted = self.store.desired(int(time.time()))
        active = {self.xray.email(value) for value in wanted}
        removed = current - active
        for email in sorted(removed):
            await self.xray.remove(UUID(email.removeprefix('kuda-')))
        if removed and self.restart_xray is not None:
            # RemoveUser rejects new handshakes, but does not establish that
            # existing Vision/TCP sessions have ended. A dedicated Xray process
            # restart closes those sessions before a revocation is ACKed.
            await self.restart_xray()
            current = set()
        missing = sorted((value for value in wanted
                          if self.xray.email(value) not in current), key=str)
        if missing and self.restart_xray is not None:
            await self.xray.add_many(missing)
        else:
            for value in missing:
                await self.xray.add(value)
        if await self.xray.managed_users() != active:
            raise RuntimeError('Xray state not converged')

    async def loop(self, interval=5):
        while True:
            if self.failed.is_set():
                raise RuntimeError('Node enforcement unavailable')
            await self.reconcile()
            try:
                await asyncio.wait_for(self.failed.wait(), timeout=interval)
            except TimeoutError:
                continue


def create_app(agent: NodeAgent):
    @web.middleware
    async def safe_response(request, handler):
        try:
            return await handler(request)
        except (ValueError, LeaseConflict):
            return web.json_response({'applied': False}, status=409)
        except Exception:
            logger.warning('Node operation failed; managed node stopping')
            return web.json_response({'applied': False}, status=503)

    app = web.Application(middlewares=[safe_response], client_max_size=1024)

    async def put_access(request):
        payload = await request.json()
        if (not isinstance(payload, dict) or set(payload) !=
                {'credential', 'revision', 'enabled', 'expires_at'} or
                type(payload['revision']) is not int or type(payload['enabled']) is not bool or
                (payload['expires_at'] is not None and type(payload['expires_at']) is not int)):
            raise ValueError('Invalid request')
        await agent.apply(UUID(payload['credential']), payload['revision'],
                          payload['enabled'], payload['expires_at'])
        return web.json_response({'applied': True}, headers={'Cache-Control': 'no-store'})

    async def get_health(request):
        healthy = await agent.health()
        return web.json_response({'healthy': healthy}, headers={'Cache-Control': 'no-store'})

    app.router.add_put('/v1/access', put_access)
    app.router.add_get('/v1/health', get_health)
    return app
