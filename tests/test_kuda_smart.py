"""Kuda Smart is an honest preview; live subscriptions stay unchanged."""
import asyncio

import pytest

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
            assert 'nav:smart' in routes(app.navigation.views[123].screen)
            await app.callback('nav:smart')
            screen = app.navigation.views[123].screen
            assert 'Kuda Smart' in screen.text
            assert 'не включаются' in screen.text
            assert 'Автоматическое переключение при обрыве пока не подтверждено' in screen.text
            assert routes(screen) == {'nav:connect', 'nav:home'}
            await app.callback('nav:home')
            assert 'nav:smart' in routes(app.navigation.views[123].screen)
        finally:
            await app.close()
    asyncio.run(run())


def test_production_menu_does_not_advertise_smart():
    from bot.screens import home
    assert 'nav:smart' not in routes(home())


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
