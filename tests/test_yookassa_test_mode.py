"""Provider traffic is simulated; no real test-shop or production charge occurs."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from aiohttp.test_utils import TestClient, TestServer

from bot.db.models import Base, Payment, Subscription, SubscriptionHistory
from bot.config import Config
from bot.services import Services
from bot.services.payment_polling import TestPaymentPoller as PaymentPoller
from bot.services.payments import PaymentCheckoutService
from bot.services.users import UserService
from bot.services.yookassa_api import PaymentProviderUnavailable, YooKassaTestClient
from bot.services.payment_notifications import PaymentNotificationProcessor
from bot.subscription_api.app import create_app
from tests.fake_telegram import TelegramHarness


class FakeYooKassa:
    def __init__(self):
        self.calls = []
        self.rows = {}

    async def __call__(self, method, path, payload, key):
        self.calls.append((method, path, deepcopy(payload), key))
        if method == 'POST':
            order_id = payload['metadata']['order_id']
            provider_id = 'test-' + order_id
            row = {'id': provider_id, 'test': True, 'status': 'pending', 'paid': False,
                   'metadata': {'order_id': order_id}, 'amount': payload['amount'],
                   'confirmation': {'type': 'redirect',
                                    'confirmation_url': 'https://yoomoney.ru/checkout/test'}}
            self.rows[provider_id] = row
            return deepcopy(row)
        return deepcopy(self.rows[path.rsplit('/', 1)[1]])


async def setup(tmp_path):
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'payments.db'))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    await UserService(sessions).register(501, 'Owner')
    await UserService(sessions).register(502, 'Other')
    fake = FakeYooKassa()
    client = YooKassaTestClient('123456', 'private-test-key', transport=fake)
    checkout = PaymentCheckoutService(sessions, client, 'https://t.me/KudaVPN_Test_Bot')
    return engine, sessions, fake, client, checkout


def test_test_shop_checkout_polling_duplicate_renewal_and_cancellation(tmp_path):
    async def run():
        engine, sessions, fake, client, checkout = await setup(tmp_path)
        try:
            poller = PaymentPoller(sessions, client)
            for tariff_id, amount in [('month', '299.00'), ('quarter', '799.00'), ('year', '2490.00')]:
                intent = await checkout.create_for_user(501, tariff_id)
                assert (await checkout.create_for_user(501, tariff_id)) == intent
                posts = [call for call in fake.calls if call[0] == 'POST']
                assert posts[-1][2]['amount'] == {'value': amount, 'currency': 'RUB'}
                assert posts[-1][2]['metadata']['order_id']
                assert posts[-1][3] is not None  # Idempotence-Key
                assert posts[-1][2]['confirmation']['return_url'] == 'https://t.me/KudaVPN_Test_Bot'
                await poller.tick()
                async with sessions() as session:
                    assert await session.scalar(select(func.count()).select_from(Subscription)) == (0 if tariff_id == 'month' else 1)
                fake.rows[intent.provider_id].update(
                    status='succeeded', paid=True, captured_at=datetime.now(timezone.utc).isoformat())
                await poller.tick()
                async with sessions() as session:
                    sub = await session.scalar(select(Subscription))
                    assert sub.status == 'active'
                    expiry = sub.expires_at
                    history_count = await session.scalar(select(func.count()).select_from(
                        SubscriptionHistory).where(SubscriptionHistory.reason == 'payment'))
                await poller.tick()
                async with sessions() as session:
                    assert (await session.scalar(select(Subscription))).expires_at == expiry
                    assert await session.scalar(select(func.count()).select_from(
                        SubscriptionHistory).where(SubscriptionHistory.reason == 'payment')) == history_count
            assert history_count == 3
            canceled = await checkout.create_for_user(501, 'month')
            fake.rows[canceled.provider_id].update(status='canceled', paid=False)
            await poller.tick()
            async with sessions() as session:
                payment = await session.scalar(select(Payment).where(
                    Payment.provider_payment_id == canceled.provider_id))
                assert payment.status == 'canceled'
                assert (await session.scalar(select(Subscription))).expires_at == expiry
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_webhook_is_only_a_hint_and_duplicate_does_not_extend(tmp_path):
    async def run():
        engine, sessions, fake, client, checkout = await setup(tmp_path)
        async with TestClient(TestServer(create_app(object(), test_payment_processor=
                                         PaymentNotificationProcessor(sessions, client)))) as http:
            try:
                intent = await checkout.create_for_user(501, 'month')
                fake.rows[intent.provider_id].update(
                    status='succeeded', paid=True, captured_at=datetime.now(timezone.utc).isoformat())
                event = {'type': 'notification', 'event': 'payment.succeeded',
                         'object': {'id': intent.provider_id, 'status': 'canceled', 'paid': False}}
                for _ in range(2):
                    response = await http.post('/yookassa/test-webhook', json=event)
                    assert response.status == 200
                async with sessions() as session:
                    sub = await session.scalar(select(Subscription))
                    assert sub.status == 'active'
                    assert await session.scalar(select(func.count()).select_from(
                        SubscriptionHistory).where(SubscriptionHistory.reason == 'payment')) == 1
                reads = len([call for call in fake.calls if call[0] == 'GET'])
                unknown = await http.post('/yookassa/test-webhook', json={
                    'type': 'notification', 'event': 'payment.succeeded',
                    'object': {'id': 'unbound-provider'}})
                assert unknown.status == 200
                assert len([call for call in fake.calls if call[0] == 'GET']) == reads
                assert (await http.post('/yookassa/test-webhook', json={
                    'type': 'notification', 'event': 'payment.succeeded',
                    'object': {'id': '../unsafe'}})).status == 400
            finally:
                await engine.dispose()
    asyncio.run(run())


def test_webhook_retries_provider_outage_without_grant(tmp_path):
    async def run():
        engine, sessions, fake, client, checkout = await setup(tmp_path)
        intent = await checkout.create_for_user(501, 'month')

        class UnavailableLookup:
            async def fetch(self, provider_id):
                raise PaymentProviderUnavailable('unavailable')

        async with TestClient(TestServer(create_app(object(), test_payment_processor=
                                         PaymentNotificationProcessor(sessions, UnavailableLookup())))) as http:
            response = await http.post('/yookassa/test-webhook', json={
                'type': 'notification', 'event': 'payment.succeeded',
                'object': {'id': intent.provider_id}})
            assert response.status == 503
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(Subscription)) == 0
        await engine.dispose()
    asyncio.run(run())


def test_webhook_is_absent_without_explicit_opt_in():
    async def run():
        async with TestClient(TestServer(create_app(object()))) as http:
            response = await http.post('/yookassa/test-webhook', json={
                'type': 'notification', 'event': 'payment.succeeded',
                'object': {'id': 'any'}})
            assert response.status == 404
    asyncio.run(run())


def test_sbp_request_is_explicit_and_disabled_by_default():
    async def run():
        fake = FakeYooKassa()
        order = uuid4()
        key = uuid4()
        smart = YooKassaTestClient('123456', 'test-secret', transport=fake)
        await smart.create(amount_kopecks=29900, currency='RUB', idempotency_key=key,
                           order_id=order, return_url='https://example.org/return')
        assert 'payment_method_data' not in fake.calls[-1][2]
        sbp = YooKassaTestClient('123456', 'test-secret', transport=fake, payment_method='sbp')
        await sbp.create(amount_kopecks=29900, currency='RUB', idempotency_key=key,
                         order_id=order, return_url='https://example.org/return')
        assert fake.calls[-1][2]['payment_method_data'] == {'type': 'sbp'}
        assert fake.calls[-1][2]['capture'] is True
    asyncio.run(run())


def test_test_marker_mismatch_never_activates_and_bot_is_owner_only(tmp_path):
    async def run():
        engine, sessions, fake, client, checkout = await setup(tmp_path)
        try:
            services = Services.persistent(sessions)
            services.test_checkout = checkout
            services.test_payment_owner_id = 501
            app = TelegramHarness(services=services)
            try:
                await app.text('/start', user_id=502)
                await app.callback('paytest:month', user_id=502)
                assert not fake.calls
                await app.text('/start', user_id=501)
                await app.callback('tariff:month', user_id=501)
                assert 'Создать тестовый заказ' in str(app.navigation.views[501].screen.keyboard)
                await app.callback('paytest:month', user_id=501)
                assert 'Открыть тестовую оплату' in str(app.navigation.views[501].screen.keyboard)
            finally:
                await app.close()
            provider_id = next(iter(fake.rows))
            fake.rows[provider_id].update(
                test=False, status='succeeded', paid=True,
                captured_at=datetime.now(timezone.utc).isoformat())
            with pytest.raises(PaymentProviderUnavailable):
                await client.fetch(provider_id)
            await PaymentPoller(sessions, client).tick()
            async with sessions() as session:
                assert await session.scalar(select(func.count()).select_from(Subscription)) == 0
                assert (await session.scalar(select(Payment))).status == 'pending'
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_private_files_and_provider_id_guard(tmp_path):
    shop = tmp_path / 'shop-id'
    secret = tmp_path / 'secret'
    shop.write_text('123456\n')
    secret.write_text('test-only-secret\n')
    shop.chmod(0o600)
    secret.chmod(0o644)
    with pytest.raises(ValueError, match='0600'):
        YooKassaTestClient.from_private_files(str(shop), str(secret))
    secret.chmod(0o600)
    client = YooKassaTestClient.from_private_files(str(shop), str(secret))
    assert client.shop_id == '123456'

    async def run():
        with pytest.raises(ValueError):
            await client.fetch('../private')
        with pytest.raises(ValueError):
            await client.create(amount_kopecks=0, currency='RUB',
                                idempotency_key=uuid4(), order_id=uuid4(),
                                return_url='https://t.me/KudaVPN_Test_Bot')
    asyncio.run(run())


def test_payment_opt_in_rejects_non_pilot_database(monkeypatch):
    monkeypatch.setenv('BOT_TOKEN', '123456:offline_test_token')
    monkeypatch.setenv('KUDAVPN_PILOT_MARKER', 'kudavpn-telegram-pilot-v1')
    monkeypatch.setenv('PILOT_TRIAL_OWNER_ID', '501')
    monkeypatch.setenv('PILOT_TEST_PAYMENTS_ENABLED', '1')
    monkeypatch.setenv('PILOT_YOOKASSA_TEST_SHOP_ID_FILE', '/private/test-shop-id')
    monkeypatch.setenv('PILOT_YOOKASSA_TEST_SECRET_FILE', '/private/test-secret')
    monkeypatch.setenv('PILOT_PAYMENT_RETURN_URL', 'https://t.me/KudaVPN_Test_Bot')
    monkeypatch.setenv('SUBSCRIPTION_PUBLIC_URL', 'https://pilot.example.org:18443')
    monkeypatch.setenv('HAPP_LINK_SECRET', 'offline-test-key')
    monkeypatch.setenv('DATABASE_URL', 'postgresql+psycopg://user:pass@db/kudavpn_production')
    with pytest.raises(ValueError, match='изолированный бот'):
        Config.from_env()
    monkeypatch.setenv('DATABASE_URL', 'postgresql+psycopg://user:pass@db/kudavpn_telegram_pilot')
    assert Config.from_env().pilot_test_payments_enabled is True
    monkeypatch.setenv('KUDAVPN_PILOT_MARKER', 'production')
    with pytest.raises(ValueError, match='изолированный бот'):
        Config.from_env()
