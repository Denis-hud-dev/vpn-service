"""Owner-only HTTPS review. Auth DB and optional Unix-only sandbox payment facade."""
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


def private_systemd_acl(path, permission):
    """Root-owned systemd copy may authorize the service UID through POSIX ACL."""
    import struct
    try:
        raw=os.getxattr(path,'system.posix_acl_access')
        if len(raw)!=44 or struct.unpack('<I',raw[:4])[0]!=2 or os.geteuid()==0:
            return False
        entries=[struct.unpack('<HHI',raw[i:i+8]) for i in range(4,len(raw),8)]
        acl={(tag,identity):mode for tag,mode,identity in entries}
        unspecified=0xffffffff
        return (len(acl)==5 and set(acl)=={(1,unspecified),(2,os.geteuid()),(4,unspecified),(16,unspecified),(32,unspecified)}
                and acl[1,unspecified]==acl[2,os.geteuid()]==acl[16,unspecified]==permission
                and acl[4,unspecified] in {0,permission} and acl[32,unspecified]==0)
    except (AttributeError,OSError,ValueError,struct.error):
        return False


def sealed_systemd_credential(path, info, parent):
    directory='/run/credentials/kudavpn-website-review.service'
    trusted=(str(path.parent)==directory and os.getenv('CREDENTIALS_DIRECTORY')==directory
            and path.name in {'oidc.json','owner.json'}
            and info.st_uid==parent.st_uid==0
            and info.st_mode & 0o777 == 0o440 and parent.st_mode & 0o777 == 0o550
            and bool(os.statvfs(path).f_flag & os.ST_RDONLY))
    return trusted and (info.st_gid==parent.st_gid==os.getegid()
                        or info.st_gid==parent.st_gid==0 and private_systemd_acl(path,4)
                        and private_systemd_acl(path.parent,5))


def private_file(name):
    path = Path(os.environ[name])
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.parent.is_symlink():
        raise ValueError('Private credential file required')
    info,parent=path.stat(),path.parent.stat()
    ordinary_private=not (info.st_mode & 0o077 or parent.st_mode & 0o077)
    if not (ordinary_private or sealed_systemd_credential(path,info,parent)):
        raise ValueError('Private credential file required')
    return path.read_text().strip()


def settings():
    if os.getenv('KUDAVPN_SITE_MODE') != 'review-test':
        raise ValueError('Website is owner review only; public sales unavailable')
    url = make_url(os.environ['DATABASE_URL'])
    local_peer = url.host is None and dict(url.query) == {'host':'/var/run/postgresql'} and url.password is None
    if (url.drivername != 'postgresql+psycopg' or url.database != 'kudavpn_website_test'
            or not (local_peer or url.host == '127.0.0.1' and not url.query)
            or url.username != 'kudavpn_website_test'):
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
    payment_mode = os.getenv("SITE_PAYMENTS_MODE", "disabled")
    if payment_mode == "owner-sandbox":
        from bot.services.website_payments import PaymentReview, LocalPaymentClient
        service = PaymentReview(snapshot,origin,LocalPaymentClient())
    elif payment_mode != "disabled":
        raise ValueError("Only owner sandbox payments are allowed")
    fast_checkout = os.getenv('SITE_DIRECT_CHECKOUT', '0')
    if fast_checkout not in {'0', '1'}:
        raise ValueError('Explicit direct checkout flag required')
    app = create_website(service,auth,service,origin,closed_review=True,
                         direct_checkout=fast_checkout == '1')

    async def ready(request):
        try:
            await asyncio.wait_for(db.ping(),timeout=3)
            snapshot.read(owner['owner_id'])
            if not service.read_only:
                await asyncio.wait_for(service.payments.request('ready'),timeout=3)
            return web.json_response({'ready':True,'read_only':service.read_only})
        except Exception:
            return web.json_response({'ready':False,'read_only':service.read_only},status=503)
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
