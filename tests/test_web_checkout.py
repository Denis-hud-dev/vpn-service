"""Standalone checkout integration: isolated DB, mocked YooKassa, no real charges."""
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from bot.db.models import Payment, Subscription, SubscriptionHistory, NodeAccess
from bot.services import PILOT_TARIFFS, TARIFFS
from bot.services.checkout_sessions import CheckoutSessions
from bot.services.payment_notifications import PaymentNotificationProcessor
from bot.services.payment_events import add_months
from bot.services.payment_polling import TestPaymentPoller as PaymentPoller
from bot.services.payment_refunds import RefundReviews, TestRefundProcessor as RefundProcessor
from bot.services.payments import PaymentCheckoutService
from bot.services.subscriptions import utcnow, as_utc
from bot.services.web_checkout import WebCheckout
from bot.services.yookassa_api import PaymentProviderUnavailable
from bot.subscription_api.checkout_web import create_checkout_app
from tests.test_yookassa_test_mode import setup

ORIGIN = 'https://checkout.example'


@asynccontextmanager
async def portal(tmp_path):
    engine, db, fake, client, _ = await setup(tmp_path)
    sessions = CheckoutSessions(str(tmp_path / 'browser.sqlite'), 501)
    reviews = RefundReviews(sessions)
    checkout = PaymentCheckoutService(db, client, ORIGIN + '/checkout', tariffs=PILOT_TARIFFS)
    processor = PaymentNotificationProcessor(db, client)
    happ = SimpleNamespace(issue_for_user=AsyncMock(return_value='https://vpn.example/s/offline'),
                           connection_page_url=lambda link: 'https://vpn.example/connect/offline')
    service = WebCheckout(db, checkout, processor, happ, 501, reviews=reviews)
    app = create_checkout_app(service, sessions, ORIGIN, local_demo=True,
                             refund_processor=RefundProcessor(db, client, reviews))
    try:
        async with TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True)) as http:
            invite = sessions.invite()
            response = await http.post('/checkout/api/session', json={'token': invite}, headers={'Origin': ORIGIN})
            assert response.status == 200
            csrf = (await response.json())['csrf']
            headers = {'Origin': ORIGIN, 'X-CSRF-Token': csrf}
            yield SimpleNamespace(http=http, headers=headers, db=db, fake=fake, service=service,
                                  sessions=sessions, invite=invite, client=client)
    finally:
        await engine.dispose()


async def create(p, plan='pilot_month_199'):
    response = await p.http.post('/checkout/api/orders', json={'tariff_id': plan}, headers=p.headers)
    assert response.status == 200
    return await response.json()


async def provider_success(p, order):
    async with p.db() as session:
        row = await session.get(Payment, __import__('uuid').UUID(order['id']))
        provider_id = row.provider_payment_id
    p.fake.rows[provider_id].update(status='succeeded', paid=True, captured_at=utcnow().isoformat())
    return provider_id


async def webhook(p, provider_id):
    return await p.http.post('/yookassa/test-webhook', json={
        'type': 'notification', 'event': 'payment.succeeded',
        'object': {'id': provider_id, 'status': 'succeeded', 'paid': True}})


def test_invitation_is_private_single_use_owner_bound_and_expires(tmp_path):
    now = [1000]
    store = CheckoutSessions(str(tmp_path / 'sessions.sqlite'), 501, clock=lambda: now[0])
    invitation = store.invite()
    token = store.exchange(invitation)
    assert store.owner(token) == 501
    with pytest.raises(PermissionError): store.exchange(invitation)
    with pytest.raises(PermissionError): store.owner('forged')
    assert invitation.encode() not in store.path.read_bytes() and token.encode() not in store.path.read_bytes()
    assert store.path.stat().st_mode & 0o077 == 0
    assert CheckoutSessions(str(store.path), 501, clock=lambda: now[0]).owner(token) == 501
    with pytest.raises(PermissionError): CheckoutSessions(str(store.path), 502, clock=lambda: now[0]).owner(token)
    now[0] += 3601
    with pytest.raises(PermissionError): store.owner(token)
    fresh = CheckoutSessions(str(tmp_path / 'expired.sqlite'), 501, clock=lambda: now[0])
    invite = fresh.invite()
    now[0] += 601
    with pytest.raises(PermissionError): fresh.exchange(invite)
    public = tmp_path / 'public'
    public.mkdir(mode=0o755)
    with pytest.raises(ValueError): CheckoutSessions(str(public / 'state.sqlite'), 501)


