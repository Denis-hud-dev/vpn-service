"""Hybrid navigation, owner-scoped history, and display-only launch pricing."""
import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

from aiogram.methods import SendMessage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.db.models import Base, Subscription, SubscriptionHistory, User
from bot.screens import launch_tariffs, launch_tariff_preview, subscription_history
from bot.services import Services, TARIFFS
from bot.services.account import AccountSnapshot
from bot.services.subscriptions import SubscriptionService, utcnow
from tests.fake_telegram import TelegramHarness


def test_starter_prices_are_preview_only_and_savings_are_correct():
    screen = launch_tariffs()
    assert '1 месяц — <b>190 ₽</b>' in screen.text
    assert '3 месяца — <b>490 ₽</b>' in screen.text
    assert '12 месяцев — <b>1690 ₽</b>' in screen.text
    assert 'Экономия 80 ₽' in screen.text  # 190 × 3 − 490
    assert 'Экономия 590 ₽' in screen.text  # 190 × 12 − 1690
    assert 'пока нельзя оплатить' in screen.text
    buttons = [button for row in screen.keyboard.inline_keyboard for button in row]
    assert [button.callback_data for button in buttons] == [
        'launchplan:month', 'launchplan:quarter', 'launchplan:year', 'nav:tariffs', 'nav:home']
    assert [button.style for button in buttons[:3]] == ['success', 'primary', 'success']
    assert [button.model_dump(exclude_none=True)['style'] for button in buttons[:3]] == [
        'success', 'primary', 'success']
    assert all('скоро' in button.text for button in buttons[:3])
    assert 'Максимальная выгода' in screen.text
    assert 'заказ не создаётся' in launch_tariff_preview('year').text
    assert not any(button.callback_data.startswith(('tariff:', 'paytest:')) for button in buttons[:3])
    assert [TARIFFS[key].price_rub for key in ('month', 'quarter', 'year')] == [299, 799, 2490]


def test_history_is_scoped_to_real_subscription_owner(tmp_path):
    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'history.db'))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        now = utcnow()
        async with sessions.begin() as session:
            owner = User(telegram_id=123, first_name='Owner')
            stranger = User(telegram_id=456, first_name='Other')
            session.add_all((owner, stranger))
            await session.flush()
            sub = Subscription(user_id=owner.id, tariff_id='month', status='active',
                               starts_at=now, expires_at=now + timedelta(days=30))
            session.add(sub)
            await session.flush()
            session.add(SubscriptionHistory(subscription_id=sub.id, reason='payment',
                new_status='active', new_starts_at=now, new_expires_at=sub.expires_at))
        reader = SubscriptionService(sessions)
        try:
            own = await reader.recent_history(123)
            assert len(own) == 1 and own[0].reason == 'payment'
            assert await reader.recent_history(456) == []
            assert 'Оплата подтверждена' in subscription_history(own).text
            assert 'История платежей и чеки пока недоступны' in subscription_history(own).text
            assert 'пока нет записанных изменений' in subscription_history([]).text.lower()
            assert 'временно недоступна' in subscription_history(None).text
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_start_and_callbacks_keep_notice_separate_and_retire_old_personal_screen():
    async def run():
        services = Services.demo()
        services.premium_account_enabled = True
        services.subscriptions.snapshot = AsyncMock(return_value=AccountSnapshot(status='active'))
        services.happ = AsyncMock()
        personal = 'https://subscriptions.example/s/' + 'a' * 43
        services.happ.issue_for_user.return_value = personal
        app = TelegramHarness(services=services)
        try:
            await app.text('/start')
            await app.callback('nav:connect')
            old_id = app.navigation.views[123].message_id
            assert personal in app.messages[(123, old_id)].text
            # This independently delivered payment notice is not navigation.
            notice = app.bot_message(123, 150, 'Тестовая оплата подтверждена')
            app.messages[(123, 150)] = notice
            app.fail_delete = "Bad Request: message can't be deleted"
            app.update_id = 200
            await app.text('/start')
            assert app.messages[(123, 150)].text == 'Тестовая оплата подтверждена'
            assert personal not in app.messages[(123, old_id)].text
            assert app.messages[(123, old_id)].reply_markup is None
            assert sum(isinstance(call, SendMessage) for call in app.calls) == 2
            await app.callback('nav:account')
            await app.callback('nav:history')
            assert 'Пока нет записанных изменений' in app.navigation.views[123].screen.text
            await app.callback('nav:account')
            await app.callback('nav:tariffs')
            await app.callback('nav:launch_tariffs')
            assert '190 ₽' in app.navigation.views[123].screen.text
            await app.callback('launchplan:year')
            assert '1690 ₽' in app.navigation.views[123].screen.text
            assert 'заказ не создаётся' in app.navigation.views[123].screen.text
            await app.callback('nav:launch_tariffs')
            assert '490 ₽' in app.navigation.views[123].screen.text
            await app.callback('nav:tariffs')
            assert '299 ₽' in app.navigation.views[123].screen.text
            assert sum(isinstance(call, SendMessage) for call in app.calls) == 2
        finally:
            await app.close()
    asyncio.run(run())


def test_new_navigation_is_only_visible_in_premium_test_interface():
    async def run():
        services = Services.demo()
        services.premium_account_enabled = False
        app = TelegramHarness(services=services)
        try:
            await app.text('/start')
            await app.callback('nav:history')
            assert 'Личный кабинет' in app.navigation.views[123].screen.text
            assert 'История подписки' not in app.navigation.views[123].screen.text
            await app.callback('nav:launch_tariffs')
            assert '190 ₽' not in app.navigation.views[123].screen.text
            assert '299 ₽' in app.navigation.views[123].screen.text
            await app.callback('launchplan:year')
            assert '1690 ₽' not in app.navigation.views[123].screen.text
        finally:
            await app.close()
    asyncio.run(run())
