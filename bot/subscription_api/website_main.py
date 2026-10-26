"""Prepared HTTPS review entrypoint. Never enabled by deployment in this PR."""
import asyncio
import logging
import os
from pathlib import Path
import ssl
from urllib.parse import urlsplit

from aiohttp import web
from sqlalchemy import text
from sqlalchemy.engine import make_url

from bot.db import Database
from bot.services import PILOT_TARIFFS
from bot.services.happ import HappService
from bot.services.payment_notifications import PaymentNotificationProcessor
from bot.services.payment_polling import TestPaymentPoller, run_test_payment_poller
from bot.services.payments import PaymentCheckoutService
from bot.services.website import NPDLedger, WebsiteCheckout
from bot.services.website_auth import TelegramOIDC, WebsiteAuth
from bot.services.yookassa_api import YooKassaTestClient
from bot.subscription_api.website import create_website


def private_file(name):
    path = Path(os.environ[name])
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError('Private credential file required')
    return path.read_text().strip()


def settings():
    if os.getenv('KUDAVPN_SITE_MODE') != 'review-test':
        raise ValueError('Website is review-test only; public sales unavailable')
    url = make_url(os.environ['DATABASE_URL'])
    if (url.drivername != 'postgresql+psycopg' or url.database != 'kudavpn_website_test'
            or url.host != '127.0.0.1' or url.username != 'kudavpn_website_test'):
        raise ValueError('Separate disposable website PostgreSQL required')
    origin = os.environ['SITE_PUBLIC_ORIGIN']
    if urlsplit(origin).scheme != 'https' or urlsplit(origin).path or urlsplit(origin).query or urlsplit(origin).fragment:
        raise ValueError('Dedicated HTTPS origin required')
    # Stay loopback-bound until a separate reviewed deployment grants exposure.
    port = int(os.getenv('SITE_PORT', '18106'))
    if port in {443, 8443, 9443, 10443, 18443, 18543} or not 1024 <= port <= 65535:
        raise ValueError('Dedicated non-VPN port required')
    return url, origin, port


def build():
    url, origin, _ = settings()
    shop, secret = private_file('SITE_TEST_SHOP_FILE'), private_file('SITE_TEST_SECRET_FILE')
    if shop != '1503208' or not secret.startswith('test_'):
        raise ValueError('Only the approved test shop is allowed')
    db = Database(str(url.render_as_string(hide_password=False)))
    provider = YooKassaTestClient(shop, secret)
    processor = PaymentNotificationProcessor(db.sessions, provider)
    checkout = PaymentCheckoutService(db.sessions, provider, origin+'/account', tariffs=PILOT_TARIFFS)
    # No node is registered or changed here. Unprovisioned access stays pending.
    happ = HappService(db.sessions, os.environ['SITE_SUBSCRIPTION_ORIGIN'],
                       link_key=private_file('SITE_HAPP_SECRET_FILE'))
    service = WebsiteCheckout(db.sessions, checkout, processor, happ, None)
    oidc = TelegramOIDC(private_file('SITE_OIDC_CLIENT_FILE'), private_file('SITE_OIDC_SECRET_FILE'),
                        origin+'/auth/callback')
    ledger = NPDLedger(db.sessions, test=True)
    app = create_website(service, WebsiteAuth(db.sessions, oidc), ledger, origin)

    async def ready(request):
        try:
            await asyncio.wait_for(db.ping(), timeout=3)
            return web.json_response({'ready': True, 'test': True})
        except Exception:
            return web.json_response({'ready': False, 'test': True}, status=503)
    app.router.add_get('/ready', ready)

    async def lifecycle(app):
        async with db.engine.connect() as connection:
            if await connection.scalar(text('SELECT version_num FROM alembic_version')) != '0009_website':
                raise RuntimeError('Apply website migration to the separate test database')
        task = asyncio.create_task(run_test_payment_poller(TestPaymentPoller(db.sessions, provider)))
        async def receipts():
            while True:
                try:
                    await ledger.tick()
                except Exception:
                    logging.warning('website_receipts outcome=retry')
                await asyncio.sleep(30)
        receipt_task = asyncio.create_task(receipts())
        try:
            yield
        finally:
            task.cancel()
            receipt_task.cancel()
            await asyncio.gather(task, receipt_task, return_exceptions=True)
            await db.close()
    app.cleanup_ctx.append(lifecycle)
    return app


def main():
    logging.basicConfig(level=logging.WARNING)
    from bot.observability import SafeDependencyLogs
    for handler in logging.getLogger().handlers:
        handler.addFilter(SafeDependencyLogs())
    _, _, port = settings()
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(os.environ['SITE_TLS_CERT'], os.environ['SITE_TLS_KEY'])
    web.run_app(build(), host='127.0.0.1', port=port, ssl_context=tls, access_log=None, print=None)


if __name__ == '__main__':
    main()