def test_provider_backoff_recovers_without_losing_pending_order():
    async def run():
        now = [100.0]
        poller = PaymentPoller(None, None, clock=lambda: now[0], jitter=lambda: 0)
        poller.repository.pending_provider_ids = AsyncMock(return_value=['offline-order'])
        poller.processor.process = AsyncMock(side_effect=[PaymentProviderUnavailable(), 'pending'])
        await poller.tick()
        await poller.tick()
        assert poller.processor.process.await_count == 1
        now[0] += 30
        await poller.tick()
        assert poller.processor.process.await_count == 2 and not poller.retry
    asyncio.run(run())


def test_browser_cookie_is_secure_and_http_only_outside_local_demo(tmp_path):
    async def run():
        sessions = CheckoutSessions(str(tmp_path / 'secure.sqlite'), 501)
        app = create_checkout_app(SimpleNamespace(processor=None), sessions, ORIGIN)
        async with TestClient(TestServer(app)) as http:
            response = await http.post('/checkout/api/session', json={'token': sessions.invite()}, headers={'Origin': ORIGIN})
            cookie = response.cookies['kuda_test_checkout']
            assert cookie['secure'] and cookie['httponly'] and cookie['samesite'] == 'Lax'
            assert cookie['path'] == '/checkout'
    asyncio.run(run())


@pytest.mark.parametrize('plan,amount', [('pilot_month_199', 19900), ('pilot_quarter_499', 49900), ('pilot_year_1790', 179000)])
def test_new_purchase_amounts_duplicates_and_connection(tmp_path, plan, amount):
    async def run():
        async with portal(tmp_path) as p:
            order = await create(p, plan)
            assert order['amount_kopecks'] == amount and order['state'] == 'pending'
            assert 'connect_url' not in order and order['receipt'] == 'test_not_fiscal'
            assert order['seller_regime'] == 'npd' and order['vat'] == 'not_applicable'
            retry = await create(p, plan)
            assert retry['id'] == order['id']
            posts = [c for c in p.fake.calls if c[0] == 'POST']
            assert posts[0][2]['amount']['value'] == f'{amount / 100:.2f}'
            assert posts[0][3] == posts[1][3]
            assert 'receipt' not in posts[0][2] and 'save_payment_method' not in posts[0][2]
            provider_id = await provider_success(p, order)
            for _ in range(2): assert (await webhook(p, provider_id)).status == 200
            response = await p.http.get('/checkout/api/orders/' + order['id'])
            data = await response.json()
            assert data['state'] == 'succeeded' and data['connect_url'].endswith('/connect/offline')
            async with p.db() as session:
                assert await session.scalar(select(func.count()).select_from(Subscription)) == 1
                sub = await session.scalar(select(Subscription))
                assert as_utc(sub.expires_at) == add_months(as_utc(sub.starts_at), PILOT_TARIFFS[plan].months)
                assert data['subscription_expires_at'] == as_utc(sub.expires_at).isoformat()
                assert await session.scalar(select(func.count()).select_from(SubscriptionHistory).where(
                    SubscriptionHistory.reason == 'payment')) == 1
            p.service.happ.issue_for_user.side_effect = TimeoutError()
            data = await (await p.http.get('/checkout/api/orders/' + order['id'])).json()
            assert data['state'] == 'succeeded' and data['access_pending'] and 'connect_url' not in data
    asyncio.run(run())


