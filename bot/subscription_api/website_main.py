"""Owner-only HTTPS review. Auth DB only, no payments or canonical DB writes."""
import asyncio
import logging
import json
import os
from pathlib import Path
import ssl

from aiohttp import web
from sqlalchemy import text
from sqlalchemy.engine import make_url

from bot.db import Database
from bot.services.website_auth import TelegramOIDC
from bot.services.website_review import ReadOnlyReview, ReviewAuth, ReviewSnapshot
from bot.subscription_api.website import create_website

ORIGIN = 'https://r1276248.s2578.vm.mydomain.zone:20443'
FEED_ORIGIN = 'https://r1276248.s2578.vm.mydomain.zone:18443'


def private_file(name):
    path = Path(os.environ[name])
    if (not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077
            or path.parent.is_symlink() or path.parent.stat().st_mode & 0o077):
        raise ValueError('Private credential file required')
    return path.read_text().strip()


def settings():
    if os.getenv('KUDAVPN_SITE_MODE') != 'review-test':
        raise ValueError('Website is owner review only; public sales unavailable')
    url = make_url(os.environ['DATABASE_URL'])
    if (url.drivername != 'postgresql+psycopg' or url.database != 'kudavpn_website_test'
            or url.host != '127.0.0.1' or url.username != 'kudavpn_website_test'):
        raise ValueError('Separate website auth PostgreSQL required')
    if os.environ['SITE_PUBLIC_ORIGIN'] != ORIGIN:
        raise ValueError('Only the approved review origin is allowed')
    port = int(os.getenv('SITE_PORT', '18106'))
    public = os.getenv('SITE_APPROVED_PUBLIC_REVIEW') == '1'
    if (public and port != 20443) or (not public and port != 18106):
        raise ValueError('Dedicated approved review port required')
    if any(os.environ.get(k) for k in ('SITE_TEST_SECRET_FILE','SITE_TEST_SHOP_FILE','SITE_HAPP_SECRET_FILE',
                                     'HAPP_LINK_SECRET','BOT_TOKEN','YOOKASSA_SECRET_KEY')):
        raise ValueError('Review process must not receive canonical or payment secrets')
    return url, ORIGIN, port, '0.0.0.0' if public else '127.0.0.1'


def build():
    url, origin, _, _ = settings()
    db = Database(url.render_as_string(hide_password=False))
    owner = json.loads(private_file('SITE_REVIEW_OWNER_FILE'))
    if set(owner) != {'owner_id'}:
        raise ValueError('Trusted private owner configuration required')
    credentials = json.loads(private_file('SITE_OIDC_BUNDLE_FILE'))
    if (credentials.get('origin') != origin or credentials.get('redirect_uri') != origin+'/auth/callback'):
        raise ValueError('OIDC origin mismatch')
    oidc = TelegramOIDC(credentials['client_id'],credentials['client_secret'],origin+'/auth/callback')
    auth = ReviewAuth(db.sessions,oidc,allowed_telegram_ids={owner['owner_id']})
    snapshot = ReviewSnapshot(os.environ['SITE_SNAPSHOT_FILE'],owner['owner_id'],FEED_ORIGIN)
    service = ReadOnlyReview(snapshot,origin)
    app = create_website(service,auth,service,origin,closed_review=True)

    async def ready(request):
        try:
            await asyncio.wait_for(db.ping(),timeout=3)
            snapshot.read(owner['owner_id'])
            return web.json_response({'ready':True,'read_only':True})
        except Exception:
            return web.json_response({'ready':False,'read_only':True},status=503)
    app.router.add_get('/ready',ready)

    async def lifecycle(app):
        async with db.engine.connect() as connection:
            if await connection.scalar(text('SELECT version_num FROM alembic_version')) != '0009_website':
                raise RuntimeError('Apply website migration only to the separate auth database')
        try:
            yield
        finally:
            await db.close()
    app.cleanup_ctx.append(lifecycle)
    return app


def main():
    logging.basicConfig(level=logging.WARNING)
    from bot.observability import SafeDependencyLogs
    for handler in logging.getLogger().handlers:
        handler.addFilter(SafeDependencyLogs())
    _, _, port, host = settings()
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(os.environ['SITE_TLS_CERT'],os.environ['SITE_TLS_KEY'])
    web.run_app(build(),host=host,port=port,ssl_context=tls,access_log=None,print=None)


if __name__ == '__main__':
    main()
