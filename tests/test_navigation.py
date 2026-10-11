"""Navigation invariants with a real aiogram Dispatcher and a fake API transport."""
import asyncio
from html.parser import HTMLParser
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup
from aiogram.methods import AnswerCallbackQuery, SendMessage, EditMessageText, EditMessageReplyMarkup, DeleteMessage
from sqlalchemy.exc import OperationalError
from bot.config import Config
from bot.navigation import Navigation
from bot.services.account import AccountSnapshot
from tests.fake_telegram import TelegramHarness


def calls(app, kind):
    return [call for call in app.calls if isinstance(call, kind)]


def scenario(check):
    async def run():
        app = TelegramHarness()
        try:
            await check(app)
        finally:
            await app.close()
    asyncio.run(run())


def test_start_sends_inline_menu_and_repeat_start_keeps_one_screen():
    async def check(app):
        await asyncio.gather(*(app.text('/start') for _ in range(4)))
        assert len(calls(app, SendMessage)) == 1
        assert not calls(app, EditMessageReplyMarkup)
        keyboard = calls(app, SendMessage)[0].reply_markup
        assert isinstance(keyboard, InlineKeyboardMarkup)
        message_id = app.navigation.views[123].message_id
        assert app.messages[(123, message_id)].reply_markup == keyboard
        assert [button.text for row in keyboard.inline_keyboard for button in row] == [
            '🚀 Подключить VPN', '👤 Личный кабинет', '💎 Тарифы', '📊 Мой трафик', '🎁 Бонусы', '💬 Поддержка']
        assert len(calls(app, DeleteMessage)) == 4
        assert 'KudaVPN Premium' in calls(app, SendMessage)[0].text
    scenario(check)


@pytest.mark.parametrize('route', ['nav:connect', 'nav:happ', 'nav:home', 'nav:account', 'nav:devices',
    'nav:tariffs', 'tariff:month', 'tariff:quarter', 'tariff:year', 'tariff:invalid',
    'nav:traffic', 'nav:bonuses', 'nav:support', 'unknown', 'buy', 'menu', 'device:add', 'instructions'])
def test_callbacks_answer_and_only_edit_the_same_message(route):
    async def check(app):
        await app.text()
        screen_id = app.navigation.views[123].message_id
        app.calls.clear()
        await app.callback(route)
        assert len(calls(app, AnswerCallbackQuery)) == 1
        assert not calls(app, SendMessage)
        assert all(method.message_id == screen_id for method in calls(app, EditMessageText))
        assert app.calls[0].__api_method__ == 'answerCallbackQuery'
    scenario(check)


def test_double_clicks_and_parallel_navigation_are_serialized():
    async def check(app):
        await app.text()
        app.calls.clear()
        await asyncio.gather(*(app.callback('nav:traffic') for _ in range(8)))
        assert len(calls(app, AnswerCallbackQuery)) == 8
        assert len(calls(app, EditMessageText)) == 1
        async def slow_account(user_id):
            await asyncio.sleep(.01)
            return AccountSnapshot()
        app.services.subscriptions.snapshot = slow_account
        first = asyncio.create_task(app.callback('nav:account'))
        await asyncio.sleep(0)
        second = asyncio.create_task(app.callback('nav:support'))
        await asyncio.gather(first, second)
        assert '💬 Поддержка' in app.navigation.views[123].screen.text
        assert not calls(app, SendMessage)
    scenario(check)


@pytest.mark.parametrize('reason', ['message is not modified', 'invalid reply markup'])
def test_edit_rejections_never_send_a_replacement(reason):
    async def check(app):
        await app.text()
        app.calls.clear()
        app.fail_edit = reason
        await app.callback('nav:traffic')
        assert len(calls(app, AnswerCallbackQuery)) == 1
        assert len(calls(app, EditMessageText)) == 1
        assert not calls(app, SendMessage)
        if reason == 'message is not modified':
            assert '📊 Мой трафик' in app.navigation.views[123].screen.text
    scenario(check)


@pytest.mark.parametrize('exception', ['network', 'rate_limit'])
def test_transient_edit_failure_has_no_send_fallback(exception):
    async def check(app):
        await app.text()
        method = EditMessageText(text='test')
        app.fail_edit = TelegramNetworkError(method=method, message='offline') if exception == 'network' else TelegramRetryAfter(method=method, message='limit', retry_after=1)
        app.calls.clear()
        await app.callback('nav:support')
        await app.text('/start')
        assert not calls(app, SendMessage)
    scenario(check)


