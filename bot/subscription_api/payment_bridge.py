"""Unix-only, peer-UID authenticated facade over the existing canonical lifecycle.

No public listener, browser tokens, arbitrary owner IDs, webhook or migrations.
The existing checkout remains the only pending-payment polling worker.
"""
import asyncio
from collections import deque
import json
import logging
import os
from pathlib import Path
import pwd
import socket
import struct
import time
from uuid import UUID

from aiohttp import web
from sqlalchemy import text
from sqlalchemy.engine import make_url

from bot.db import Database
from bot.services import PILOT_TARIFFS
from bot.services.payments import PaymentCheckoutService
from bot.services.payment_notifications import PaymentNotificationProcessor
from bot.services.website_payments import CanonicalPaymentCheckout, SOCKET
from bot.services.web_checkout import OrderUnavailable
from bot.services.yookassa_api import YooKassaTestClient

ORIGIN = 'https://r1276248.s2578.vm.mydomain.zone:20443'


def peer_uid(request):
    sock = request.transport.get_extra_info('socket') if request.transport else None
    if sock is None or sock.family != socket.AF_UNIX or not hasattr(socket, 'SO_PEERCRED'):
        raise PermissionError()
    return struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]


def create_bridge(service, owner, allowed_uid, *, peer=peer_uid):
    lock = asyncio.Lock()
    calls = deque()

    @web.middleware
    async def private(request, handler):
        try:
            if peer(request) != allowed_uid:
                raise PermissionError()
            return await handler(request)
        except PermissionError:
            return web.json_response({'error':'forbidden'}, status=403)
        except OrderUnavailable:
            return web.json_response({'error':'order_unavailable'}, status=404)
        except (ValueError, TypeError, KeyError):
            return web.json_response({'error':'invalid_request'}, status=400)
        except Exception:
            # Never log DSNs, payment URLs, response payloads or request values.
            return web.json_response({'error':'temporarily_unavailable'}, status=503)

    app = web.Application(middlewares=[private], client_max_size=1024)

    async def rpc(request):
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError()
        action = data.get('action')
        fields = {'ready':set(), 'latest':set(), 'create':{'tariff_id'}, 'state':{'order_id'}, 'refresh':{'order_id'}}
        if action not in fields or set(data) != {'action'} | fields[action]:
            raise ValueError()
        if action == 'create':
            if data['tariff_id'] not in PILOT_TARIFFS:
                raise ValueError()
            now = time.monotonic()
            while calls and calls[0] <= now-60:
                calls.popleft()
            if len(calls)>=6:
                raise ValueError()
            calls.append(now)
            async with lock:
                result = await service.create(owner, data['tariff_id'])
        elif action == 'ready':
            result = await service.ready()
        elif action == 'latest':
            result = {'order':await service.latest(owner)}
        else:
            UUID(data['order_id'])
            result = await getattr(service, action)(owner, data['order_id'])
        return web.json_response(result, headers={'Cache-Control':'no-store'})

    app.router.add_post('/rpc', rpc)
    return app


def validate_configuration(data):
    if set(data) != {'owner_id','database_url','shop_id','secret_key'}:
        raise ValueError('Minimal isolated payment credentials required')
    url = make_url(data['database_url'])
    if (url.drivername!='postgresql+psycopg' or url.host!='127.0.0.1'
            or url.username!='kudavpn_pilot' or url.database!='kudavpn_telegram_pilot'
            or url.port not in (None,5432) or url.query
            or type(data['owner_id']) is not int or data['owner_id']<=0
            or data['shop_id']!='1503208' or not isinstance(data['secret_key'],str)
            or not data['secret_key'].startswith('test_') or any(c.isspace() for c in data['secret_key'])):
        raise ValueError('Approved test shop and canonical test database only')
    return data


def build():
    if os.environ.get('KUDAVPN_PAYMENT_BRIDGE_MODE')!='owner-sandbox':
        raise ValueError('Explicit owner-only sandbox required')
    directory = os.environ.get('CREDENTIALS_DIRECTORY','')
    if directory!='/run/credentials/kudavpn-site-payments.service':
        raise ValueError('Sealed systemd credentials required')
    credential = Path(directory)/'payments.json'
    from bot.subscription_api.website_main import private_systemd_acl
    info, parent = credential.lstat(), credential.parent.lstat()
    if (os.geteuid() != pwd.getpwnam('kudavpn_site_payments').pw_uid
            or credential.is_symlink() or info.st_uid != 0 or parent.st_uid != 0
            or info.st_mode & 0o777 != 0o440 or parent.st_mode & 0o777 != 0o550
            or not os.statvfs(credential).f_flag & os.ST_RDONLY
            or not (info.st_gid == parent.st_gid == os.getegid()
                or info.st_gid == parent.st_gid == 0 and private_systemd_acl(credential,4)
                    and private_systemd_acl(credential.parent,5))):
        raise ValueError('Private sealed credentials required')
    data = validate_configuration(json.loads(credential.read_text()))
    db = Database(data['database_url'])
    client = YooKassaTestClient(data['shop_id'], data['secret_key'])
    checkout = PaymentCheckoutService(db.sessions, client, ORIGIN+'/account', tariffs=PILOT_TARIFFS)
    service = CanonicalPaymentCheckout(db.sessions, checkout, PaymentNotificationProcessor(db.sessions,client),data['owner_id'])
    app = create_bridge(service, data['owner_id'], pwd.getpwnam('kudavpn_website_test').pw_uid)

    async def lifecycle(app):
        # Read schema version, never upgrade the existing canonical database.
        async with db.engine.connect() as connection:
            if await connection.scalar(text('SELECT version_num FROM alembic_version'))!='0008_premium_notices':
                raise RuntimeError('Unexpected canonical test schema')
        try:
            yield
        finally:
            await db.close()
    app.cleanup_ctx.append(lifecycle)
    return app


def main():
    logging.disable(logging.CRITICAL)
    os.umask(0o007)
    try:
        web.run_app(build(), path=SOCKET, access_log=None, print=None)
    except Exception:
        raise SystemExit('Isolated payment bridge unavailable; details withheld') from None


if __name__=='__main__':
    main()
