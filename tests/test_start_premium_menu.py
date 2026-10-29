"""The first and repeated /start expose the catalog without a begin callback."""
import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram.methods import DeleteMessage, SendMessage, SendPhoto
from aiogram.types import ReplyKeyboardMarkup, Update
from sqlalchemy.exc import SQLAlchemyError

from bot.screens import home
from bot.services import Services
from bot.services.account import AccountSnapshot
from bot.services.onboarding import MemoryPilotOnboarding
from bot.services.subscriptions import utcnow
from tests.fake_telegram import TelegramHarness


def pilot(snapshot):
    services = Services.demo()
    services.premium_account_enabled = services.pilot_welcome_enabled = True
    services.onboarding = MemoryPilotOnboarding()
    services.subscriptions.snapshot = AsyncMock(return_value=snapshot)
    services.test_checkout = AsyncMock()
    return services


@pytest.mark.parametrize('known', [False, True])
def test_start_immediately_exposes_five_actions_and_only_one_banner(known):
    async def run():
        services = pilot(AccountSnapshot(status='active', expires_at=utcnow() + timedelta(days=20)))
        if known:
            services.onboarding.known.add(123)
        app = TelegramHarness(services=services)
        try:
            await app.text('/start')
            screen = app.navigation.views[123].screen
            assert [[b.text for b in row] for row in screen.keyboard.inline_keyboard] == [
                ['💎 Купить VPN'], ['👤 Моя подписка', '🚀 Подключиться'],
                ['💬 Поддержка', 'ℹ️ Информация']]
            assert screen.keyboard.inline_keyboard[0][0].style == 'primary'
            if known:
                greeting=next(c for c in app.calls if isinstance(c,SendMessage) and isinstance(c.reply_markup,ReplyKeyboardMarkup))
                assert '🟢 Подписка: активна' in greeting.text and '📅 Действует до:' in greeting.text
            assert len(screen.text) < 350
            assert not any(b.url for row in screen.keyboard.inline_keyboard for b in row)
            receipt = await app.bot.send_message(123, 'Оплата подтверждена')
            await app.callback('nav:tariffs')
            catalog = app.navigation.views[123].screen
            assert all(price in catalog.text for price in ('199', '499', '1790'))
            assert 'Рекомендуем' in str(catalog.keyboard)
            await app.callback('tariff:pilot_month_199')
            services.test_checkout.create_for_user.assert_not_awaited()
            for target, marker in [('nav:home', 'Свобода начинается здесь'),
                                   ('nav:info', 'Информация'), ('nav:home', 'KudaVPN'),
                                   ('nav:support', 'Поддержка'), ('nav:home', 'KudaVPN'),
                                   ('nav:account', 'Kuda Status')]:
                await app.callback(target)
                assert marker in app.navigation.views[123].screen.text
            await app.text('/start')
            await app.text('/start')
            assert len([c for c in app.calls if isinstance(c, SendPhoto)]) == (0 if known else 1)
            assert (123, receipt.message_id) in app.messages
            assert len([c for c in app.calls if isinstance(c, SendMessage)
                        and isinstance(c.reply_markup, ReplyKeyboardMarkup)]) == (3 if known else 2)
            assert not any(isinstance(c, DeleteMessage) and c.message_id in (1, 2, 3)
                           for c in app.calls)
        finally:
            await app.close()
    asyncio.run(run())


@pytest.mark.parametrize('snapshot,expected', [
    (AccountSnapshot(), '🔴 Подписка: не активна'),
    (AccountSnapshot(status='expired', expires_at=utcnow() - timedelta(days=1)), 'срок истёк'),
    (AccountSnapshot(status='active', expires_at=utcnow() - timedelta(days=1)), 'срок истёк'),
    (AccountSnapshot(status='blocked'), 'доступ заблокирован'),
    (None, 'временно недоступен'),
])
def test_home_does_not_invent_active_status(snapshot, expected):
    screen = home(premium_account=True, snapshot=snapshot)
    assert expected in screen.text
    assert '🟢' not in screen.text
    assert '📅 Действует до:' not in screen.text
    assert screen.keyboard.inline_keyboard[0][0].callback_data == 'nav:tariffs'


def test_subscription_read_failure_keeps_purchase_and_navigation_available():
    async def run():
        services = pilot(AccountSnapshot())
        services.subscriptions.snapshot.side_effect = SQLAlchemyError('private detail')
        app = TelegramHarness(services=services)
        try:
            await app.text('/start')
            screen = app.navigation.views[123].screen
            assert 'private detail' not in screen.text
            await app.callback('nav:tariffs')
            assert '199' in app.navigation.views[123].screen.text
        finally:
            await app.close()
    asyncio.run(run())


def test_legacy_begin_button_still_opens_current_menu_without_new_banner():
    async def run():
        services = pilot(AccountSnapshot())
        services.onboarding.known.add(123)
        await services.onboarding.first_start(123, new_user=True)
        app = TelegramHarness(services=services)
        try:
            banner = await app.bot.send_message(123, 'Старое приветствие')
            await services.onboarding.record_welcome(123, banner.message_id)
            await app.callback('pilot:begin', message_id=banner.message_id)
            assert app.navigation.views[123].screen.keyboard.inline_keyboard[0][0].text == '💎 Купить VPN'
            assert (123, banner.message_id) in app.messages
            assert not any(isinstance(c, SendPhoto) for c in app.calls)
        finally:
            await app.close()
    asyncio.run(run())


def test_repeat_start_refreshes_real_subscription_without_resetting_onboarding():
    async def run():
        services = pilot(AccountSnapshot())
        app = TelegramHarness(services=services)
        try:
            await app.text('/start')
            banner_id = services.onboarding.rows[123]['message_id']
            services.subscriptions.snapshot.return_value = AccountSnapshot(
                status='active', expires_at=utcnow() + timedelta(days=30))
            await app.text('/start')
            assert any('🟢 Подписка: активна' in c.text for c in app.calls if isinstance(c,SendMessage))
            assert services.onboarding.rows[123]['message_id'] == banner_id
            assert len([c for c in app.calls if isinstance(c, SendPhoto)]) == 1
            assert (123, banner_id) in app.messages
        finally:
            await app.close()
    asyncio.run(run())


def test_repeat_start_menu_is_below_keyboard_hint_with_real_telegram_message_ids():
    async def run():
        services = pilot(AccountSnapshot())
        services.onboarding.known.add(123)
        app = TelegramHarness(services=services)
        try:
            await app.text('/start')
            old_menu = app.navigation.views[123].message_id
            # Real Telegram shares one monotonically increasing chat sequence
            # across incoming commands and outgoing bot messages.
            incoming_id = old_menu + 1
            app.sequence = incoming_id
            app.update_id += 1
            update = Update.model_validate({'update_id': app.update_id, 'message': {
                'message_id': incoming_id, 'date': 1,
                'chat': {'id': 123, 'type': 'private'},
                'from': {'id': 123, 'is_bot': False, 'first_name': 'Tester'},
                'text': '/start', 'entities': [{'type': 'bot_command', 'offset': 0, 'length': 6}]}})
            await app.dispatcher.feed_update(app.bot, update)
            hint = services.onboarding.rows[123]['keyboard_message_id']
            assert app.navigation.views[123].message_id > hint > incoming_id
            assert (123, old_menu) not in app.messages
            assert not any(isinstance(c, DeleteMessage) and c.message_id == incoming_id for c in app.calls)
        finally:
            await app.close()
    asyncio.run(run())
