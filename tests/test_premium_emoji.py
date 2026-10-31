"""The isolated test bot uses published IDs, with safe legacy fallback."""
import asyncio

from aiogram.methods import EditMessageText, SendMessage

from bot.config import Config
from bot.premium_emoji import EMOJI, PACK_NAME, decorate
from bot.screens import account, connect, home, tariffs
from bot.services import Services
from bot.services.account import AccountSnapshot
from tests.fake_telegram import TelegramHarness


def test_published_pack_has_thirty_distinct_real_ids_and_semantic_fallbacks():
    assert PACK_NAME == 'KudaVPNPremium'
    assert len(EMOJI) == len({icon.id for icon in EMOJI.values()}) == 30
    assert all(icon.id.isdecimal() and len(icon.id) >= 16 and icon.alt for icon in EMOJI.values())


def test_configuration_can_only_enable_emoji_in_isolated_premium_pilot(monkeypatch):
    monkeypatch.setenv('PILOT_CUSTOM_EMOJI_ENABLED', '1')
    monkeypatch.setenv('PILOT_PREMIUM_ACCOUNT_ENABLED', '1')
    monkeypatch.setenv('PILOT_TRIAL_OWNER_ID', '123')
    monkeypatch.setenv('BOT_TOKEN', '123456:offline_test_token')
    assert not Config.from_env().pilot_custom_emoji_enabled
    monkeypatch.setenv('KUDAVPN_PILOT_MARKER', 'kudavpn-telegram-pilot-v1')
    assert Config.from_env().pilot_custom_emoji_enabled
    monkeypatch.setenv('PILOT_PREMIUM_ACCOUNT_ENABLED', '0')
    assert not Config.from_env().pilot_custom_emoji_enabled


def test_supported_html_and_button_icons_preserve_callbacks_and_private_link():
    link = 'https://subscriptions.example/s/' + 'a' * 43
    original = connect(link, page_url='https://subscriptions.example/page')
    branded = decorate(original)
    assert '<tg-emoji emoji-id="' + EMOJI['premium'].id + '">💎</tg-emoji>' in branded.text
    assert '<tg-emoji emoji-id="' + EMOJI['connect'].id + '">🔗</tg-emoji>' in branded.text
    assert '<code>' + link + '</code>' in branded.text
    original_buttons = [button for row in original.keyboard.inline_keyboard for button in row]
    branded_buttons = [button for row in branded.keyboard.inline_keyboard for button in row]
    assert [(b.callback_data, b.url) for b in branded_buttons] == [
        (b.callback_data, b.url) for b in original_buttons]
    assert branded_buttons[0].icon_custom_emoji_id == EMOJI['premium'].id
    assert branded_buttons[1].icon_custom_emoji_id == EMOJI['subscription'].id
    assert branded_buttons[-1].text == 'Назад'
    assert branded_buttons[-1].icon_custom_emoji_id == EMOJI['back'].id
    assert original_buttons[0].text.startswith('✦')


def test_account_tariffs_and_menu_receive_relevant_icons_without_duplicate_text():
    for screen, expected in (
        (home(premium_account=True), 'premium'),
        (account(AccountSnapshot(status='active'), premium_account=True), 'status'),
        (tariffs(test_payment=True), 'plans'),
    ):
        branded = decorate(screen)
        assert f'emoji-id="{EMOJI[expected].id}"' in branded.text
        assert all(button.text for row in branded.keyboard.inline_keyboard for button in row)


def test_navigation_falls_back_to_plain_buttons_when_telegram_denies_custom_emoji():
    async def run():
        services = Services.demo()
        services.premium_account_enabled = True
        app = TelegramHarness(services=services)
        app.navigation.custom_emoji = True
        try:
            app.fail_custom_emoji = True
            await app.text('/start')
            assert app.navigation.custom_emoji is False
            sent = [call for call in app.calls if isinstance(call, SendMessage)]
            assert len(sent) == 2
            assert '<tg-emoji' in sent[0].text
            assert '<tg-emoji' not in sent[1].text
            assert sent[1].reply_markup.inline_keyboard[0][0].text == '💎 Купить VPN'
            await app.callback('nav:tariffs')
            assert [call for call in app.calls if isinstance(call, EditMessageText)]
        finally:
            await app.close()
    asyncio.run(run())


def test_existing_branded_screen_can_be_adopted_after_restart():
    async def run():
        services = Services.demo()
        services.premium_account_enabled = True
        app = TelegramHarness(services=services)
        app.navigation.custom_emoji = True
        try:
            await app.text('/start')
            old = app.navigation.views[123].message_id
            app.navigation.views.clear()
            await app.callback('nav:tariffs', message_id=old)
            assert app.navigation.views[123].message_id == old
        finally:
            await app.close()
    asyncio.run(run())
