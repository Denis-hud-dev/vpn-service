"""Public TEST-menu destinations through the real router, without network calls."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from aiogram.methods import (AnswerCallbackQuery, DeleteMessage, EditMessageText,
                             SendMessage, SendPhoto)
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup
from bot.config import Config
from bot.menu_links import MenuLinks, REVIEW_WEBSITE
from bot.navigation import Navigation
from bot.premium_emoji import EMOJI, decorate
from bot.screens import main_actions
from bot.services import Services
from bot.services.onboarding import MemoryPilotOnboarding
from tests.fake_telegram import TelegramHarness


def premium_config(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123456:offline_test_token")
    monkeypatch.setenv("KUDAVPN_PILOT_MARKER", "kudavpn-telegram-pilot-v1")
    monkeypatch.setenv("PILOT_TRIAL_OWNER_ID", "123")
    monkeypatch.setenv("PILOT_PREMIUM_ACCOUNT_ENABLED", "1")
    for name in ("PILOT_MENU_WEBSITE_URL", "PILOT_MENU_CHANNEL_URL", "PILOT_MENU_COMMUNITY_URL"):
        monkeypatch.delenv(name, raising=False)
    return Config.from_env()


def test_links_are_test_only_and_default_to_closed_review(monkeypatch):
    config = premium_config(monkeypatch)
    assert config.pilot_menu_links == MenuLinks()
    assert config.pilot_menu_links.website == REVIEW_WEBSITE
    monkeypatch.delenv("KUDAVPN_PILOT_MARKER")
    # Ignored entirely without the existing isolated pilot gate.
    monkeypatch.setenv("PILOT_MENU_WEBSITE_URL", "not-a-public-url")
    assert Config.from_env().pilot_menu_links is None


def test_future_destinations_are_read_from_configuration(monkeypatch):
    premium_config(monkeypatch)
    monkeypatch.setenv("PILOT_MENU_WEBSITE_URL", "https://example.org")
    monkeypatch.setenv("PILOT_MENU_CHANNEL_URL", "https://t.me/kuda_example_channel")
    monkeypatch.setenv("PILOT_MENU_COMMUNITY_URL", "https://t.me/kuda_example_group")
    links = Config.from_env().pilot_menu_links
    keyboard = main_actions(menu_links=links).keyboard
    assert [row[0].url for row in keyboard.inline_keyboard[-3:]] == [
        "https://example.org", "https://t.me/kuda_example_channel", "https://t.me/kuda_example_group"]
    assert all(row[0].callback_data is None for row in keyboard.inline_keyboard[-3:])


@pytest.mark.parametrize("field,value", [
    ("website", "http://example.org"),
    ("website", "javascript:alert(1)"),
    ("website", "https://user:password@example.org"),
    ("website", "https://example.org/checkout"),
    ("website", "https://example.org?token=private-test-value"),
    ("website", "https://example.org:invalid"),
    ("website", "https://example.org/\n"),
    ("channel", "https://t.me.attacker.invalid/example"),
    ("community", "https://t.me/"),
])
def test_invalid_destination_is_rejected_without_echoing_it(field, value):
    with pytest.raises(ValueError) as error:
        MenuLinks(**{field: value})
    assert value not in str(error.value)
    assert "private-test-value" not in str(error.value)


def test_three_separate_rows_keep_existing_actions_and_supported_url_markup():
    plain = main_actions(menu_links=MenuLinks())
    rows = plain.keyboard.inline_keyboard
    assert rows[0][0].callback_data == "nav:tariffs" and len(rows[0]) == 1
    assert {b.callback_data for row in rows[:3] for b in row} == {
        "nav:tariffs", "nav:account", "nav:connect", "nav:support", "nav:info"}
    assert [row[0].text for row in rows[-3:]] == ["🌐 Наш сайт", "📢 Наш канал", "💬 Наше сообщество"]
    site, channel, community = [row[0] for row in rows[-3:]]
    assert site.url == REVIEW_WEBSITE and site.callback_data is None and site.web_app is None
    assert channel.callback_data == "nav:channel" and channel.url is None
    assert community.callback_data == "nav:community" and community.url is None
    styled = decorate(plain).keyboard.inline_keyboard
    assert [row[0].icon_custom_emoji_id for row in styled[-3:]] == [
        EMOJI[name].id for name in ("world", "channel", "community")]
    assert styled[-3][0].url == site.url
    # Plain fallback retains the supplied Unicode labels.
    assert all(b.icon_custom_emoji_id is None for row in rows[-3:] for b in row)


def pilot_app(links=MenuLinks()):
    services = Services.demo()
    services.premium_account_enabled = services.pilot_welcome_enabled = True
    services.menu_links = links
    services.onboarding = MemoryPilotOnboarding()
    services.payments.create_payment = AsyncMock(side_effect=AssertionError("no payments"))
    return TelegramHarness(services=services)


@pytest.mark.parametrize("route,expected", [
    ("nav:channel", "📢 Официальный канал KudaVPN скоро появится. Здесь будут новости, обновления и важные объявления"),
    ("nav:community", "💬 Сообщество KudaVPN скоро откроется. Здесь можно будет общаться, задавать вопросы и делиться опытом"),
])
def test_placeholders_back_repeat_start_preserve_banner_keyboard_and_receipt(route, expected):
    async def run():
        app = pilot_app()
        try:
            await app.text("/start")
            banner = app.services.onboarding.rows[123]["message_id"]
            receipt = await app.bot.send_message(123, "Подтверждение подписки: важное событие")
            original = app.navigation.views[123].message_id
            app.calls.clear()
            for _ in range(2):
                await app.callback(route)
                assert expected in app.navigation.views[123].screen.text
                await app.callback("nav:home")
                assert app.navigation.views[123].screen.keyboard.inline_keyboard[-3][0].url == REVIEW_WEBSITE
            assert app.navigation.views[123].message_id == original
            assert not any(isinstance(c, SendMessage) for c in app.calls)
            assert len([c for c in app.calls if isinstance(c, AnswerCallbackQuery)]) == 4
            await app.text("/start")
            current = app.navigation.views[123].message_id
            assert current != original and (123, original) not in app.messages
            assert app.navigation.views[123].screen.keyboard.inline_keyboard[-3][0].url == REVIEW_WEBSITE
            assert not any(isinstance(c, SendPhoto) for c in app.calls)
            assert any(isinstance(getattr(c, "reply_markup", None), ReplyKeyboardMarkup) for c in app.calls)
            assert (123, banner) in app.messages and (123, receipt.message_id) in app.messages
            await app.callback(route, message_id=original)
            assert app.navigation.views[123].message_id == current
            app.services.payments.create_payment.assert_not_awaited()
        finally:
            await app.close()
    asyncio.run(run())


def test_destination_edit_failure_and_restart_do_not_accumulate_messages():
    async def run():
        app = pilot_app()
        try:
            await app.text("/start")
            mid = app.navigation.views[123].message_id
            app.fail_edit = "invalid reply markup"
            app.calls.clear()
            await app.callback("nav:channel")
            assert app.navigation.views[123].message_id == mid
            assert not any(isinstance(c, (SendMessage, DeleteMessage)) for c in app.calls)
            app.fail_edit = None
            app.navigation = Navigation(app.navigation.store)
            app.dispatcher["navigation"] = app.navigation
            await app.callback("nav:community", message_id=mid)
            assert "Сообщество KudaVPN скоро откроется" in app.navigation.views[123].screen.text
            assert not any(isinstance(c, SendMessage) for c in app.calls)
            await app.callback("nav:home")
            assert app.navigation.views[123].screen.keyboard.inline_keyboard[-3][0].url == REVIEW_WEBSITE
        finally:
            await app.close()
    asyncio.run(run())


def test_placeholder_remains_safe_if_channel_is_configured_after_old_menu():
    async def run():
        app = pilot_app(MenuLinks(channel="https://t.me/kuda_example_channel"))
        try:
            await app.text("/start")
            await app.callback("nav:channel")
            screen = app.navigation.views[123].screen
            assert "скоро" not in screen.text
            assert screen.keyboard.inline_keyboard[0][0].url == "https://t.me/kuda_example_channel"
        finally:
            await app.close()
    asyncio.run(run())


def test_legacy_bot_menu_and_callbacks_do_not_expose_new_destinations():
    async def run():
        app = TelegramHarness()
        try:
            await app.text("/start")
            assert not any(b.url for row in app.navigation.views[123].screen.keyboard.inline_keyboard for b in row)
            await app.callback("nav:channel")
            assert "скоро появится" not in app.navigation.views[123].screen.text
            assert not any(b.url for row in app.navigation.views[123].screen.keyboard.inline_keyboard for b in row)
        finally:
            await app.close()
    asyncio.run(run())


def test_new_button_icons_fall_back_without_losing_direct_website_url():
    async def run():
        app = pilot_app()
        app.navigation.custom_emoji = True
        app.fail_custom_emoji = True
        try:
            await app.text("/start")
            keyboard = app.navigation.views[123].screen.keyboard
            assert [row[0].text for row in keyboard.inline_keyboard[-3:]] == [
                "🌐 Наш сайт", "📢 Наш канал", "💬 Наше сообщество"]
            assert keyboard.inline_keyboard[-3][0].url == REVIEW_WEBSITE
            assert not any(b.icon_custom_emoji_id for row in keyboard.inline_keyboard for b in row)
            await app.callback("nav:community")
            assert "Сообщество KudaVPN скоро откроется" in app.navigation.views[123].screen.text
        finally:
            await app.close()
    asyncio.run(run())


def test_repeated_start_keeps_new_menu_when_old_screen_cannot_be_deleted():
    async def run():
        app = pilot_app()
        try:
            await app.text("/start")
            old = app.navigation.views[123].message_id
            banner = app.services.onboarding.rows[123]["message_id"]
            receipt = await app.bot.send_message(123, "Важное уведомление")
            app.fail_delete = "message cannot be deleted"
            await app.callback("nav:channel")
            await app.text("/start")
            current = app.navigation.views[123]
            assert current.message_id != old
            assert current.screen.keyboard.inline_keyboard[-3][0].url == REVIEW_WEBSITE
            assert app.messages[(123, old)].reply_markup is None
            assert (123, banner) in app.messages and (123, receipt.message_id) in app.messages
            await app.callback("nav:community")
            assert "Сообщество KudaVPN скоро откроется" in app.navigation.views[123].screen.text
        finally:
            await app.close()
    asyncio.run(run())