def test_auth_csrf_foreign_order_and_price_tampering(tmp_path):
    async def run():
        async with portal(tmp_path) as p:
            assert (await p.http.post('/checkout/api/session', json={'token': p.invite}, headers={'Origin': ORIGIN})).status == 401
            assert (await p.http.post('/checkout/api/orders', json={'tariff_id': 'pilot_quarter_499'})).status == 403
            assert (await p.http.post('/checkout/api/orders', json={'tariff_id': 'pilot_quarter_499'}, headers={**p.headers, 'Origin': 'https://evil.example'})).status == 403
            for body in ({'tariff_id': 'month'}, {'tariff_id': 'pilot_month_199', 'amount_kopecks': 1}):
                assert (await p.http.post('/checkout/api/orders', json=body, headers=p.headers)).status == 400
            foreign = await p.service.checkout.create_for_user(502, 'pilot_quarter_499')
            async with p.db() as session:
                row = await session.scalar(select(Payment).where(Payment.provider_payment_id == foreign.provider_id))
            assert (await p.http.get('/checkout/api/orders/' + str(row.id))).status == 404
            response = await p.http.get('/checkout')
            assert 'no-store' in response.headers['Cache-Control']
            assert response.headers['Referrer-Policy'] == 'no-referrer'
            assert response.headers['X-Frame-Options'] == 'DENY'
            assert "script-src 'self'" in response.headers['Content-Security-Policy']
            assert (await p.http.post('/checkout/api/logout', headers=p.headers)).status == 200
            assert (await p.http.get('/checkout/api/session')).status == 401
    asyncio.run(run())


@pytest.mark.parametrize('old_plan,new_plan,old_amount,new_amount', [
    ('month', 'pilot_month_199', 29900, 19900),
    ('quarter', 'pilot_quarter_499', 79900, 49900),
    ('year', 'pilot_year_1790', 249000, 179000),
])
def test_repriced_offers_preserve_historical_order_amount_and_term(
        tmp_path, old_plan, new_plan, old_amount, new_amount):
    async def run():
        async with portal(tmp_path) as p:
            legacy = PaymentCheckoutService(p.db, p.client, ORIGIN + '/checkout')
            intent = await legacy.create_for_user(501, old_plan)
            async with p.db() as db:
                old = await db.scalar(select(Payment).where(Payment.provider_payment_id == intent.provider_id))
            new = await create(p, new_plan)
            assert old.amount_kopecks == old_amount and new['amount_kopecks'] == new_amount
            assert str(old.id) != new['id']
            # Old buttons are readable as history, never accepted for a new sale.
            assert (await p.http.post('/checkout/api/orders', json={'tariff_id': old_plan}, headers=p.headers)).status == 400
            state = await p.service.state(501, str(old.id))
            assert state['amount_kopecks'] == old_amount
            assert state['months'] == TARIFFS[old_plan].months
            assert state['label'] == TARIFFS[old_plan].label
            p.fake.rows[intent.provider_id].update(status='succeeded', paid=True, captured_at=utcnow().isoformat())
            assert (await webhook(p, intent.provider_id)).status == 200
            async with p.db() as db:
                sub = await db.scalar(select(Subscription))
                assert as_utc(sub.expires_at) > utcnow() + timedelta(days=27 * TARIFFS[old_plan].months)
                assert (await db.get(Payment, old.id)).amount_kopecks == old_amount
    asyncio.run(run())


