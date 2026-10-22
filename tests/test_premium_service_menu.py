"""Premium pilot navigation: available actions work, unavailable ones stay honest."""
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from aiogram.methods import AnswerCallbackQuery, SendMessage

from bot.services import Services
from bot.services.account import AccountSnapshot
from bot.services.subscriptions import utcnow
from tests.fake_telegram import TelegramHarness


def routes(screen):
    return [button.callback_data for row in screen.keyboard.inline_keyboard
            for button in row if button.callback_data]


def test_premium_sections_navigate_back_without_duplicate_messages_or_side_effects():
    async def run():
        services = Services.demo()
        services.premium_account_enabled = True
        services.subscriptions.snapshot = AsyncMock(return_value=AccountSnapshot(
            status='active', tariff_label='1 месяц — 299 ₽',
            expires_at=utcnow() + timedelta(days=20)))
        services.test_checkout = AsyncMock()
        services.test_payment_owner_id = 123
        app = TelegramHarness(services=services)
        try:
            await app.text('/start')
            message_id = app.navigation.views[123].message_id
            for destination, marker in (
                ('nav:subscription', 'Моя подписка'),
                ('nav:devices', 'Список отдельных устройств'),
                ('nav:traffic', 'Нет данных'),
                ('nav:billing', 'Автопродление: <b>выключено</b>'),
                ('nav:gift', 'пока не выдаются'),
                ('nav:invite', 'Бонус за приглашение не начисляется'),
            ):
                await app.callback('nav:account')
                assert destination in routes(app.navigation.views[123].screen)
                before = len([call for call in app.calls if isinstance(call, AnswerCallbackQuery)])
                await app.callback(destination)
                view = app.navigation.views[123]
                assert marker in view.screen.text
                assert view.message_id == message_id
                assert 'nav:account' in routes(view.screen)
                assert 'nav:home' in routes(view.screen)
                assert len([call for call in app.calls if isinstance(call, AnswerCallbackQuery)]) == before + 1
            services.test_checkout.create_for_user.assert_not_awaited()
            assert len([call for call in app.calls if isinstance(call, SendMessage)]) == 1
        finally:
            await app.close()
    asyncio.run(run())


def test_expired_and_missing_subscription_offer_no_false_access_or_traffic():
    async def run():
        services = Services.demo()
        services.premium_account_enabled = True
        services.subscriptions.snapshot = AsyncMock(return_value=AccountSnapshot(
            status='expired', tariff_label='1 месяц — 299 ₽',
            expires_at=utcnow() - timedelta(days=1)))
        app = TelegramHarness(services=services)
        try:
            await app.text('/start')
            await app.callback('nav:subscription')
            view = app.navigation.views[123].screen
            assert 'Истекла' in view.text and 'nav:connect' not in routes(view)
            assert 'nav:tariffs' in routes(view)
            await app.callback('nav:devices')
            assert 'сначала активируйте подписку' in app.navigation.views[123].screen.text
            await app.callback('nav:traffic')
            assert 'Нет данных' in app.navigation.views[123].screen.text
            assert '0.00 ГБ' not in app.navigation.views[123].screen.text
            services.subscriptions.snapshot.return_value = AccountSnapshot()
            await app.callback('nav:subscription')
            assert 'Не активна' in app.navigation.views[123].screen.text
            assert 'nav:connect' not in routes(app.navigation.views[123].screen)
        finally:
            await app.close()
    asyncio.run(run())


def test_premium_read_models_escape_user_data_and_never_show_personal_url_in_menu():
    from bot.screens import account, expiry_label, gift, home, invite, subscription_details, traffic
    personal = 'https://subscriptions.example/s/' + 'a' * 43
    snapshot = AccountSnapshot(status='active', tariff_label='<b>unsafe & tariff</b>',
                               traffic_bytes=None)
    for screen in (home(premium_account=True), account(snapshot, premium_account=True),
                   subscription_details(snapshot), traffic(snapshot), gift(), invite()):
        assert personal not in screen.text
        assert not any(button.url for row in screen.keyboard.inline_keyboard for button in row)
    assert '&lt;b&gt;unsafe &amp; tariff&lt;/b&gt;' in subscription_details(snapshot).text
    assert expiry_label(datetime(2026, 11, 21, 15, 24)) == '21.11.2026 15:24 UTC'
