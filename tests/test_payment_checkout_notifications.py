"""No real YooKassa requests or customer data are used in these tests."""
import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.db.models import Base, Payment, Subscription, SubscriptionHistory
from bot.services.payments import PaymentCheckoutService, PaymentIntent
from bot.services.payment_notifications import PaymentNotificationProcessor
from bot.services.users import UserService


class FakeGateway:
    def __init__(self):
        self.calls = []
        self.url = 'https://yoomoney.ru/checkout/test'

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return PaymentIntent('provider-' + str(kwargs['order_id']), self.url)


class FakeLookup:
    def __init__(self):
        self.rows = {}
        self.calls = 0

    async def fetch(self, provider_id):
        self.calls += 1
        return deepcopy(self.rows[provider_id])


def provider_row(payment, status='succeeded'):
    return {
        'id': payment.provider_payment_id,
        'status': status,
        'paid': status == 'succeeded',
        'metadata': {'order_id': str(payment.id)},
        'amount': {'value': f'{payment.amount_kopecks / 100:.2f}', 'currency': 'RUB'},
        'captured_at': '2026-10-15T00:00:00Z',
    }


async def setup(tmp_path):
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'checkout.db'))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    await UserService(sessions).register(501, 'Test')
    await UserService(sessions).register(502, 'Other')
    return engine, sessions


def test_offline_order_and_verified_status_lifecycle(tmp_path):
    async def run():
        engine, sessions = await setup(tmp_path)
        try:
            gateway = FakeGateway()
            checkout = PaymentCheckoutService(sessions, gateway, 'https://example.org/return')
            key = uuid4()
            first = await checkout.create(501, 'month', key)
            repeat = await checkout.create(501, 'month', key)
            assert first == repeat and gateway.calls[0]['idempotency_key'] == key
            assert gateway.calls[0]['order_id'] == gateway.calls[1]['order_id']
            with pytest.raises(ValueError, match='Ключ идемпотентности'):
                await checkout.create(502, 'month', key)
            with pytest.raises(ValueError, match='Ключ идемпотентности'):
                await checkout.create(501, 'year', key)
            async with sessions() as session:
                payment = await session.scalar(select(Payment).where(Payment.idempotency_key == key))
                assert payment.provider_payment_id == first.provider_id
                assert await session.scalar(select(func.count()).select_from(Payment)) == 1

            lookup = FakeLookup()
            processor = PaymentNotificationProcessor(sessions, lookup)
            lookup.rows[first.provider_id] = provider_row(payment, 'pending')
            assert await processor.process(first.provider_id) == 'pending'
            async with sessions() as session:
                assert await session.scalar(select(func.count()).select_from(Subscription)) == 0
            lookup.rows[first.provider_id] = provider_row(payment)
            assert await processor.process(first.provider_id) == 'succeeded'
            async with sessions() as session:
                sub = await session.scalar(select(Subscription))
                expiry = sub.expires_at
                assert sub.status == 'active'
            assert await processor.process(first.provider_id) == 'succeeded'
            async with sessions() as session:
                sub = await session.scalar(select(Subscription))
                assert sub.expires_at == expiry
                assert await session.scalar(select(func.count()).select_from(SubscriptionHistory).where(
                    SubscriptionHistory.reason == 'payment')) == 1

            renewal = await checkout.create(501, 'month', uuid4())
            async with sessions() as session:
                renewal_payment = await session.scalar(select(Payment).where(
                    Payment.provider_payment_id == renewal.provider_id))
            lookup.rows[renewal.provider_id] = provider_row(renewal_payment)
            assert await processor.process(renewal.provider_id) == 'succeeded'
            async with sessions() as session:
                sub = await session.scalar(select(Subscription))
                assert sub.expires_at > expiry
                assert await session.scalar(select(func.count()).select_from(SubscriptionHistory).where(
                    SubscriptionHistory.reason == 'payment')) == 2

            failed = await checkout.create(501, 'year', uuid4())
            async with sessions() as session:
                failed_payment = await session.scalar(select(Payment).where(
                    Payment.provider_payment_id == failed.provider_id))
            lookup.rows[failed.provider_id] = provider_row(failed_payment, 'canceled')
            assert await processor.process(failed.provider_id) == 'canceled'
            assert await processor.process(failed.provider_id) == 'canceled'
            async with sessions() as session:
                assert (await session.get(Payment, failed_payment.id)).status == 'canceled'
                assert (await session.scalar(select(Subscription))).expires_at > expiry
            assert lookup.calls >= 7  # each success also gets independent verification
        finally:
            await engine.dispose()
    asyncio.run(run())


