"""Independent switches cannot bypass Telegram policy or test-shop capabilities."""
import asyncio

import pytest
from aiogram import Dispatcher
from aiogram.methods import SendInvoice,AnswerCallbackQuery

from bot.handlers import create_router
from bot.handlers.payment_preview import create_router as preview_router,methods_screen
from bot.services import PILOT_TARIFFS
from bot.services.payment_channels import Channel,Surface,ChannelFlags,ChannelPolicy
from tests.fake_telegram import TelegramHarness


def test_switches_default_off_and_do_not_override_platform_rules():
    policy=ChannelPolicy()
    for surface in Surface:
        for channel in Channel:assert not policy.decision(channel,surface).allowed
    flags=ChannelFlags(stars=True,card=True,sbp=True)
    ready=ChannelPolicy(flags,telegram_test_transport_ready=True,canonical_xtr_ledger_ready=True,
                        yookassa_test_shop=False,yookassa_sbp_confirmed=True)
    assert ready.decision(Channel.STARS,Surface.TELEGRAM).allowed
    for channel in (Channel.CARD,Channel.SBP):
        assert ready.decision(channel,Surface.TELEGRAM).reason=='telegram_requires_stars'
        with pytest.raises(PermissionError):ready.require(channel,Surface.TELEGRAM)
    assert ready.decision(Channel.STARS,Surface.WEBSITE).reason=='native_telegram_only'


def test_stars_requires_real_test_transport_and_canonical_ledger():
    flags=ChannelFlags(stars=True)
    assert ChannelPolicy(flags).decision('stars','telegram').reason=='telegram_test_bot_required'
    assert ChannelPolicy(flags,telegram_test_transport_ready=True).decision('stars','telegram').reason=='canonical_xtr_ledger_required'


def test_test_shop_sbp_is_not_enabled_by_switch_or_capability_claim():
    flags=ChannelFlags(card=True,sbp=True)
    policy=ChannelPolicy(flags,yookassa_sbp_confirmed=True)
    assert policy.decision('card','website').allowed
    assert policy.decision('sbp','website').reason=='sbp_unavailable_in_test_shop'
    assert ChannelPolicy(flags,yookassa_test_shop=False).decision('sbp','website').reason=='sbp_not_confirmed'
    assert not ChannelPolicy(ChannelFlags(card='1')).decision('card','website').allowed


@pytest.mark.parametrize('tariff',list(PILOT_TARIFFS))
def test_preview_contains_only_card_and_sbp_nonpayment_choices(tariff):
    screen=methods_screen(tariff)
    buttons=[b for row in screen.keyboard.inline_keyboard for b in row]
    choices=[b for b in buttons if (b.callback_data or '').startswith('paymentinfo:')]
    assert len(choices)==2 and 'макет' in screen.text
    assert all(not b.url and not b.pay and not b.web_app for b in buttons)
    assert all(len(b.callback_data.encode())<=64 for b in buttons)
    assert all('Stars' not in b.text for b in buttons)
    assert 'Stars' not in screen.text and 'XTR' not in screen.text
    assert all('недоступно' in b.text for b in choices)


def test_all_preview_callbacks_are_informational_and_owner_only():
    async def run():
        app=TelegramHarness()
        await app.dispatcher.storage.close()
        app.dispatcher=Dispatcher(config=app.config,services=app.services,navigation=app.navigation)
        app.dispatcher.include_router(preview_router(123))
        app.dispatcher.include_router(create_router())
        try:
            await app.text('/start')
            await app.callback('paymentpreview:pilot_month_199')
            assert 'Способ оплаты · макет' in app.navigation.views[123].screen.text
            for channel in Channel:
                await app.callback('paymentinfo:pilot_month_199:'+channel.value)
                assert 'Оплата · информация' in app.navigation.views[123].screen.text
            await app.callback('nav:tariffs')
            assert 'Тарифы' in app.navigation.views[123].screen.text
            previous=app.navigation.views[123].screen
            for payload in ('paymentinfo:pilot_month_199:card:123','paymentinfo:pilot_month_199:other',
                            'paymentpreview:unknown'):
                await app.callback(payload)
                assert app.navigation.views[123].screen==previous
            await app.callback('paymentpreview:pilot_month_199',inline=True)
            assert app.navigation.views[123].screen==previous
            await app.text('/start',user_id=502)
            foreign=app.navigation.views[502].screen
            await app.callback('paymentpreview:pilot_month_199',user_id=502)
            assert app.navigation.views[502].screen==foreign
            assert any(isinstance(call,AnswerCallbackQuery) and call.show_alert for call in app.calls)
            assert not any(isinstance(call,SendInvoice) for call in app.calls)
        finally:await app.close()
    asyncio.run(run())
