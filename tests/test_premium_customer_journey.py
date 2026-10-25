"""Pilot section traversal uses the real router and an offline Telegram transport."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.methods import DeleteMessage, SendPhoto
from aiogram.types import ReplyKeyboardMarkup, ReplyKeyboardRemove

from bot.keyboards.reply import ACCOUNT, CONNECT, INFO
from bot.services import Services, TARIFFS, PILOT_TARIFFS
from bot.services.account import AccountSnapshot
from bot.services.happ import AccessDenied
from bot.services.onboarding import MemoryPilotOnboarding
from bot.services.subscriptions import utcnow
from tests.fake_telegram import TelegramHarness


@pytest.mark.parametrize('status', ['active', 'expired', 'inactive'])
def test_pilot_customer_can_traverse_sections_without_order_or_lost_receipt(status):
    async def run():
        services = Services.demo()
        services.premium_account_enabled = services.pilot_welcome_enabled = True
        services.onboarding = MemoryPilotOnboarding()
        services.onboarding.known.add(123)
        services.subscriptions.snapshot = AsyncMock(return_value=AccountSnapshot(
            status=status, tariff_label=TARIFFS['month'].label if status != 'inactive' else None,
            expires_at=utcnow() + timedelta(days=2 if status == 'active' else -2)
            if status != 'inactive' else None))
        services.test_checkout = AsyncMock()
        services.test_payment_owner_id = 123
        services.happ = SimpleNamespace(
            issue_for_user=AsyncMock(return_value='https://subscriptions.example/s/offline'),
            available_node_names_for_user=AsyncMock(return_value=('Isolated test node',)))
        if status != 'active':
            services.happ.issue_for_user.side_effect = AccessDenied()
        app = TelegramHarness(services=services)

        def screen():
            return app.navigation.views[123].screen

        async def tap(route):
            assert route in [b.callback_data for row in screen().keyboard.inline_keyboard for b in row]
            await app.callback(route)

        try:
            await app.text('/start')
            receipt = await app.bot.send_message(123, 'Ранее подтверждённое событие подписки')
            await app.text(ACCOUNT)
            assert 'Kuda Status' in screen().text
            for route in ('nav:subscription', 'nav:devices'):
                await tap(route)
                await tap('nav:account')
            for route in ('nav:history', 'nav:traffic', 'nav:billing'):
                await tap('nav:manage')
                await tap(route)
                await tap('nav:account')
            await tap('nav:renew')
            for tariff in PILOT_TARIFFS:
                await tap('tariff:' + tariff)
                assert 'paytest:' + tariff in [
                    b.callback_data for row in screen().keyboard.inline_keyboard for b in row]
                await tap('nav:tariffs')
            await tap('nav:home')
            await tap('nav:smart')
            await tap('nav:home')
            await app.text(INFO)
            await tap('nav:support')
            await tap('nav:home')
            old_screen = app.navigation.views[123].message_id
            await app.text(CONNECT)
            urls = [b.url for row in screen().keyboard.inline_keyboard for b in row if b.url]
            assert bool(urls) == (status == 'active')
            assert (123, old_screen) not in app.messages
            await tap('nav:happ')
            await tap('nav:connect')
            await app.text('/start')
            services.test_checkout.create_for_user.assert_not_awaited()
            assert (123, receipt.message_id) in app.messages
            assert not any(isinstance(c, DeleteMessage) and c.message_id == receipt.message_id
                           for c in app.calls)
            assert not any(isinstance(c, SendPhoto) for c in app.calls)
            assert any(isinstance(getattr(c, 'reply_markup', None), ReplyKeyboardMarkup)
                       for c in app.calls)
            assert not any(isinstance(getattr(c, 'reply_markup', None), ReplyKeyboardRemove)
                           for c in app.calls)
        finally:
            await app.close()

    asyncio.run(run())
