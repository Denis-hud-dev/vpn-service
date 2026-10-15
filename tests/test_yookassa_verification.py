import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.db.models import Base
from bot.services import TARIFFS
from bot.services.payment_events import ConfirmedPayment
from bot.services.payments import PaymentRepository
from bot.services.subscriptions import SubscriptionService, as_utc
from bot.services.users import UserService
from bot.services.yookassa_verification import (
    YooKassaPaymentVerifier, confirmed_from_lookup,
)


class FakeLookup:
    def __init__(self, data):
        self.data = data
        self.calls = 0

    async def fetch(self, provider_payment_id):
        self.calls += 1
        return deepcopy(self.data)


def provider_payment(payment_id):
    return {
        'id': 'provider-test-1', 'status': 'succeeded', 'paid': True,
        'metadata': {'order_id': str(payment_id)},
        'amount': {'value': '299.00', 'currency': 'RUB'},
        'captured_at': '2026-01-01T12:00:00Z',
    }


@pytest.mark.parametrize('change', [
    {'status': 'pending'}, {'paid': False}, {'id': ''},
    {'metadata': {'order_id': 'different'}},
    {'amount': {'value': '298.99', 'currency': 'RUB'}},
    {'amount': {'value': '299.001', 'currency': 'RUB'}},
    {'amount': {'value': '299.00', 'currency': 'USD'}},
    {'captured_at': 'invalid'},
])
def test_provider_response_must_be_complete_and_final(change):
    payment_id = uuid4()
    data = provider_payment(payment_id)
    data.update(change)
    if change == {'amount': {'value': '298.99', 'currency': 'RUB'}}:
        # A valid provider amount can be parsed, but cannot match a 299 RUB order.
        assert confirmed_from_lookup(payment_id, data).amount_kopecks == 29899
    else:
        with pytest.raises(ValueError, match='Провайдер не подтвердил'):
            confirmed_from_lookup(payment_id, data)


def test_independent_lookup_required_and_replay_is_idempotent(tmp_path):
    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'payments.db'))
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            await UserService(sessions).register(501, 'Test')
            payment = await PaymentRepository(sessions).create_pending(
                501, TARIFFS['month'], uuid4())
            lookup = FakeLookup(provider_payment(payment.id))
            service = SubscriptionService(sessions, YooKassaPaymentVerifier(lookup))
            event = confirmed_from_lookup(payment.id, lookup.data)
            forged = ConfirmedPayment(event.payment_id, event.provider,
                event.provider_payment_id, event.amount_kopecks + 100,
                event.currency, event.paid_at)
            with pytest.raises(ValueError, match='Провайдер не подтвердил'):
                await service.apply_confirmed_payment(forged)
            first = await service.apply_confirmed_payment(event)
            expiry = first.expires_at
            again = await service.apply_confirmed_payment(event)
            assert again.id == first.id and as_utc(again.expires_at) == as_utc(expiry)
            assert lookup.calls == 3
        finally:
            await engine.dispose()
    asyncio.run(run())
