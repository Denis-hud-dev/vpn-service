"""One canonical RUB account; no invoices or external payment steering in bot."""
import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

from aiogram.methods import SendInvoice
from sqlalchemy import select

from bot.db.models import Subscription
from bot.handlers.subscription import account_for_user
from bot.screens import home,tariffs,tariff_details
from bot.services import Services,PILOT_TARIFFS
from bot.services.payment_channels import ChannelFlags,ChannelPolicy,website_methods
from bot.services.payment_notifications import PaymentNotificationProcessor
from bot.services.payments import PaymentCheckoutService
from bot.services.subscriptions import utcnow,as_utc
from tests.fake_telegram import TelegramHarness
from tests.test_yookassa_test_mode import setup


def test_premium_five_actions_have_no_payment_url_or_stars():
    for reply in (True,False):
        screen=home(premium_account=True,reply_navigation=reply)
        buttons=[b for row in screen.keyboard.inline_keyboard for b in row]
        assert [b.callback_data for b in buttons]==['nav:tariffs','nav:account','nav:connect','nav:support','nav:info']
        assert [b.text for b in buttons]==['💎 Купить VPN','👤 Моя подписка','🚀 Подключиться','💬 Поддержка','ℹ️ Информация']
        assert not any(b.url or b.pay or b.web_app for b in buttons)
    for screen in [tariffs(premium_account=True)]+[tariff_details(key,pilot=True) for key in PILOT_TARIFFS]:
        assert 'Stars' not in screen.text and 'XTR' not in screen.text
        assert 'http' not in screen.text


def test_retired_payment_callbacks_never_expose_stars_or_create_payments():
    async def run():
        services=Services.demo();services.premium_account_enabled=True
        services.test_checkout=AsyncMock();app=TelegramHarness(services=services)
        try:
            await app.text('/start')
            for payload in ('paytest:pilot_month_199','paymentpreview:pilot_month_199',
                            'paymentinfo:pilot_month_199:stars','paymentinfo:pilot_month_199:sbp'):
                await app.callback(payload)
                screen=app.navigation.views[123].screen
                assert 'Stars' not in screen.text and 'XTR' not in screen.text
                assert 'не создаётся' in screen.text
                assert not any(b.url or b.pay or b.web_app for row in screen.keyboard.inline_keyboard for b in row)
            assert not any(isinstance(call,SendInvoice) for call in app.calls)
            services.test_checkout.create_for_user.assert_not_awaited()
        finally:await app.close()
    asyncio.run(run())


def test_website_lists_only_card_and_sbp_with_honest_sandbox_gate():
    flags=ChannelFlags(card=True,sbp=True,stars=True)
    options=website_methods(ChannelPolicy(flags,yookassa_sbp_confirmed=True))
    assert [row['id'] for row in options]==['bank_card','sbp']
    assert options[0]['available'] and not options[1]['available']
    assert options[1]['reason']=='sbp_unavailable_in_test_shop'
    live=website_methods(ChannelPolicy(flags,yookassa_test_shop=False,yookassa_sbp_confirmed=True))
    assert all(row['available'] for row in live)
    unconfirmed=website_methods(ChannelPolicy(flags,yookassa_test_shop=False))
    assert not unconfirmed[1]['available']
    assert not any(row['available'] for row in website_methods(ChannelPolicy(flags),read_only=True))


def test_website_payment_renews_the_same_subscription_visible_to_bot(tmp_path):
    async def run():
        engine,db,fake,client,_=await setup(tmp_path)
        try:
            services=Services.persistent(db,trial_owner_id=501,premium_account_enabled=True)
            checkout=PaymentCheckoutService(db,client,'https://site.example/account',tariffs=PILOT_TARIFFS)
            processor=PaymentNotificationProcessor(db,client)
            identity=None
            for plan in ('pilot_month_199','pilot_quarter_499'):
                intent=await checkout.create_for_user(501,plan)
                fake.rows[intent.provider_id].update(status='succeeded',paid=True,captured_at=utcnow().isoformat())
                await processor.process(intent.provider_id)
                snapshot=await services.subscriptions.snapshot(501)
                assert snapshot.status=='active' and snapshot.tariff_label==PILOT_TARIFFS[plan].label
                screen=await account_for_user(services,501)
                assert PILOT_TARIFFS[plan].label in screen.text
                async with db() as session:
                    sub=await session.scalar(select(Subscription));current=sub.id
                    expiry=as_utc(sub.expires_at)
                assert identity is None or identity==current
                identity=current
                await processor.process(intent.provider_id)
                assert (await services.subscriptions.snapshot(501)).expires_at==expiry
        finally:await engine.dispose()
    asyncio.run(run())