def test_restart_recovers_pending_order_session_and_once_only_fulfillment(tmp_path):
    async def run():
        async with portal(tmp_path) as p:
            order = await create(p, 'pilot_quarter_499')
            async with p.db() as db:
                saved = await db.scalar(select(Payment))
                stable_key, provider_id = saved.idempotency_key, saved.provider_payment_id
            cookie = p.http.session.cookie_jar.filter_cookies(p.http.make_url('/checkout'))['kuda_test_checkout'].value
            # A new engine, session ledger and app reuse only durable state.
            # Provider state represents the remote shop, independent of the app.
            engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'payments.db'))
            db = async_sessionmaker(engine, expire_on_commit=False)
            try:
                browser = CheckoutSessions(str(p.sessions.path), 501)
                assert browser.owner(cookie) == 501
                checkout = PaymentCheckoutService(db, p.client, ORIGIN + '/checkout', tariffs=PILOT_TARIFFS)
                processor = PaymentNotificationProcessor(db, p.client)
                service = WebCheckout(db, checkout, processor, p.service.happ, 501)
                assert (await service.latest(501))['id'] == order['id']
                retried = await service.create(501, 'pilot_quarter_499')
                assert retried['id'] == order['id']
                async with db() as session:
                    assert (await session.get(Payment, saved.id)).idempotency_key == stable_key
                await provider_success(p, order)
                # Restarted poller recovers a confirmation missed during downtime.
                await PaymentPoller(db, p.client).tick()
                state = await service.state(501, order['id'])
                assert state['state'] == 'succeeded' and state['subscription_expires_at']
                await PaymentNotificationProcessor(db, p.client).process(provider_id)
                await PaymentPoller(db, p.client).tick()
                assert (await service.state(501, order['id']))['subscription_expires_at'] == state['subscription_expires_at']
                async with db() as session:
                    assert await session.scalar(select(func.count()).select_from(SubscriptionHistory).where(
                        SubscriptionHistory.reason == 'payment')) == 1
                    assert await session.scalar(select(func.count()).select_from(Payment)) == 1
                app = create_checkout_app(service, browser, ORIGIN, local_demo=True)
                async with TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True)) as restarted:
                    restarted.session.cookie_jar.update_cookies({'kuda_test_checkout': cookie})
                    response = await restarted.get('/checkout/api/orders/latest')
                    assert response.status == 200
                    restored = (await response.json())['order']
                    assert restored['id'] == order['id'] and restored['state'] == 'succeeded'
            finally:
                await engine.dispose()
    asyncio.run(run())


def test_api_outage_cancel_and_unverified_success_do_not_activate(tmp_path):
    async def run():
        async with portal(tmp_path) as p:
            order = await create(p)
            provider_id = next(iter(p.fake.rows))
            # Webhook lies about success; independent API still says pending.
            assert (await webhook(p, provider_id)).status == 200
            async with p.db() as session:
                assert await session.scalar(select(func.count()).select_from(Subscription)) == 0
            original = p.service.processor.lookup.fetch
            p.service.processor.lookup.fetch = AsyncMock(side_effect=PaymentProviderUnavailable())
            response = await p.http.post('/checkout/api/orders/' + order['id'] + '/refresh', json={}, headers=p.headers)
            assert response.status == 503 and (await response.json())['error'] == 'provider_unavailable'
            p.service.processor.lookup.fetch = original
            p.fake.rows[provider_id]['status'] = 'canceled'
            assert (await p.http.post('/yookassa/test-webhook', json={
                'type': 'notification', 'event': 'payment.canceled', 'object': {'id': provider_id}})).status == 200
            state = await (await p.http.get('/checkout/api/orders/' + order['id'])).json()
            assert state['state'] == 'canceled' and 'connect_url' not in state
    asyncio.run(run())


def test_expired_order_and_mismatched_amount_then_late_verified_payment(tmp_path):
    async def run():
        async with portal(tmp_path) as p:
            order = await create(p)
            async with p.db.begin() as session:
                row = await session.scalar(select(Payment))
                row.created_at = utcnow() - timedelta(minutes=31)
                provider_id = row.provider_payment_id
            state = await (await p.http.get('/checkout/api/orders/' + order['id'])).json()
            assert state['state'] == 'expired' and 'payment_url' not in state
            p.fake.rows[provider_id].update(status='succeeded', paid=True, captured_at=utcnow().isoformat())
            p.fake.rows[provider_id]['amount']['value'] = '1.00'
            await webhook(p, provider_id)
            async with p.db() as session:
                assert await session.scalar(select(func.count()).select_from(Subscription)) == 0
            p.fake.rows[provider_id]['amount']['value'] = '199.00'
            await webhook(p, provider_id)
            state = await (await p.http.get('/checkout/api/orders/' + order['id'])).json()
            assert state['state'] == 'succeeded'
    asyncio.run(run())


