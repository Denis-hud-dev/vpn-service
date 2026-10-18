"""Separate local entry point, TLS is mandatory; no production deployment here."""
import asyncio
import logging
import os
import ssl
from aiohttp import web
from bot.db.session import Database
from bot.services.happ import HappService, NodeReconciler
from bot.services.node_health import NodeHealthMonitor
from bot.subscription_api.app import create_app

logger = logging.getLogger(__name__)


def main():
    from bot.observability import SafeDependencyLogs
    logging.basicConfig(level=logging.WARNING)
    for handler in logging.getLogger().handlers:
        handler.addFilter(SafeDependencyLogs())
    database = Database(os.environ['DATABASE_URL'])
    service = HappService(database.sessions, os.environ['SUBSCRIPTION_PUBLIC_URL'])
    from bot.services.node_gateway import HTTPSNodeGateway
    gateway = HTTPSNodeGateway(os.environ['NODE_AGENT_CONFIG'])
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(os.environ['SUBSCRIPTION_TLS_CERT'], os.environ['SUBSCRIPTION_TLS_KEY'])
    app = create_app(service)
    async def lifecycle(app):
        await database.check()
        async def worker(action, failure_message):
            while True:
                try:
                    await action.tick()
                except Exception:
                    logger.warning(failure_message)
                await asyncio.sleep(30)
        tasks = [
            asyncio.create_task(worker(NodeHealthMonitor(database.sessions, gateway),
                                       'Node health monitoring failed; retry scheduled')),
            asyncio.create_task(worker(NodeReconciler(database.sessions, gateway),
                                       'Node reconciliation failed; retry scheduled')),
        ]
        try:yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await database.close()
    app.cleanup_ctx.append(lifecycle)
    web.run_app(app, host=os.getenv('SUBSCRIPTION_BIND_HOST', '127.0.0.1'),
                port=int(os.getenv('SUBSCRIPTION_PORT','18443')),
                ssl_context=tls, access_log=None, print=None)


if __name__ == '__main__':main()