def test_deleted_screen_replaced_once_and_repeat_start_reuses_it():
    async def check(app):
        await app.text()
        app.fail_edit = 'message to edit not found'
        await app.callback('nav:traffic')
        assert len(calls(app, SendMessage)) == 2
        app.fail_edit = None
        await app.text('/start')
        assert len(calls(app, SendMessage)) == 2
    scenario(check)


def test_command_deletion_and_expired_callback_are_nonfatal(caplog):
    async def check(app):
        app.fail_delete = 'not enough rights to delete message'
        await app.text()
        app.fail_answer = 'query is too old and response timeout expired'
        app.calls.clear()
        await app.callback('nav:account')
        assert len(calls(app, AnswerCallbackQuery)) == 1
        assert not calls(app, SendMessage)
        assert '👤 Личный кабинет' in app.navigation.views[123].screen.text
        assert not [record for record in caplog.records if record.levelname == 'ERROR']
    scenario(check)


def test_stale_inaccessible_inline_callbacks_do_not_send_or_edit():
    async def check(app):
        await app.text()
        app.calls.clear()
        await app.callback('nav:account', message_id=5)
        await app.callback('nav:account', inaccessible=True)
        await app.callback('nav:account', inline=True)
        assert len(calls(app, AnswerCallbackQuery)) == 3
        assert not calls(app, EditMessageText) and not calls(app, SendMessage)
        assert 'устарел' in calls(app, AnswerCallbackQuery)[0].text
    scenario(check)


def test_restart_adopts_callback_message_without_sending():
    async def check(app):
        await app.text()
        original = app.navigation.views[123].message_id
        app.navigation = Navigation(app.navigation.store)
        app.dispatcher['navigation'] = app.navigation
        app.calls.clear()
        await app.callback('nav:account', message_id=original)
        assert not calls(app, SendMessage)
        assert calls(app, EditMessageText)[0].message_id == original
    scenario(check)


def test_database_error_shows_no_fake_account_and_does_not_log_details(caplog):
    async def check(app):
        await app.text()
        app.services.subscriptions.snapshot = AsyncMock(side_effect=OperationalError('test', None, Exception('hidden-database-detail')))
        app.calls.clear()
        await app.callback('nav:account')
        assert 'Не удалось загрузить данные' in app.navigation.views[123].screen.text
        assert not calls(app, SendMessage)
        assert len(calls(app, AnswerCallbackQuery)) == 1
        assert 'hidden-database-detail' not in caplog.text
    scenario(check)


def test_account_uses_unknown_values_html_escape_and_real_snapshots():
    async def check(app):
        await app.text()
        await app.callback('nav:account')
        text = app.navigation.views[123].screen.text
        assert 'Не активна' in text
        assert 'Устройства: Нет данных' in text and 'Трафик: Нет данных' in text
        app.services.subscriptions.snapshot = AsyncMock(return_value=AccountSnapshot(status='active', tariff_label='<b>untrusted & tariff</b>', device_count=2))
        await app.callback('nav:account')
        text = app.navigation.views[123].screen.text
        assert 'Устройства: 2' in text and 'Трафик: Нет данных' in text
        assert '&lt;b&gt;untrusted &amp; tariff&lt;/b&gt;' in text
    scenario(check)


@pytest.mark.parametrize('configured', [False, True])
def test_support_only_uses_configured_contact(configured):
    async def check(app):
        app.dispatcher['config'] = Config('123456:offline_test_token', 'kudavpn_support' if configured else '')
        await app.text()
        await app.callback('nav:support')
        screen = app.navigation.views[123].screen
        urls = [button.url for row in screen.keyboard.inline_keyboard for button in row if button.url]
        assert urls == (['https://t.me/kudavpn_support'] if configured else [])
        if not configured:
            assert 'пока не настроен' in screen.text
    scenario(check)


def test_tariffs_and_connection_do_not_issue_payments_or_vpn_links():
    async def check(app):
        app.services.payments.create_payment = AsyncMock(side_effect=AssertionError('must not charge'))
        await app.text()
        await app.callback('nav:tariffs')
        keyboard = app.navigation.views[123].screen.keyboard
        assert [row[0].text for row in keyboard.inline_keyboard[:3]] == ['1 месяц — 299 ₽', '3 месяца — 799 ₽', '12 месяцев — 2490 ₽']
        for tariff in ('month', 'quarter', 'year'):
            await app.callback('tariff:' + tariff)
            assert 'Платёж не создан' in app.navigation.views[123].screen.text
        app.services.payments.create_payment.assert_not_awaited()
        for route in ('nav:connect', 'nav:happ'):
            await app.callback(route)
            text = app.navigation.views[123].screen.text
            assert 'Happ' in text
            assert all(protocol not in text for protocol in ('happ://', 'vless://', 'wireguard://', 'https://'))
        await app.callback('nav:bonuses')
        assert 'ещё не запущена' in app.navigation.views[123].screen.text
    scenario(check)


