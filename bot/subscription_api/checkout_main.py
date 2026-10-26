"""Opt-in standalone HTTPS test checkout; never starts a bot or VPN process."""
import asyncio
import logging
import os
from pathlib import Path
import ssl
from urllib.parse import urlsplit

from aiohttp import web
from sqlalchemy.engine import make_url

from bot.db import Database
from bot.services import PILOT_TARIFFS
from bot.services.checkout_sessions import CheckoutSessions
from bot.services.happ import HappService
from bot.services.payment_notifications import PaymentNotificationProcessor
from bot.services.payment_polling import TestPaymentPoller, run_test_payment_poller
from bot.services.payment_refunds import RefundReviews, TestRefundProcessor
from bot.services.payments import PaymentCheckoutService
from bot.services.web_checkout import WebCheckout
from bot.services.yookassa_api import YooKassaTestClient
from bot.subscription_api.checkout_web import create_checkout_app


def settings():
    if os.getenv('KUDAVPN_CHECKOUT_MODE') != 'test-owner-only':
        raise ValueError('Standalone checkout is test-only')
    database_url = os.environ['DATABASE_URL']
    database = make_url(database_url)
    if (database.drivername != 'postgresql+psycopg' or database.host != '127.0.0.1'
            or database.database != 'kudavpn_telegram_pilot'
            or database.username != 'kudavpn_pilot' or database.port not in (None, 5432)):
        raise ValueError('Isolated test database required')
    origin = os.environ['CHECKOUT_PUBLIC_ORIGIN'].rstrip('/')
    if urlsplit(origin).scheme != 'https':
        raise ValueError('HTTPS required')
    host = os.getenv('CHECKOUT_BIND_HOST', '127.0.0.1')
    if host not in ('127.0.0.1', '0.0.0.0'):
        raise ValueError('Explicit IPv4 bind address required')
    port = int(os.getenv('CHECKOUT_PORT', '18543'))
    if not 1024 <= port <= 65535 or port in (8443, 9443, 10443, 18443):
        raise ValueError('Dedicated non-VPN checkout port required')
    if urlsplit(origin).port != port:
        raise ValueError('Checkout origin must match the dedicated TLS port')
    return database_url, origin, host, port


def build():
    database_url, origin, _, _ = settings()
    shop_file = Path(os.environ['PILOT_YOOKASSA_TEST_SHOP_ID_FILE'])
    if shop_file.read_text().strip() != '1503208':
        raise ValueError('Only the approved test shop is allowed')
    owner = int(os.environ['PILOT_TRIAL_OWNER_ID'])
    sessions = CheckoutSessions(os.environ['CHECKOUT_SESSION_FILE'], owner)
    reviews = RefundReviews(sessions)
    db = Database(database_url)
    client = YooKassaTestClient.from_private_files(
        str(shop_file), os.environ['PILOT_YOOKASSA_TEST_SECRET_FILE'])
    if not client._secret_key.startswith('test_'):
        raise ValueError('Only a test API secret is allowed')
    checkout = PaymentCheckoutService(db.sessions, client, origin + '/checkout', tariffs=PILOT_TARIFFS)
    processor = PaymentNotificationProcessor(db.sessions, client)
    happ = HappService(db.sessions, os.environ['SUBSCRIPTION_PUBLIC_URL'],
                       link_key=os.environ['HAPP_LINK_SECRET'], trial_owner_id=owner, trial_node_port=10443)
    service = WebCheckout(db.sessions, checkout, processor, happ, owner, reviews=reviews)
    app = create_checkout_app(service, sessions, origin,
                             refund_processor=TestRefundProcessor(db.sessions, client, reviews))

    async def readiness(request):
        try:
            await asyncio.wait_for(db.ping(), timeout=3)
            return web.json_response({'ready': True, 'test': True})
        except Exception:
            return web.json_response({'ready': False, 'test': True}, status=503)
    app.router.add_get('/ready', readiness)

    async def lifecycle(app):
        await db.check()
        poller = TestPaymentPoller(db.sessions, client)
        task = asyncio.create_task(run_test_payment_poller(poller))
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.close()
    app.cleanup_ctx.append(lifecycle)
    return app


def main():
    logging.basicConfig(level=logging.WARNING)
    from bot.observability import SafeDependencyLogs
    for handler in logging.getLogger().handlers:
        handler.addFilter(SafeDependencyLogs())
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(os.environ['CHECKOUT_TLS_CERT'], os.environ['CHECKOUT_TLS_KEY'])
    _, _, host, port = settings()
    # Public binding is explicit and authorized only for the isolated test.
    web.run_app(build(), host=host, port=port,
                ssl_context=tls, access_log=None, print=None)


if __name__ == '__main__':
    main()
