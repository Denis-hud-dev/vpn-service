"""Loopback-only visual/E2E demo. All payments, accounts and access are simulated."""
import argparse
import asyncio
import hmac
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

from aiohttp import web
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from bot.db.models import Base
from bot.services import PILOT_TARIFFS
from bot.services.checkout_sessions import CheckoutSessions
from bot.services.payment_notifications import PaymentNotificationProcessor
from bot.services.payment_refunds import RefundReviews, TestRefundProcessor
from bot.services.payments import PaymentCheckoutService
from bot.services.subscriptions import utcnow
from bot.services.users import UserService
from bot.services.web_checkout import WebCheckout
from bot.services.yookassa_api import YooKassaTestClient
from bot.subscription_api.checkout_web import create_checkout_app, COOKIE
from tests.test_yookassa_test_mode import FakeYooKassa


async def build(port, folder):
    origin = f'http://127.0.0.1:{port}'
    engine = create_async_engine('sqlite+aiosqlite:///' + str(folder / 'demo.sqlite'))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    db = async_sessionmaker(engine, expire_on_commit=False)
    await UserService(db).register(501, 'Demo')
    sessions = CheckoutSessions(str(folder / 'sessions.sqlite'), 501)
    fake = FakeYooKassa()
    client = YooKassaTestClient('1503208', 'offline-placeholder', transport=fake)
    checkout = PaymentCheckoutService(db, client, 'https://demo.invalid/checkout', tariffs=PILOT_TARIFFS)
    processor = PaymentNotificationProcessor(db, client)
    async def link(owner): return origin + '/__demo__/happ'
    happ = SimpleNamespace(issue_for_user=link, connection_page_url=lambda link: link)
    reviews = RefundReviews(sessions)
    service = WebCheckout(db, checkout, processor, happ, 501, reviews=reviews)
    app = create_checkout_app(service, sessions, origin, local_demo=True,
        refund_processor=TestRefundProcessor(db, client, reviews))

    @web.middleware
    async def demonstration(request, handler):
        response = await handler(request)
        if request.path == '/checkout' and response.status == 200:
            html = response.body.decode().replace('<body>', '<body><p class="notice">Локальная демонстрация: ЮKassa, покупатель и VPN-доступ имитируются. Реальных списаний и чеков нет.</p>')
            return web.Response(text=html, content_type='text/html')
        if request.path == '/checkout/api/orders' and response.status == 200:
            data = json.loads(response.body)
            if 'payment_url' in data:
                data['payment_url'] = origin + '/__demo__/provider/' + data['id']
            return web.json_response(data)
        return response
    app.middlewares.append(demonstration)

    async def login(request):
        token = sessions.exchange(sessions.invite())
        response = web.Response(status=303, headers={'Location': '/checkout'})
        response.set_cookie(COOKIE, token, httponly=True, samesite='Lax', path='/')
        return response

    async def provider(request):
        token = request.cookies.get(COOKIE, '')
        row = await service.order(sessions.owner(token), request.match_info['order'])
        csrf = sessions.csrf(token)
        return web.Response(text=f'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/checkout/assets/checkout.css"><title>Демонстрация провайдера</title><script src="/__demo__/provider.js" defer></script><main class="shell"><h1>Имитация оплаты</h1><p>Это не сайт ЮKassa. Никакие данные карты вводить не нужно.</p><form method="post"><input type="hidden" name="csrf" value="{csrf}"><button class="button green" name="outcome" value="succeeded">Эмулировать успех</button><button class="button secondary" name="outcome" value="canceled">Эмулировать отмену</button></form><p>Демонстрационная сумма: {row.amount_kopecks / 100:.0f} ₽</p></main></html>''', content_type='text/html')

    async def outcome(request):
        token = request.cookies.get(COOKIE, '')
        row = await service.order(sessions.owner(token), request.match_info['order'])
        data = await request.json()
        if request.headers.get('Origin') != origin or not hmac.compare_digest(data.get('csrf', ''), sessions.csrf(token)):
            raise web.HTTPForbidden()
        state = data.get('outcome')
        if state not in {'succeeded', 'canceled'}: raise ValueError()
        fake.rows[row.provider_payment_id].update(status=state, paid=state == 'succeeded', captured_at=utcnow().isoformat())
        await processor.process(row.provider_payment_id)
        return web.json_response({'ok': True})

    async def provider_js(request):
        return web.Response(text="document.querySelector('form').addEventListener('submit', async event => {event.preventDefault(); const form=event.currentTarget; const response=await fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf:form.elements.csrf.value,outcome:event.submitter.value})}); if(response.ok) location.href='/checkout'; else document.body.append(document.createTextNode('Не удалось подтвердить демонстрацию.'));});", content_type='application/javascript')

    async def happ_demo(request):
        sessions.owner(request.cookies.get(COOKIE, ''))
        return web.Response(text='Локальная демонстрация: реальный профиль/UUID не создаётся. В рабочем тестовом контуре здесь используется существующая персональная страница Happ.', content_type='text/plain')

    app.router.add_get('/__demo__/login', login)
    app.router.add_get('/__demo__/provider/{order}', provider)
    app.router.add_post('/__demo__/provider/{order}', outcome)
    app.router.add_get('/__demo__/happ', happ_demo)
    app.router.add_get('/__demo__/provider.js', provider_js)
    async def close(app): await engine.dispose()
    app.on_cleanup.append(close)
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18105)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='kuda-checkout-preview-') as directory:
        web.run_app(build(args.port, Path(directory)), host='127.0.0.1', port=args.port,
                    access_log=None, print=None)


if __name__ == '__main__': main()
