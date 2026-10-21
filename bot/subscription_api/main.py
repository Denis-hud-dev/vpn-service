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
    trial_owner_id = (int(os.getenv('PILOT_TRIAL_OWNER_ID'))
                      if os.getenv('KUDAVPN_PILOT_MARKER') == 'kudavpn-telegram-pilot-v1'
                      and os.getenv('PILOT_TRIAL_OWNER_ID', '').isascii()
                      and os.getenv('PILOT_TRIAL_OWNER_ID', '').isdigit() else None)
    service = HappService(database.sessions, os.environ['SUBSCRIPTION_PUBLIC_URL'],
                          trial_owner_id=trial_owner_id,
                          trial_node_port=10443 if trial_owner_id else None)
    from bot.services.node_gateway import HTTPSNodeGateway
    gateway = HTTPSNodeGateway(os.environ['NODE_AGENT_CONFIG'])
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(os.environ['SUBSCRIPTION_TLS_CERT'], os.environ['SUBSCRIPTION_TLS_KEY'])
    test_payment_processor = None
    if os.getenv('PILOT_PAYMENT_WEBHOOK_ENABLED') == '1':
        if (os.getenv('KUDAVPN_PILOT_MARKER') != 'kudavpn-telegram-pilot-v1'
                or not trial_owner_id
                or not os.environ['DATABASE_URL'].startswith('postgresql+psycopg://')
                or os.environ['DATABASE_URL'].rsplit('/', 1)[-1] != 'kudavpn_telegram_pilot'):
            raise RuntimeError('Webhook разрешён только для изолированного тестового контура')
        from bot.services.payment_notifications import PaymentNotificationProcessor
        from bot.services.yookassa_api import YooKassaTestClient
        client = YooKassaTestClient.from_private_files(
            os.environ['PILOT_YOOKASSA_TEST_SHOP_ID_FILE'],
            os.environ['PILOT_YOOKASSA_TEST_SECRET_FILE'])
        test_payment_processor = PaymentNotificationProcessor(database.sessions, client)
    app = create_app(service, test_payment_processor=test_payment_processor)
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
            asyncio.create_task(worker(NodeReconciler(database.sessions, gateway,
                                                     trial_owner_id=trial_owner_id,
                                                     trial_node_port=10443 if trial_owner_id else None),
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
