"""Telegram Premium UI 2.0: compact markup, truthful state and safe styles."""
import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

from aiogram.methods import AnswerCallbackQuery, DeleteMessage, EditMessageText, SendMessage
from sqlalchemy.exc import SQLAlchemyError

from bot.screens import (account, connect, happ, help_center, home, kuda_smart,
                         premium_devices, subscription_details, tariff_details,
                         tariffs, test_checkout as checkout_screen)
from bot.services import Services
from bot.services.account import AccountSnapshot
from bot.services.subscriptions import utcnow
from tests.fake_telegram import TelegramHarness


def buttons(screen):
    return [button for row in screen.keyboard.inline_keyboard for button in row]


def test_premium_menu_is_compact_with_one_primary_and_true_status():
    snapshot = AccountSnapshot(status='active', tariff_label='1 месяц — 299 ₽',
                               expires_at=utcnow() + timedelta(days=3))
    screen = home(premium_account=True, snapshot=snapshot)
    assert 'Свобода подключения. Простота управления.' in screen.text
    assert 'Подписка: <b>Активна</b>' in screen.text
    assert [[button.callback_data for button in row] for row in screen.keyboard.inline_keyboard] == [
        ['nav:connect'], ['nav:account', 'nav:tariffs'],
        ['nav:smart', 'nav:devices'], ['nav:invite', 'nav:help']]
    assert [button.callback_data for button in buttons(screen) if button.style == 'primary'] == ['nav:connect']
    assert all(len(button.text) <= 30 and len(button.callback_data.encode()) <= 64
               for button in buttons(screen))
    assert all(sum(len(button.text) for button in row) <= 30
               for row in screen.keyboard.inline_keyboard)


def test_secondary_screens_keep_one_highlight_and_no_fake_mode_controls():
    active = AccountSnapshot(status='active')
    screens = (account(active, premium_account=True), subscription_details(active),
               premium_devices('active'), help_center(''), tariffs(test_payment=True),
               tariff_details('month', test_payment=True),
               checkout_screen('month', 'https://checkout.example.invalid'),
               connect('https://subscriptions.example.invalid/s/' + 'a' * 43,
                       page_url='https://subscriptions.example.invalid/connect/test',
                       premium_account=True), kuda_smart(), happ())
    for screen in screens:
        assert len(screen.text) < 4096
        assert sum(button.style == 'primary' for button in buttons(screen)) <= 1
        assert buttons(screen)[-1].text in {'⬅️ Назад', '⌂ Главное меню'}
        assert all(button.style in {None, 'primary'} for button in buttons(screen))
    assert {button.callback_data for button in buttons(kuda_smart())} == {'nav:connect', 'nav:home'}
    assert 'Сейчас эти режимы не включаются' in kuda_smart().text
    page_buttons = buttons(screens[-3])
    assert page_buttons[0].style == 'primary' and page_buttons[1].style is None
    assert page_buttons[0].url.startswith('https://subscriptions.example.invalid/connect/')


def test_premium_navigation_keeps_one_screen_and_does_not_delete_start():
    async def run():
        services = Services.demo()
        services.premium_account_enabled = True
        services.subscriptions.snapshot = AsyncMock(return_value=AccountSnapshot(status='active'))
        app = TelegramHarness(services=services)
        try:
            await app.text('/start')
            message_id = app.navigation.views[123].message_id
            for route, expected in (
                ('nav:smart', 'в разработке'), ('nav:home', 'Свобода подключения.'),
                ('nav:devices', 'пока недоступны'), ('nav:home', 'Свобода подключения.'),
                ('nav:invite', 'не запущены'), ('nav:home', 'Свобода подключения.'),
                ('nav:help', 'Как подключиться?'), ('nav:account', 'Личный кабинет'),
                ('nav:tariffs', 'Выберите срок доступа'), ('nav:home', 'Свобода подключения.'),
            ):
                acknowledged = sum(isinstance(call, AnswerCallbackQuery) for call in app.calls)
                await app.callback(route)
                assert expected in app.navigation.views[123].screen.text
                assert app.navigation.views[123].message_id == message_id
                assert sum(isinstance(call, AnswerCallbackQuery) for call in app.calls) == acknowledged + 1
            assert sum(isinstance(call, SendMessage) for call in app.calls) == 1
            assert not any(isinstance(call, DeleteMessage) for call in app.calls)
        finally:
            await app.close()
    asyncio.run(run())


