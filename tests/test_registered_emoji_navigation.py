"""Verified public IDs, unchanged callback routes and real ReplyKeyboard labels."""
import asyncio
from aiogram.methods import SendMessage,SendPhoto
from aiogram.types import ReplyKeyboardMarkup
from bot import screens
from bot.premium_emoji import EMOJI,PLATFORM_EMOJI,decorate
from bot.services.account import AccountSnapshot
from tests.fake_telegram import TelegramHarness
from tests.test_start_premium_menu import pilot


def test_existing_routes_have_expected_verified_icons_without_new_controls():
    expected={'nav:tariffs':'premium','nav:account':'subscription','nav:connect':'connect',
              'nav:support':'support','nav:info':'info','nav:manage':'settings',
              'nav:renew':'renew','nav:home':'back'}
    base=screens.home(premium_account=True)
    decorated=decorate(base)
    assert [b.callback_data for row in decorated.keyboard.inline_keyboard for b in row]==[
        'nav:tariffs','nav:account','nav:connect','nav:support','nav:info']
    assert len(decorated.keyboard.inline_keyboard[0])==1
    for source in [base,screens.account(AccountSnapshot(status='active'),premium_account=True)]:
        for row in decorate(source).keyboard.inline_keyboard:
            for b in row:
                if b.callback_data in expected:
                    assert b.icon_custom_emoji_id==EMOJI[expected[b.callback_data]].id
    manage=decorate(screens.account_management())
    assert manage.keyboard.inline_keyboard[-1][0].icon_custom_emoji_id==EMOJI['home'].id
    assert set(PLATFORM_EMOJI)=={'apple','windows','android','linux','ios','macos','smart_tv'}
    assert all(icon.id in {v.id for v in EMOJI.values()} for icon in PLATFORM_EMOJI.values())


def test_branded_reply_labels_navigate_and_first_caption_is_not_repeated():
    async def run():
        services=pilot(AccountSnapshot());app=TelegramHarness(services=services)
        app.navigation.custom_emoji=True
        try:
            await app.text('/start')
            photo=next(c for c in app.calls if isinstance(c,SendPhoto))
            assert photo.parse_mode=='HTML' and len(photo.caption)<1024
            assert all(word in photo.caption for word in ('\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0442\u0430\u0440\u0438\u0444','\u041e\u0444\u043e\u0440\u043c\u0438\u0442\u0435 \u043f\u043e\u0434\u043f\u0438\u0441\u043a\u0443','Happ'))
            assert 'https://' not in photo.caption and 'Stars' not in photo.caption
            banner=services.onboarding.rows[123]['message_id']
            keyboard=next(c.reply_markup for c in app.calls if isinstance(c,SendMessage) and isinstance(c.reply_markup,ReplyKeyboardMarkup))
            assert all(b.icon_custom_emoji_id for row in keyboard.keyboard for b in row)
            for command,marker in [('\u041b\u0438\u0447\u043d\u044b\u0439 \u043a\u0430\u0431\u0438\u043d\u0435\u0442','Kuda Status'),('\u0418\u043d\u0444\u043e\u0440\u043c\u0430\u0446\u0438\u044f','KudaVPN'),('\u041f\u043e\u0434\u043a\u043b\u044e\u0447\u0438\u0442\u044c\u0441\u044f','Happ')]:
                await app.text(command)
                assert marker in app.navigation.views[123].screen.text
            await app.text('/start')
            assert len([c for c in app.calls if isinstance(c,SendPhoto)])==1
            assert (123,banner) in app.messages
            assert '\u0422\u0440\u0438 \u043f\u0440\u043e\u0441\u0442\u044b\u0445 \u0448\u0430\u0433\u0430' not in app.navigation.views[123].screen.text
        finally:await app.close()
    asyncio.run(run())


def test_reply_custom_emoji_api_denial_keeps_unicode_keyboard_and_navigation():
    async def run():
        app=TelegramHarness(services=pilot(AccountSnapshot()))
        app.navigation.custom_emoji=True;app.fail_custom_emoji=True
        try:
            await app.text('/start')
            keyboards=[c.reply_markup for c in app.calls if isinstance(c,SendMessage) and isinstance(c.reply_markup,ReplyKeyboardMarkup)]
            assert len(keyboards)==2
            assert any(b.icon_custom_emoji_id for row in keyboards[0].keyboard for b in row)
            assert not any(b.icon_custom_emoji_id for row in keyboards[1].keyboard for b in row)
            assert all(b.text[0] not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' for row in keyboards[1].keyboard for b in row)
            assert not app.navigation.custom_emoji
            await app.text('\u2139\ufe0f \u0418\u043d\u0444\u043e\u0440\u043c\u0430\u0446\u0438\u044f')
            assert 'KudaVPN' in app.navigation.views[123].screen.text
        finally:await app.close()
    asyncio.run(run())


def test_new_html_fallbacks_match_telegram_registered_alt_exactly():
    import json
    from pathlib import Path
    from bot.premium_emoji import tag
    proof=json.loads((Path(__file__).resolve().parents[1]/'assets/telegram-emoji-extension/registration.json').read_text())
    for name,entry in proof['icons'].items():
        assert EMOJI[name].alt==entry['alt']
        assert tag(name).endswith('>'+entry['alt']+'</tg-emoji>')