def test_full_refund_is_verified_idempotent_and_preserves_prior_paid_time(tmp_path):
    async def run():
        async with portal(tmp_path) as p:
            first = await create(p)
            await webhook(p, await provider_success(p, first))
            async with p.db() as session:
                before = as_utc((await session.scalar(select(Subscription))).expires_at)
            second = await create(p)
            provider_id = await provider_success(p, second)
            await webhook(p, provider_id)
            p.fake.rows['refund-test'] = {'id': 'refund-test', 'payment_id': provider_id,
                'status': 'succeeded', 'amount': {'value': '199.00', 'currency': 'RUB'}}
            event = {'type': 'notification', 'event': 'refund.succeeded',
                     'object': {'id': 'refund-test', 'payment_id': provider_id}}
            for _ in range(2): assert (await p.http.post('/yookassa/test-webhook', json=event)).status == 200
            await webhook(p, provider_id)  # Late duplicate payment cannot restore refunded time.
            async with p.db() as session:
                sub = await session.scalar(select(Subscription))
                assert as_utc(sub.expires_at) == before and sub.status == 'active'
                assert await session.scalar(select(func.count()).select_from(SubscriptionHistory).where(
                    SubscriptionHistory.reason == 'payment_refund')) == 1
            state = await (await p.http.get('/checkout/api/orders/' + second['id'])).json()
            assert state['state'] == 'refunded' and 'connect_url' not in state
    asyncio.run(run())


def test_partial_refund_requires_review_without_revoking_other_paid_time(tmp_path):
    async def run():
        async with portal(tmp_path) as p:
            order = await create(p)
            provider_id = await provider_success(p, order)
            await webhook(p, provider_id)
            async with p.db() as session:
                before = as_utc((await session.scalar(select(Subscription))).expires_at)
            p.fake.rows['partial'] = {'id': 'partial', 'payment_id': provider_id,
                'status': 'succeeded', 'amount': {'value': '50.00', 'currency': 'RUB'}}
            assert (await p.http.post('/yookassa/test-webhook', json={'type': 'notification',
                'event': 'refund.succeeded', 'object': {'id': 'partial', 'payment_id': provider_id}})).status == 200
            state = await (await p.http.get('/checkout/api/orders/' + order['id'])).json()
            assert state['state'] == 'refund_review' and 'connect_url' not in state
            async with p.db() as session:
                sub = await session.scalar(select(Subscription))
                assert as_utc(sub.expires_at) == before and sub.status == 'active'
    asyncio.run(run())


@pytest.mark.parametrize('state', ['pending', 'canceled', 'wrong-parent', 'succeeded'])
def test_refund_does_not_trust_notification_or_incomplete_provider_state(tmp_path, state):
    async def run():
        async with portal(tmp_path) as p:
            order = await create(p)
            provider_id = await provider_success(p, order)
            await webhook(p, provider_id)
            p.fake.rows['refund-check'] = {'id': 'refund-check',
                'payment_id': 'unrelated' if state == 'wrong-parent' else provider_id,
                'status': 'succeeded' if state == 'wrong-parent' else state,
                'amount': {'value': '199.00', 'currency': 'RUB'}}
            assert (await p.http.post('/yookassa/test-webhook', json={'type': 'notification',
                'event': 'refund.succeeded', 'object': {'id': 'refund-check', 'payment_id': provider_id}})).status in (200, 503)
            async with p.db() as session:
                sub = await session.scalar(select(Subscription))
                row = await session.scalar(select(Payment))
                if state == 'succeeded':
                    assert sub.status == 'expired' and row.status == 'refunded'
                else:
                    assert sub.status == 'active' and row.status == 'succeeded'
    asyncio.run(run())


def test_refund_of_earlier_payment_is_reviewed_without_removing_later_renewal(tmp_path):
    async def run():
        async with portal(tmp_path) as p:
            first = await create(p)
            first_provider = await provider_success(p, first)
            await webhook(p, first_provider)
            second = await create(p, 'pilot_quarter_499')
            await webhook(p, await provider_success(p, second))
            async with p.db() as session:
                end = as_utc((await session.scalar(select(Subscription))).expires_at)
            p.fake.rows['earlier'] = {'id': 'earlier', 'payment_id': first_provider,
                'status': 'succeeded', 'amount': {'value': '199.00', 'currency': 'RUB'}}
            await p.http.post('/yookassa/test-webhook', json={'type': 'notification',
                'event': 'refund.succeeded', 'object': {'id': 'earlier', 'payment_id': first_provider}})
            data = await (await p.http.get('/checkout/api/orders/' + first['id'])).json()
            assert data['state'] == 'refund_review'
            async with p.db() as session:
                assert as_utc((await session.scalar(select(Subscription))).expires_at) == end
    asyncio.run(run())


