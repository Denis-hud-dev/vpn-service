"""Calendar entitlements with fake provider traffic and disposable local data."""
import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import func, select

from bot.db.models import Payment, Subscription, SubscriptionHistory, User
from bot.services import PILOT_TARIFFS
from bot.services.payment_events import add_months
from bot.services.payment_notifications import PaymentNotificationProcessor
from bot.services.payments import PaymentCheckoutService
from bot.services.subscriptions import SubscriptionService, as_utc
from bot.subscription_api.app import create_app
from tests.test_yookassa_test_mode import setup


def date(year, month, day):
    return datetime(year, month, day, 15, tzinfo=timezone.utc)


def test_trial_then_two_historical_months_then_199_is_january_once(tmp_path, monkeypatch):
    """A one-month renewal adds to December; it is not a three-month purchase."""
    async def run():
        engine, sessions, fake, client, historical = await setup(tmp_path)
        clock = [date(2026, 10, 21)]
        monkeypatch.setattr('bot.services.subscriptions.utcnow', lambda: clock[0])
        processor = PaymentNotificationProcessor(sessions, client)
        current = PaymentCheckoutService(sessions, client, 'https://checkout.example/checkout',
                                         tariffs=PILOT_TARIFFS)
        try:
            async with sessions.begin() as session:
                owner = await session.scalar(select(User.id).where(User.telegram_id == 501))
                sub = Subscription(user_id=owner, status='active', tariff_id='pilot_trial_24h',
                                   starts_at=date(2026, 10, 20), expires_at=date(2026, 10, 21))
                session.add(sub)
                await session.flush()
                sub_id = sub.id
                SubscriptionService(sessions).history(session, sub, 'pilot_trial_grant')
            provider_ids = []
            steps = [
                (historical, 'month', date(2026, 10, 20), date(2026, 11, 21), 29900),
                (historical, 'month', date(2026, 10, 21), date(2026, 12, 21), 29900),
                (current, 'pilot_month_199', date(2026, 10, 26), date(2027, 1, 21), 19900),
            ]
            async with TestClient(TestServer(create_app(object(), test_payment_processor=processor))) as http:
                for checkout, plan, now, end, amount in steps:
                    clock[0] = now
                    intent = await checkout.create(501, plan, uuid4())
                    provider_ids.append(intent.provider_id)
                    fake.rows[intent.provider_id].update(status='succeeded', paid=True,
                                                        captured_at=now.isoformat())
                    event = {'type': 'notification', 'event': 'payment.succeeded',
                             'object': {'id': intent.provider_id}}
                    for _ in range(3):
                        assert (await http.post('/yookassa/test-webhook', json=event)).status == 200
                    async with sessions() as session:
                        sub = await session.get(Subscription, sub_id)
                        payment = await session.scalar(select(Payment).where(
                            Payment.provider_payment_id == intent.provider_id))
                        assert as_utc(sub.expires_at) == end
                        assert payment.amount_kopecks == amount
                        assert await session.scalar(select(func.count()).select_from(SubscriptionHistory)
                            .where(SubscriptionHistory.payment_id == payment.id)) == 1
                # Late replay of an older successful purchase cannot reset or extend time.
                for provider_id in provider_ids:
                    assert await processor.process(provider_id) == 'succeeded'
            async with sessions() as session:
                assert as_utc((await session.get(Subscription, sub_id)).expires_at) == date(2027, 1, 21)
                assert await session.scalar(select(func.count()).select_from(Subscription)) == 1
                assert await session.scalar(select(func.count()).select_from(SubscriptionHistory)
                    .where(SubscriptionHistory.reason == 'payment')) == 3
        finally:
            await engine.dispose()
    asyncio.run(run())


@pytest.mark.parametrize('previous', [None, 'active', 'expired', 'stale_active'])
@pytest.mark.parametrize('plan,months,amount', [
    ('pilot_month_199', 1, 19900), ('pilot_quarter_499', 3, 49900),
    ('pilot_year_1790', 12, 179000),
])
def test_new_and_renewed_terms_use_calendar_months_and_keep_remaining_time(
        tmp_path, monkeypatch, previous, plan, months, amount):
    async def run():
        engine, sessions, fake, client, _ = await setup(tmp_path)
        now, future = date(2026, 10, 26), date(2026, 12, 31)
        monkeypatch.setattr('bot.services.subscriptions.utcnow', lambda: now)
        checkout = PaymentCheckoutService(sessions, client, 'https://checkout.example/checkout',
                                         tariffs=PILOT_TARIFFS)
        try:
            original = None
            if previous:
                async with sessions.begin() as session:
                    owner = await session.scalar(select(User.id).where(User.telegram_id == 501))
                    sub = Subscription(user_id=owner, tariff_id='month',
                        status='expired' if previous == 'expired' else 'active',
                        starts_at=date(2026, 9, 20),
                        expires_at=future if previous == 'active' else date(2026, 10, 21))
                    session.add(sub)
                    await session.flush()
                    original = sub.id
            intent = await checkout.create(501, plan, uuid4())
            fake.rows[intent.provider_id].update(status='succeeded', paid=True, captured_at=now.isoformat())
            processor = PaymentNotificationProcessor(sessions, client)
            for _ in range(2):
                assert await processor.process(intent.provider_id) == 'succeeded'
            async with sessions() as session:
                sub = await session.scalar(select(Subscription))
                assert sub.status == 'active'
                assert not original or sub.id == original
                assert as_utc(sub.starts_at) == (date(2026, 9, 20) if previous == 'active' else now)
                assert as_utc(sub.expires_at) == add_months(future if previous == 'active' else now, months)
                payment = await session.scalar(select(Payment))
                assert payment.amount_kopecks == amount and payment.tariff_id == plan
                assert await session.scalar(select(func.count()).select_from(SubscriptionHistory)
                    .where(SubscriptionHistory.reason == 'payment')) == 1
        finally:
            await engine.dispose()
    asyncio.run(run())
