"""Isolated Premium UI and verified, owner-only Telegram notices."""
import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

from aiogram.methods import AnswerCallbackQuery, SendMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.db.models import Base, Payment, PremiumNotice, Subscription, SubscriptionHistory, User
from bot.services import Services
from bot.services.account import AccountSnapshot
from bot.services.premium_notices import PremiumNoticeService
from bot.services.subscriptions import utcnow
from tests.fake_telegram import TelegramHarness


def test_premium_menu_account_devices_help_and_renewal():
    async def run():
        services = Services.demo()
        services.premium_account_enabled = True
        services.subscriptions.snapshot = AsyncMock(return_value=AccountSnapshot(
            status='active', tariff_label='1 месяц — 299 ₽', expires_at=utcnow() + timedelta(days=10)))
        app = TelegramHarness(services=services)
        try:
            await app.text('/start')
            menu = app.navigation.views[123].screen
            assert [button.callback_data for row in menu.keyboard.inline_keyboard for button in row] == [
                'nav:connect', 'nav:account', 'nav:tariffs', 'nav:smart', 'nav:help']
            for route, title in [('nav:account', 'Личный кабинет'), ('nav:renew', 'Тарифы'),
                                 ('nav:subscription', 'Моя подписка'),
                                 ('nav:devices', 'Мои устройства'), ('nav:traffic', 'Мой трафик'),
                                 ('nav:billing', 'Оплата и автопродление'),
                                 ('nav:gift', 'Подарить подписку'), ('nav:invite', 'Пригласить друга'),
                                 ('nav:help', 'Помощь'),
                                 ('nav:happ', 'Настройка Happ'), ('nav:home', 'KudaVPN Premium')]:
                await app.callback(route)
                assert title in app.navigation.views[123].screen.text
            await app.callback('nav:devices')
            assert 'отключение пока недоступны' in app.navigation.views[123].screen.text
            assert not [method for method in app.calls if isinstance(method, SendMessage)][1:]
        finally:
            await app.close()
    asyncio.run(run())


def test_premium_client_navigation_and_unrecognised_text_keep_one_screen():
    async def run():
        services = Services.demo()
        services.premium_account_enabled = True
        services.subscriptions.snapshot = AsyncMock(return_value=AccountSnapshot(
            status='active', tariff_label='1 месяц — 299 ₽',
            expires_at=utcnow() + timedelta(days=10)))
        services.happ = AsyncMock()
        services.happ.issue_for_user.return_value = 'https://subscriptions.example/s/' + 'a' * 43
        app = TelegramHarness(services=services)
        try:
            await app.text('/start')
            screen_id = app.navigation.views[123].message_id
            for route, expected in (
                ('nav:account', 'Действует до:'),
                ('nav:subscription', 'Действует до:'),
                ('nav:renew', 'Тарифы'),
                ('tariff:month', '1 месяц — 299 ₽'),
                ('nav:home', 'Ваше пространство подключения.'),
                ('nav:devices', 'отключение пока недоступны'),
                ('nav:traffic', 'Нет данных'),
                ('nav:billing', 'Автопродление'),
                ('nav:gift', 'пока не выдаются'),
                ('nav:invite', 'не запущены'),
                ('nav:connect', 'https://subscriptions.example/s/'),
                ('nav:happ', 'Настройка Happ'),
                ('nav:help', 'Как подключиться?'),
                ('nav:home', 'Ваше пространство подключения.'),
            ):
                before = len([call for call in app.calls if isinstance(call, AnswerCallbackQuery)])
                await app.callback(route)
                assert expected in app.navigation.views[123].screen.text
                assert len([call for call in app.calls if isinstance(call, AnswerCallbackQuery)]) == before + 1
                assert app.navigation.views[123].message_id == screen_id
            await app.text('старое меню')
            buttons = [button.callback_data for row in app.navigation.views[123].screen.keyboard.inline_keyboard
                       for button in row]
            assert buttons == ['nav:connect', 'nav:account', 'nav:tariffs', 'nav:smart', 'nav:help']
            assert app.navigation.views[123].message_id == screen_id
            assert len([call for call in app.calls if isinstance(call, SendMessage)]) == 1
            services.happ.issue_for_user.assert_awaited_once_with(123)
        finally:
            await app.close()
    asyncio.run(run())


def test_verified_payment_notice_is_owner_only_and_once(tmp_path):
    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'notice.db'))
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
            paid = Payment(user_id=owner.id, subscription_id=sub.id, tariff_id='month',
                           amount_kopecks=29900, status='succeeded', paid_at=now)
            pending = Payment(user_id=owner.id, tariff_id='year',
                              amount_kopecks=249000, status='pending')
            foreign = Payment(user_id=stranger.id, tariff_id='month',
                              amount_kopecks=29900, status='succeeded')
            session.add_all((paid, pending, foreign))
            await session.flush()
            session.add(SubscriptionHistory(subscription_id=sub.id, payment_id=paid.id,
                reason='payment', new_status='active', new_starts_at=now,
                new_expires_at=sub.expires_at))
        bot = AsyncMock()
        notice = PremiumNoticeService(sessions, 123)
        try:
            await notice.tick(bot)
            await notice.tick(bot)
            assert bot.send_message.await_count == 1
            args = bot.send_message.await_args.args
            assert args[0] == 123 and 'Тестовая оплата подтверждена' in args[1]
            assert 'http' not in args[1] and 'UUID' not in args[1]
            async with sessions() as session:
                rows = (await session.scalars(select(PremiumNotice))).all()
                assert len(rows) == 1 and rows[0].sent_at is not None
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_expiry_reminder_dedupes_and_renewal_invalidates_old_notice(tmp_path):
    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'expiry.db'))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as session:
            owner = User(telegram_id=123, first_name='Owner')
            session.add(owner)
            await session.flush()
            sub = Subscription(user_id=owner.id, tariff_id='month', status='active',
                               starts_at=utcnow() - timedelta(days=29),
                               expires_at=utcnow() + timedelta(hours=12))
            session.add(sub)
        bot = AsyncMock()
        notice = PremiumNoticeService(sessions, 123)
        try:
            await notice.enqueue()
            async with sessions.begin() as session:
                sub = await session.scalar(select(Subscription))
                sub.expires_at = utcnow() + timedelta(days=30)
            await notice.deliver(bot)
            bot.send_message.assert_not_awaited()
            async with sessions.begin() as session:
                sub = await session.scalar(select(Subscription))
                sub.expires_at = utcnow() + timedelta(hours=10)
            await notice.tick(bot)
            await notice.tick(bot)
            assert bot.send_message.await_count == 1
            assert 'менее суток' in bot.send_message.await_args.args[1]
        finally:
            await engine.dispose()
    asyncio.run(run())