def test_start_brings_hidden_premium_menu_to_recent_chat_position():
    async def run():
        services = Services.demo()
        services.premium_account_enabled = True
        services.subscriptions.snapshot = AsyncMock(return_value=AccountSnapshot(status='active'))
        app = TelegramHarness(services=services)
        try:
            await app.text('/start')
            original = app.navigation.views[123].message_id
            app.update_id = original + 10  # intervening chat activity after the old menu
            await app.text('/start')
            assert app.navigation.views[123].message_id != original
            assert sum(isinstance(call, SendMessage) for call in app.calls) == 2
            assert (123, original) not in app.messages
            assert 'Свобода подключения.' in app.navigation.views[123].screen.text
        finally:
            await app.close()
    asyncio.run(run())


def test_home_degrades_without_database_and_production_stays_plain():
    async def run():
        services = Services.demo()
        services.premium_account_enabled = True
        services.subscriptions.snapshot = AsyncMock(side_effect=SQLAlchemyError('offline'))
        app = TelegramHarness(services=services)
        try:
            await app.text('/start')
            assert 'Статус подписки — в личном кабинете' in app.navigation.views[123].screen.text
            assert 'Активна' not in app.navigation.views[123].screen.text
        finally:
            await app.close()
    asyncio.run(run())

    plain = home()
    assert 'Свобода подключения.' not in plain.text
    assert not any(button.style for button in buttons(plain))
    assert 'nav:smart' not in {button.callback_data for button in buttons(plain)}


def test_unsupported_button_style_falls_back_without_losing_navigation():
    async def run():
        services = Services.demo()
        services.premium_account_enabled = True
        app = TelegramHarness(services=services)
        app.fail_button_style = True
        try:
            await app.text('/start')
            assert app.navigation.button_styles is False
            assert sum(isinstance(call, SendMessage) for call in app.calls) == 2
            assert not any(button.style for button in buttons(app.navigation.views[123].screen))
            await app.callback('nav:smart')
            assert 'Kuda Smart' in app.navigation.views[123].screen.text
            assert not any(button.style for button in buttons(app.navigation.views[123].screen))
        finally:
            await app.close()
    asyncio.run(run())


def test_style_rejection_while_editing_retries_same_message():
    async def run():
        services = Services.demo()
        services.premium_account_enabled = True
        app = TelegramHarness(services=services)
        try:
            await app.text('/start')
            message_id = app.navigation.views[123].message_id
            app.fail_button_style = True
            await app.callback('nav:smart')
            assert app.navigation.button_styles is False
            assert app.navigation.views[123].message_id == message_id
            assert sum(isinstance(call, EditMessageText) for call in app.calls) == 2
            assert sum(isinstance(call, SendMessage) for call in app.calls) == 1
        finally:
            await app.close()
    asyncio.run(run())


def test_emoji_and_style_rejection_both_fall_back_to_readable_text():
    async def run():
        services = Services.demo()
        services.premium_account_enabled = True
        app = TelegramHarness(services=services)
        app.navigation.custom_emoji = True
        app.fail_custom_emoji = True
        app.fail_button_style = True
        try:
            await app.text('/start')
            assert app.navigation.custom_emoji is False
            assert app.navigation.button_styles is False
            assert sum(isinstance(call, SendMessage) for call in app.calls) == 3
            screen = app.navigation.views[123].screen
            assert 'Свобода подключения.' in screen.text
            assert not any(button.style or button.icon_custom_emoji_id for button in buttons(screen))
        finally:
            await app.close()
    asyncio.run(run())
