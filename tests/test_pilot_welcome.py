"""Pilot first-run greeting, persistent keyboard, and hybrid navigation."""
import asyncio
import os
from unittest.mock import AsyncMock
from aiogram.methods import SendMessage, SendPhoto, DeleteMessage
from aiogram.types import ReplyKeyboardMarkup
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from bot.db.models import Base, User
from bot.services import Services
from bot.services.onboarding import MemoryPilotOnboarding, PilotOnboardingService
from bot.services.account import AccountSnapshot
from tests.fake_telegram import TelegramHarness


def test_first_start_preserves_banner_and_reply_navigation():
    async def run():
        services = Services.demo()
        services.premium_account_enabled = services.pilot_welcome_enabled = True
        services.onboarding = MemoryPilotOnboarding()
        services.subscriptions.snapshot = AsyncMock(return_value=AccountSnapshot(status='inactive'))
        app = TelegramHarness(services=services)
        try:
            await app.text('/start')
            banners = [call for call in app.calls if isinstance(call, SendPhoto)]
            assert len(banners) == 1
            assert 'Свобода быть на связи' in banners[0].caption
            assert banners[0].reply_markup.inline_keyboard[0][0].callback_data == 'pilot:begin'
            banner_id = app.sequence
            await app.callback('pilot:begin', message_id=banner_id + 99)
            assert app.navigation.views.get(123) is None
            await app.callback('pilot:begin', message_id=banner_id)
            keyboard = [call for call in app.calls if isinstance(call, SendMessage)
                        and isinstance(call.reply_markup, ReplyKeyboardMarkup)]
            assert len(keyboard) == 1
            assert [[button.text for button in row] for row in keyboard[0].reply_markup.keyboard] == [
                ['🔐 Подключиться'], ['👤 Личный кабинет', 'ℹ️ Информация']]
            menu_id = app.navigation.views[123].message_id
            await app.text('ℹ️ Информация')
            assert app.navigation.views[123].message_id == menu_id
            assert 'политика конфиденциальности пока не опубликованы' in app.navigation.views[123].screen.text
            await app.text('👤 Личный кабинет')
            assert app.navigation.views[123].message_id == menu_id
            await app.text('🔐 Подключиться')
            assert app.navigation.views[123].message_id == menu_id
            assert 'Happ' in app.navigation.views[123].screen.text
            await app.text('/start')
            assert len([call for call in app.calls if isinstance(call, SendPhoto)]) == 1
            assert len([call for call in app.calls if isinstance(call, SendMessage)
                        and isinstance(call.reply_markup, ReplyKeyboardMarkup)]) == 1
            assert (123, banner_id) in app.messages
            assert not any(isinstance(call, DeleteMessage) and call.message_id == banner_id for call in app.calls)
        finally:
            await app.close()
    asyncio.run(run())


def test_old_user_gets_keyboard_without_first_run_banner():
    async def run():
        services = Services.demo()
        services.premium_account_enabled = services.pilot_welcome_enabled = True
        services.onboarding = MemoryPilotOnboarding()
        services.onboarding.known.add(123)
        app = TelegramHarness(services=services)
        try:
            await app.text('/start')
            await app.text('/start')
            assert not any(isinstance(call, SendPhoto) for call in app.calls)
            assert len([call for call in app.calls if isinstance(call, SendMessage)
                        and isinstance(call.reply_markup, ReplyKeyboardMarkup)]) == 1
        finally:
            await app.close()
    asyncio.run(run())


def test_pilot_ledger_survives_restart_without_shared_database_migration(tmp_path):
    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'users.sqlite'))
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        state = tmp_path / 'private' / 'welcome.sqlite'
        first = PilotOnboardingService(sessions, str(state))
        assert not await first.user_exists(123)
        assert await first.first_start(123, new_user=True)
        await first.record_welcome(123, 501)
        assert await first.claim_keyboard(123)
        async with sessions.begin() as session:
            session.add(User(telegram_id=123, first_name='Tester'))
        restarted = PilotOnboardingService(sessions, str(state))
        assert await restarted.user_exists(123)
        assert not await restarted.first_start(123, new_user=False)
        assert await restarted.valid_welcome(123, 501)
        assert not await restarted.valid_welcome(456, 501)
        assert not await restarted.claim_keyboard(123)
        assert os.stat(state).st_mode & 0o777 == 0o600
        await engine.dispose()
    asyncio.run(run())
