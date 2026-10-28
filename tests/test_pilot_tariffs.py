"""Versioned 199 RUB test tariff; no real Telegram or payment API traffic."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, func

from bot import screens
from bot.services import Services, TARIFFS, PILOT_TARIFFS
from bot.db.models import Payment, SubscriptionHistory
from bot.services.payments import PaymentCheckoutService, PaymentIntent
from bot.services.payment_notifications import PaymentNotificationProcessor
from bot.services.subscriptions import SubscriptionService, as_utc
from tests.fake_telegram import TelegramHarness
from tests.test_payment_checkout_notifications import setup, FakeGateway, FakeLookup, provider_row


def test_pilot_catalog_and_native_styles_do_not_change_legacy_catalog():
    screen = screens.tariffs(premium_account=True, test_payment=True)
    buttons = [b for row in screen.keyboard.inline_keyboard for b in row]
    assert [b.text for b in buttons[:3]] == [
        '1 месяц — 199 ₽', '3 месяца — 499 ₽', '12 месяцев — 1790 ₽']
    assert [b.style for b in buttons[:3]] == ['success', 'primary', 'primary']
    assert '299 ₽' not in screen.text and 'Стартовые' not in screen.text
    assert not any((b.callback_data or '').startswith(('launchplan:', 'nav:launch')) for b in buttons)
    default = screens.tariffs()
    assert '299 ₽' in default.text and '199 ₽' not in default.text
    assert [p.price_rub for p in TARIFFS.values()] == [299, 799, 2490]
    assert [p.price_rub for p in PILOT_TARIFFS.values()] == [199, 499, 1790]
    assert not set(PILOT_TARIFFS).intersection(TARIFFS)
    assert 'Скоро' not in screen.text and 'скоро' not in screen.text


def test_old_tariff_buttons_cannot_create_old_price_order_in_premium():
    async def run():
        services = Services.demo()
        services.premium_account_enabled = True
        services.test_checkout = AsyncMock()
        services.test_checkout.create_for_user.return_value = PaymentIntent(
            'offline', 'https://yoomoney.ru/checkout/test')
        services.test_payment_owner_id = 123
        app = TelegramHarness(services=services)
        try:
            await app.text('/start')
            await app.callback('nav:tariffs')
            for old in ('nav:launch_tariffs', 'launchplan:year'):
                await app.callback(old)
                assert '199 ₽' in app.navigation.views[123].screen.text
            services.test_checkout.create_for_user.assert_not_awaited()
            await app.callback('tariff:pilot_month_199')
            text = app.navigation.views[123].screen.text
            assert '199 ₽' in text and '299 ₽' not in text
            services.test_checkout.create_for_user.assert_not_awaited()
            await app.callback('paytest:pilot_month_199')
            services.test_checkout.create_for_user.assert_not_awaited()
            assert 'Оплата в этом боте' in app.navigation.views[123].screen.text
            assert not any(b.url for row in app.navigation.views[123].screen.keyboard.inline_keyboard for b in row)
            await app.callback('nav:tariffs')
            assert 'Тарифы' in app.navigation.views[123].screen.text
        finally:
            await app.close()
    asyncio.run(run())


def test_tariffs_keep_navigation_when_telegram_rejects_styles():
    async def run():
        services = Services.demo()
        services.premium_account_enabled = True
        app = TelegramHarness(services=services)
        try:
            await app.text('/start')
            app.fail_button_style = True
            await app.callback('nav:tariffs')
            assert not app.navigation.button_styles
            screen = app.navigation.views[123].screen
            assert '199 ₽' in screen.text
            assert not any(b.style for row in screen.keyboard.inline_keyboard for b in row)
            await app.callback('nav:home')
            assert 'Свобода подключения' in app.navigation.views[123].screen.text
        finally:
            await app.close()
    asyncio.run(run())


def test_new_199_order_preserves_old_299_order_and_applies_once(tmp_path):
    async def run():
        engine, sessions = await setup(tmp_path)
        gateway, lookup = FakeGateway(), FakeLookup()
        legacy = PaymentCheckoutService(sessions, gateway, 'https://example.org/return')
        pilot = PaymentCheckoutService(sessions, gateway, 'https://example.org/return', tariffs=PILOT_TARIFFS)
        try:
            with pytest.raises(ValueError):
                await legacy.create_for_user(501, 'pilot_month_199')
            with pytest.raises(ValueError):
                await pilot.create_for_user(501, 'month')
            old = await legacy.create_for_user(501, 'month')
            new = await pilot.create_for_user(501, 'pilot_month_199')
            retry = await pilot.create_for_user(501, 'pilot_month_199')
            assert old.provider_id != new.provider_id == retry.provider_id
            assert [c['amount_kopecks'] for c in gateway.calls] == [29900, 19900, 19900]
            assert gateway.calls[1]['idempotency_key'] == gateway.calls[2]['idempotency_key']
            async with sessions() as session:
                old_row = await session.scalar(select(Payment).where(Payment.provider_payment_id == old.provider_id))
                new_row = await session.scalar(select(Payment).where(Payment.provider_payment_id == new.provider_id))
                assert old_row.amount_kopecks == 29900 and old_row.tariff_id == 'month'
                assert new_row.amount_kopecks == 19900 and new_row.tariff_id == 'pilot_month_199'
                lookup.rows[new.provider_id] = provider_row(new_row)
                lookup.rows[old.provider_id] = provider_row(old_row)
            processor = PaymentNotificationProcessor(sessions, lookup)
            # A 299 RUB confirmation cannot activate the new 199 RUB order.
            lookup.rows[new.provider_id]['amount']['value'] = '299.00'
            with pytest.raises(ValueError):
                await processor.process(new.provider_id)
            lookup.rows[new.provider_id]['amount']['value'] = '199.00'
            await processor.process(new.provider_id)
            reader = SubscriptionService(sessions)
            first = await reader.snapshot(501)
            assert first.tariff_label == '1 месяц — 199 ₽' and first.status == 'active'
            await processor.process(new.provider_id)
            assert (await reader.snapshot(501)).expires_at == first.expires_at
            async with sessions() as session:
                assert await session.scalar(select(func.count()).select_from(SubscriptionHistory).where(
                    SubscriptionHistory.reason == 'payment')) == 1
            # A legitimately completed old order still grants its purchased month.
            await processor.process(old.provider_id)
            final = await reader.snapshot(501)
            assert as_utc(final.expires_at) > as_utc(first.expires_at)
            assert final.tariff_label == '1 месяц — 299 ₽'
        finally:
            await engine.dispose()
    asyncio.run(run())
