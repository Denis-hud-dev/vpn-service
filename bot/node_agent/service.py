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
    def __init__(self, store: LeaseStore, xray: XrayCLI):
        self.store, self.xray = store, xray
        self.lock = asyncio.Lock()

    async def reconcile(self):
        async with self.lock:
            await self._reconcile_locked_unlocked()

    async def health(self) -> bool:
        # Management connectivity plus the actual VLESS listener. This does not
        # claim end-to-end reachability from any particular client network.
        await asyncio.wait_for(self.xray.managed_users(), timeout=3)
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
            self.store.apply(credential, revision, enabled, expires_at, now)
            await self._reconcile_locked_unlocked()

    async def _reconcile_locked_unlocked(self):
        # Avoid reacquiring the lock from apply().
        current = await self.xray.managed_users()
        wanted = self.store.desired(int(time.time()))
        active = {self.xray.email(value) for value in wanted}
        for email in sorted(current - active):
            await self.xray.remove(UUID(email.removeprefix('kuda-')))
        for value in sorted(wanted, key=str):
            if self.xray.email(value) not in current:
                await self.xray.add(value)
        if await self.xray.managed_users() != active:
            raise RuntimeError('Xray state not converged')

    async def loop(self, interval=5):
        while True:
            try:
                await self.reconcile()
            except Exception:
                logger.warning('Node state reconciliation failed; retry scheduled')
            await asyncio.sleep(interval)


def create_app(agent: NodeAgent):
    @web.middleware
    async def safe_response(request, handler):
        try:
            return await handler(request)
        except (ValueError, LeaseConflict):
            return web.json_response({'applied': False}, status=409)
        except Exception:
            logger.warning('Node operation failed; retry scheduled')
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
