"""Kuda Smart is an honest preview; live subscriptions stay unchanged."""
import asyncio
from html.parser import HTMLParser
from unittest.mock import AsyncMock

import pytest
from aiogram.methods import AnswerCallbackQuery, SendMessage

from bot.premium_emoji import decorate
from bot.screens import kuda_smart
from bot.services import Services
from bot.services.kuda_smart import SmartPrototypeUnavailable, happ_launch_selection_headers
from tests.fake_telegram import TelegramHarness


def routes(screen):
    return {button.callback_data for row in screen.keyboard.inline_keyboard
            for button in row if button.callback_data}


def test_smart_screen_only_in_premium_pilot_and_never_activates_a_mode():
    async def run():
        services = Services.demo()
        services.premium_account_enabled = True
        app = TelegramHarness(services=services)
        try:
            await app.text('/start')
            assert 'nav:smart' not in routes(app.navigation.views[123].screen)
            await app.callback('nav:account')
            assert 'nav:manage' in routes(app.navigation.views[123].screen)
            await app.callback('nav:manage')
            assert 'nav:smart' in routes(app.navigation.views[123].screen)
            await app.callback('nav:smart')
            screen = app.navigation.views[123].screen
            assert 'Kuda Smart' in screen.text
            assert 'не включаются' in screen.text
            assert 'Автоматическое переключение при обрыве пока не подтверждено' in screen.text
            assert routes(screen) == {'nav:connect', 'nav:home'}
            await app.callback('nav:home')
            assert 'nav:smart' not in routes(app.navigation.views[123].screen)
            await app.callback('nav:manage')
            assert 'nav:smart' in routes(app.navigation.views[123].screen)
        finally:
            await app.close()
    asyncio.run(run())


def test_production_menu_does_not_advertise_smart():
    from bot.screens import home
    assert 'nav:smart' not in routes(home())


def test_smart_screen_mobile_payload_is_readable_and_has_no_mode_controls():
    """Android/iOS receive the same compact Telegram text and inline markup."""
    class Tags(HTMLParser):
        def __init__(self):
            super().__init__()
            self.open = []

        def handle_starttag(self, tag, attrs):
            assert tag in {'b', 'tg-emoji'}
            self.open.append(tag)

        def handle_endtag(self, tag):
            assert self.open.pop() == tag

    for screen in (kuda_smart(), decorate(kuda_smart())):
        parser = Tags()
        parser.feed(screen.text)
        assert not parser.open
        assert len(screen.text) < 1024  # Telegram limit is 4096 characters.
        assert 'Сейчас эти режимы не включаются' in screen.text
        assert 'подписка работает как прежде' in screen.text
        buttons = [button for row in screen.keyboard.inline_keyboard for button in row]
        assert [button.callback_data for button in buttons] == ['nav:connect', 'nav:home']
        assert buttons[0].text in {'🚀 Подключить VPN', 'Подключить VPN'}
        assert buttons[1].text == '⬅️ Назад'
        assert all(button.url is None and len(button.text) <= 32
                   and len(button.callback_data.encode()) <= 64 for button in buttons)


def test_smart_navigation_back_connect_and_home_keep_one_message():
    async def run():
        services = Services.demo()
        services.premium_account_enabled = True
        services.happ = AsyncMock()
        services.happ.issue_for_user.return_value = 'https://subscriptions.example/s/' + 'a' * 43
        app = TelegramHarness(services=services)
        try:
            await app.text('/start')
            original_message = app.navigation.views[123].message_id
            for route, expected in (
                ('nav:smart', 'режимы не включаются'),
                ('nav:home', 'Свобода начинается здесь.'),
                ('nav:smart', 'режимы не включаются'),
                ('nav:connect', 'Подключение через'),
                ('nav:happ', 'Android и iPhone'),
                ('nav:connect', 'Подключение через'),
                ('nav:home', 'Свобода начинается здесь.'),
            ):
                acknowledged = sum(isinstance(call, AnswerCallbackQuery) for call in app.calls)
                await app.callback(route)
                assert expected in app.navigation.views[123].screen.text
                assert app.navigation.views[123].message_id == original_message
                assert sum(isinstance(call, AnswerCallbackQuery) for call in app.calls) == acknowledged + 1
            assert sum(isinstance(call, SendMessage) for call in app.calls) == 1
            services.happ.issue_for_user.assert_awaited_with(123)
        finally:
            await app.close()
    asyncio.run(run())


def test_smart_callback_cannot_enable_screen_without_pilot_flag():
    async def run():
        services = Services.demo()
        services.happ = AsyncMock()
        app = TelegramHarness(services=services)
        try:
            await app.text('/start')
            await app.callback('nav:smart')  # A saved or forged old callback.
            screen = app.navigation.views[123].screen
            assert 'Kuda Smart' not in screen.text
            assert 'nav:smart' not in routes(screen)
            services.happ.issue_for_user.assert_not_awaited()
        finally:
            await app.close()
    asyncio.run(run())


def test_opt_in_header_prototype_requires_provider_and_two_nodes():
    with pytest.raises(SmartPrototypeUnavailable):
        happ_launch_selection_headers(provider_id=None, healthy_nodes=2)
    with pytest.raises(SmartPrototypeUnavailable):
        happ_launch_selection_headers(provider_id='pilot', healthy_nodes=1)
    with pytest.raises(SmartPrototypeUnavailable):
        happ_launch_selection_headers(provider_id='bad\r\nheader', healthy_nodes=2)
    assert happ_launch_selection_headers(provider_id='pilot1', healthy_nodes=2) == {
        'providerid': 'pilot1',
        'subscription-ping-onopen-enabled': '1',
        'subscription-autoconnect': '1',
        'subscription-autoconnect-type': 'lowestdelay',
    }


def test_live_feed_does_not_emit_smart_directives():
    from aiohttp.test_utils import TestClient, TestServer
    from bot.subscription_api.app import create_app

    class Feed:
        async def feed(self, token):
            assert token == 'a' * 43
            return 'vless://fixture@example.invalid:10443\n', 1790000000

    async def run():
        async with TestClient(TestServer(create_app(Feed()))) as client:
            response = await client.get('/s/' + 'a' * 43)
            assert response.status == 200
            assert (await response.text()).startswith('vless://')
            for name in ('providerid', 'subscription-autoconnect',
                         'subscription-ping-onopen-enabled'):
                assert name not in response.headers
    asyncio.run(run())
