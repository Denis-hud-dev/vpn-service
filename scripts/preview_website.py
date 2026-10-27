"""Loopback-only visual demo; no secrets, real payments, nodes or server access."""
import argparse
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

from aiohttp import web
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from bot.db.models import Base, User
from bot.services import PILOT_TARIFFS
from bot.services.payment_notifications import PaymentNotificationProcessor
from bot.services.payments import PaymentCheckoutService
from bot.services.subscriptions import utcnow, SubscriptionService
from bot.services.users import UserService
from bot.services.website import NPDLedger, WebsiteCheckout
from bot.services.website_auth import WebsiteAuth
from bot.subscription_api.connect_page import render_connection_page
from bot.subscription_api.website import DEMO_COOKIE, create_website
from tests.test_yookassa_test_mode import FakeYooKassa
from bot.services.yookassa_api import YooKassaTestClient


async def build(port, directory):
    origin = f'http://127.0.0.1:{port}'
    engine = create_async_engine('sqlite+aiosqlite:///'+str(directory/'demo.sqlite'))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    db = async_sessionmaker(engine, expire_on_commit=False)
    user = await UserService(db).register(501, 'Demo')
    # No OIDC provider is configured in the demo: its separate local route grants
    # only this disposable fixture session. The review app has no such bypass.
    auth = WebsiteAuth(db, None)
    fake = FakeYooKassa()
    provider = YooKassaTestClient('1503208', 'offline-fixture', transport=fake)
    checkout = PaymentCheckoutService(db, provider, 'https://demo.invalid/account', tariffs=PILOT_TARIFFS)
    processor = PaymentNotificationProcessor(db, provider)
    async def link(owner): return origin+'/__demo__/feed'
    async def nodes(owner): return ('Демонстрационный узел · имитация',)
    happ = SimpleNamespace(issue_for_user=link, available_node_names_for_user=nodes,
                           connection_page_url=lambda url:url)
    service, ledger = WebsiteCheckout(db, checkout, processor, happ, None), NPDLedger(db)
    app = create_website(service, auth, ledger, origin, local_demo=True)

    @web.middleware
    async def demo(request, handler):
        if request.path == '/auth/login':
            return web.Response(status=303, headers={'Location':'/__demo__/login'})
        response = await handler(request)
        if request.path in ('/', '/account') and response.status == 200:
            html = response.body.decode().replace('Оплаты тестовые · реальные продажи выключены',
                'Локальная имитация аккаунта, оплаты и VPN · реальных списаний нет')
            response.body = html.encode()
        if request.path == '/api/orders' and response.status == 200:
            data = json.loads(response.body)
            if 'payment_url' in data:
                data['payment_url'] = origin+'/__demo__/pay/'+data['id']
            # Preserve all security headers from the website middleware.
            response.body = json.dumps(data).encode()
        if request.path == '/connect' and response.status == 200:
            response.body = response.body.decode().replace('<body>',
                '<body><p role="status">ЛОКАЛЬНОЕ ДЕМО: ссылка и QR — заглушки, не импортируйте их в Happ.</p>').encode()
        return response
    # First middleware wraps the security middleware; demo HTML is static and
    # keeps the original policy, rather than enabling unsafe inline scripts.
    app.middlewares.insert(0,demo)

    async def login(request):
        token = await auth.new_session(user.id)
        response = web.Response(status=303, headers={'Location':'/account'})
        response.set_cookie(DEMO_COOKIE,token,httponly=True,samesite='Lax',path='/',max_age=3600)
        return response

    async def pay_page(request):
        owner = await auth.owner(request.cookies.get(DEMO_COOKIE,''))
        row = await service.order(owner,request.match_info['order'])
        return web.Response(text='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/assets/base.css"><script src="/__demo__/pay.js" defer></script><main class="shell"><p class="eyebrow">ТОЛЬКО ЛОКАЛЬНАЯ ИМИТАЦИЯ</p><h1>Проверка сценария.</h1><p>Это не ЮKassa. Данные карты и банковские приложения не используются.</p><p>Сумма: '''+str(row.amount_kopecks//100)+''' ₽</p><form><button class="button green" value="succeeded">Имитировать успех</button> <button class="button secondary" value="canceled">Имитировать отмену</button></form></main></html>''',content_type='text/html')

    async def outcome(request):
        token = request.cookies.get(DEMO_COOKIE,'')
        owner = await auth.owner(token)
        import hmac
        if request.headers.get('Origin') != origin or not hmac.compare_digest(
                request.headers.get('X-CSRF-Token',''),auth.csrf(token)):
            raise web.HTTPForbidden()
        row = await service.order(owner,request.match_info['order'])
        data = await request.json()
        if data.get('outcome') not in {'succeeded','canceled'}:
            raise ValueError()
        fake.rows[row.provider_payment_id].update(status=data['outcome'],paid=data['outcome']=='succeeded',captured_at=utcnow().isoformat())
        await processor.process(row.provider_payment_id)
        await ledger.tick()
        return web.json_response({'ok':True})

    async def pay_js(request):
        return web.Response(text="document.querySelector('form').addEventListener('submit',async e=>{e.preventDefault();const s=await(await fetch('/api/session')).json();const r=await fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':s.csrf},body:JSON.stringify({outcome:e.submitter.value})});if(r.ok)location.href='/account';});",content_type='application/javascript')

    async def connection(request):
        owner = await auth.owner(request.cookies.get(DEMO_COOKIE,''))
        snapshot = await SubscriptionService(db).snapshot(owner)
        if snapshot.status != 'active':
            raise web.HTTPForbidden()
        html = render_connection_page(origin+'/__demo__/feed',int(snapshot.expires_at.timestamp()))
        html = html.replace('<body>','<body><p role="status">ЛОКАЛЬНОЕ ДЕМО: ссылка и QR — заглушки, не импортируйте их в Happ.</p>')
        return web.Response(text=html,content_type='text/html')

    async def feed(request):
        await auth.owner(request.cookies.get(DEMO_COOKIE,''))
        return web.Response(text='Демонстрация. Настоящих UUID/подписки нет. Не импортируйте в Happ.',content_type='text/plain')

    app.router.add_get('/__demo__/login',login)
    app.router.add_get('/__demo__/pay/{order}',pay_page)
    app.router.add_post('/__demo__/pay/{order}',outcome)
    app.router.add_get('/__demo__/pay.js',pay_js)
    app.router.add_get('/__demo__/happ',connection)
    app.router.add_get('/__demo__/feed',feed)
    async def close(app):await engine.dispose()
    app.on_cleanup.append(close)
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=18106)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='kudavpn-site-demo-') as directory:
        web.run_app(build(args.port,Path(directory)),host='127.0.0.1',port=args.port,access_log=None,print=None)


if __name__ == '__main__':main()