def test_telegram_limits_back_paths_and_device_html():
    class BalancedHTML(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
        def handle_starttag(self, tag, attrs):
            assert tag in ('b', 'i')
            self.stack.append(tag)
        def handle_endtag(self, tag):
            assert self.stack.pop() == tag
    async def check(app):
        await app.text()
        app.services.devices.describe = AsyncMock(return_value='📱 Ваши устройства\n\n' + '<unsafe&>' * 1000)
        for route in ('nav:account', 'nav:devices', 'nav:tariffs', 'tariff:year', 'nav:connect', 'nav:happ', 'nav:bonuses', 'nav:traffic', 'nav:support'):
            await app.callback(route)
            screen = app.navigation.views[123].screen
            parser = BalancedHTML()
            parser.feed(screen.text)
            assert not parser.stack
            assert len(parser.get_starttag_text() or '') < 4096
            # Telegram limits text after entity parsing, not escaped source length.
            from html import unescape
            import re
            assert len(unescape(re.sub(r'<[^>]+>', '', screen.text))) <= 4096
            buttons = [button for row in screen.keyboard.inline_keyboard for button in row]
            assert any(button.text == '⬅️ Назад' for button in buttons)
            assert all(len(button.callback_data.encode()) <= 64 for button in buttons if button.callback_data)
        await app.callback('nav:happ')
        assert any(button.callback_data == 'nav:connect' for row in app.navigation.views[123].screen.keyboard.inline_keyboard for button in row)
    scenario(check)


def test_separate_users_have_separate_screens():
    async def check(app):
        await asyncio.gather(app.text(user_id=123), app.text(user_id=456))
        await app.callback('nav:traffic', user_id=123)
        assert 'Мой трафик' in app.navigation.views[123].screen.text
        assert 'Мой трафик' not in app.navigation.views[456].screen.text
        assert len(calls(app, SendMessage)) == 2
    scenario(check)



def test_start_has_inline_buttons_even_when_editing_is_unavailable():
    async def check(app):
        # Regression: adding the keyboard by a second edit must not be necessary.
        app.fail_edit = "Bad Request: message can't be edited"
        await app.text('/start')
        sent = calls(app, SendMessage)
        assert len(sent) == 1
        assert isinstance(sent[0].reply_markup, InlineKeyboardMarkup)
        assert len([b for row in sent[0].reply_markup.inline_keyboard for b in row]) == 6
        assert not calls(app, EditMessageReplyMarkup) and not calls(app, EditMessageText)
        screen_id = app.navigation.views[123].message_id
        assert app.messages[(123, screen_id)].reply_markup == sent[0].reply_markup
        app.fail_edit = None
        await app.callback('nav:account')
        await app.callback('nav:home')
        assert len(calls(app, SendMessage)) == 1
        assert app.messages[(123, screen_id)].reply_markup == sent[0].reply_markup
    scenario(check)


def test_repeat_start_checks_cached_home_and_restores_manually_deleted_screen():
    async def check(app):
        await app.text('/start')
        previous = app.navigation.views[123].message_id
        del app.messages[(123, previous)]  # User removed the home screen in Telegram.
        app.calls.clear()
        await app.text('/start')
        current = app.navigation.views[123].message_id
        assert current != previous
        assert len(calls(app, EditMessageText)) == 1
        assert len(calls(app, SendMessage)) == 1
        assert len(app.messages) == 1
        assert (await app.navigation.store.load(123)).message_id == current
        await app.text('/start')
        await app.callback('nav:home')
        assert len(calls(app, SendMessage)) == 1
        assert len(app.messages) == 1
        assert app.messages[(123, current)].reply_markup is not None
    scenario(check)


def test_repeat_start_accepts_not_modified_for_cached_home():
    async def check(app):
        await app.text('/start')
        previous = app.navigation.views[123].message_id
        app.calls.clear()
        app.fail_edit = 'message is not modified'
        await app.text('/start')
        assert len(calls(app, EditMessageText)) == 1
        assert not calls(app, SendMessage)
        assert app.navigation.views[123].message_id == previous
        assert len(app.messages) == 1
    scenario(check)