@pytest.mark.parametrize('mutation', [
    lambda row: row.update(id='other-provider'),
    lambda row: row['metadata'].update(order_id=str(uuid4())),
    lambda row: row['amount'].update(value='1.00'),
    lambda row: row.update(status='succeeded', paid=False),
])
def test_untrusted_or_mismatched_provider_state_cannot_activate(tmp_path, mutation):
    async def run():
        engine, sessions = await setup(tmp_path)
        try:
            gateway = FakeGateway()
            intent = await PaymentCheckoutService(sessions, gateway, 'https://example.org/return').create(
                501, 'month', uuid4())
            async with sessions() as session:
                payment = await session.scalar(select(Payment))
            row = provider_row(payment)
            mutation(row)
            lookup = FakeLookup()
            lookup.rows[intent.provider_id] = row
            with pytest.raises(ValueError):
                await PaymentNotificationProcessor(sessions, lookup).process(intent.provider_id)
            async with sessions() as session:
                assert (await session.get(Payment, payment.id)).status == 'pending'
                assert await session.scalar(select(func.count()).select_from(Subscription)) == 0
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_cancel_does_not_revive_and_checkout_url_is_restricted(tmp_path):
    async def run():
        engine, sessions = await setup(tmp_path)
        try:
            gateway = FakeGateway()
            checkout = PaymentCheckoutService(sessions, gateway, 'https://example.org/return')
            gateway.url = 'https://yoomoney.ru.evil.example/pay'
            with pytest.raises(ValueError, match='адрес оплаты'):
                await checkout.create(501, 'month', uuid4())
            async with sessions() as session:
                assert (await session.scalar(select(Payment))).provider_payment_id is None
            gateway.url = 'https://yoomoney.ru/checkout/test'
            intent = await checkout.create(501, 'month', uuid4())
            async with sessions() as session:
                payment = await session.scalar(select(Payment).where(
                    Payment.provider_payment_id == intent.provider_id))
            lookup = FakeLookup()
            lookup.rows[intent.provider_id] = provider_row(payment, 'canceled')
            processor = PaymentNotificationProcessor(sessions, lookup)
            assert await processor.process(intent.provider_id) == 'canceled'
            lookup.rows[intent.provider_id] = provider_row(payment)
            with pytest.raises(ValueError):
                await processor.process(intent.provider_id)
            async with sessions() as session:
                assert await session.scalar(select(func.count()).select_from(Subscription)) == 0
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_gateway_failure_retries_same_order_without_second_charge(tmp_path):
    async def run():
        engine, sessions = await setup(tmp_path)
        try:
            class FlakyGateway(FakeGateway):
                async def create(self, **kwargs):
                    result = await super().create(**kwargs)
                    if len(self.calls) == 1:
                        raise TimeoutError('test gateway timeout')
                    return result

            gateway = FlakyGateway()
            checkout = PaymentCheckoutService(sessions, gateway, 'https://example.org/return')
            key = uuid4()
            with pytest.raises(TimeoutError):
                await checkout.create(501, 'month', key)
            intent = await checkout.create(501, 'month', key)
            assert intent.provider_id.startswith('provider-')
            assert gateway.calls[0]['order_id'] == gateway.calls[1]['order_id']
            assert gateway.calls[0]['idempotency_key'] == gateway.calls[1]['idempotency_key']
            async with sessions() as session:
                assert await session.scalar(select(func.count()).select_from(Payment)) == 1
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_bound_payment_cannot_activate_another_users_unbound_order(tmp_path):
    async def run():
        from bot.services import TARIFFS
        from bot.services.payments import PaymentRepository
        engine,sessions=await setup(tmp_path)
        try:
            checkout=PaymentCheckoutService(sessions,FakeGateway(),'https://example.org/return')
            intent=await checkout.create(501,'month',uuid4())
            other=await PaymentRepository(sessions).create_pending(502,TARIFFS['month'],uuid4())
            async with sessions() as session:
                bound=await session.scalar(select(Payment).where(Payment.provider_payment_id==intent.provider_id))
            lookup=FakeLookup();lookup.rows[intent.provider_id]=provider_row(bound)
            lookup.rows[intent.provider_id]['metadata']['order_id']=str(other.id)
            with pytest.raises(ValueError):
                await PaymentNotificationProcessor(sessions,lookup).process(intent.provider_id)
            async with sessions() as session:
                assert await session.scalar(select(func.count()).select_from(Subscription))==0
                assert await session.scalar(select(func.count()).select_from(SubscriptionHistory))==0
                assert (await session.get(Payment,bound.id)).status=='pending'
                other_row=await session.get(Payment,other.id)
                assert other_row.status=='pending' and other_row.provider_payment_id is None
        finally:await engine.dispose()
    asyncio.run(run())