def test_web_199_purchase_and_renewal_keep_real_issuer_uuid_and_https_link(tmp_path):
    from bot.services.happ import NodeReconciler, AccessDenied
    from bot.services.node_health import NodeHealthMonitor
    from bot.services.users import UserService
    from tests.test_managed_pilot_payment_flow import setup as managed_setup, provider_row

    async def run():
        engine, db, lookup, processor, legacy_checkout, feed, gateway = await managed_setup(tmp_path)
        try:
            await UserService(db).register(501, 'Test')
            checkout = PaymentCheckoutService(db, legacy_checkout.gateway,
                'https://checkout.example/checkout', tariffs=PILOT_TARIFFS)
            website = WebCheckout(db, checkout, processor, feed, 501)
            reconciler, health = NodeReconciler(db, gateway), NodeHealthMonitor(db, gateway)
            original_uuid = original_link = first_expiry = None
            paid = []
            for attempt in range(2):
                order = await website.create(501, 'pilot_month_199')
                async with db() as session:
                    payment = await session.scalar(select(Payment).where(
                        Payment.id == __import__('uuid').UUID(order['id'])))
                    assert payment.amount_kopecks == 19900
                lookup.rows[payment.provider_payment_id] = provider_row(payment)
                lookup.rows[payment.provider_payment_id]['test'] = True
                paid.append(payment)
                await processor.process(payment.provider_payment_id)
                await processor.process(payment.provider_payment_id)
                async with db() as session:
                    sub = await session.scalar(select(Subscription))
                if attempt == 0:
                    gateway.available = False
                    with pytest.raises(RuntimeError, match='incomplete'):
                        await reconciler.reconcile(sub.id)
                    async with db() as session:
                        durable = await session.scalar(select(NodeAccess))
                        pending_uuid = durable.credential
                        assert durable.desired_enabled and not durable.enabled
                    waiting = await website.state(501, order['id'])
                    assert waiting['state'] == 'succeeded' and waiting['access_pending']
                    assert 'connect_url' not in waiting
                    gateway.available = True
                    # A new worker retries the same durable intent after recovery.
                    reconciler = NodeReconciler(db, gateway)
                await reconciler.reconcile(sub.id)
                await reconciler.reconcile(sub.id)
                await health.tick()
                async with db() as session:
                    access = await session.scalar(select(NodeAccess))
                    assert access.credential == pending_uuid
                link = await feed.issue_for_user(501)
                state = await website.state(501, order['id'])
                assert state['state'] == 'succeeded' and '/connect/' in state['connect_url']
                if original_uuid is None:
                    original_uuid, original_link, first_expiry = access.credential, link, as_utc(sub.expires_at)
                else:
                    assert access.credential == original_uuid and link == original_link
                    assert as_utc(sub.expires_at) > first_expiry
            async with db() as session:
                assert await session.scalar(select(func.count()).select_from(Subscription)) == 1
                assert await session.scalar(select(func.count()).select_from(SubscriptionHistory).where(
                    SubscriptionHistory.reason == 'payment')) == 2
            # Refund the latest remaining grant, then the initial one; the real
            # reconciler/issuer deny access without changing the personal UUID.
            reviews = RefundReviews(CheckoutSessions(str(tmp_path / 'refund-sessions.sqlite'), 501))
            for index, payment in enumerate(reversed(paid)):
                refund_id = f'offline-refund-{index}'
                lookup.fetch_refund = AsyncMock(return_value={'id': refund_id,
                    'payment_id': payment.provider_payment_id, 'status': 'succeeded',
                    'amount': {'value': '199.00', 'currency': 'RUB'}})
                assert await RefundProcessor(db, lookup, reviews).process(
                    refund_id, payment.provider_payment_id) == 'refunded'
            await reconciler.reconcile(sub.id)
            await health.tick()
            with pytest.raises(AccessDenied):
                await feed.issue_for_user(501)
            async with db() as session:
                access = await session.scalar(select(NodeAccess))
                assert not access.enabled and access.credential == original_uuid
        finally:
            gateway.close()
            await engine.dispose()
    asyncio.run(run())
