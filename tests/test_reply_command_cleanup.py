"""Only exact incoming pilot reply commands are removed; durable history survives."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from aiogram.methods import DeleteMessage, SendMessage
from aiogram.types import Message, ReplyKeyboardMarkup, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.db.models import Base
from bot.handlers.start import remove_reply_command
from bot.keyboards.reply import CONNECT, ACCOUNT, INFO
from bot.navigation import Navigation
from bot.services import Services
from bot.services.navigation import PostgresScreenStore
from bot.services.onboarding import MemoryPilotOnboarding
from tests.fake_telegram import TelegramHarness


def pilot():
    services = Services.demo()
    services.premium_account_enabled = services.pilot_welcome_enabled = True
    services.onboarding = MemoryPilotOnboarding()
    services.onboarding.known.add(123)
    return services


async def incoming(app, text):
    mid = app.update_id + 1
    message = Message.model_validate({'message_id': mid, 'date': 1,
        'chat': {'id': 123, 'type': 'private'},
        'from': {'id': 123, 'is_bot': False, 'first_name': 'Owner'}, 'text': text}).as_(app.bot)
    app.messages[(123, mid)] = message
    await app.text(text)
    return mid


@pytest.mark.parametrize('label,heading', [(CONNECT, 'Happ'), (ACCOUNT, 'Kuda Status'), (INFO, 'Информация')])
def test_each_reply_command_deletes_itself_and_previous_navigation_only(label, heading):
    async def run():
        app = TelegramHarness(services=pilot())
        try:
            await app.text('/start')
            old = app.navigation.views[123].message_id
            receipt = await app.bot.send_message(123, 'Оплата подтверждена')
            foreign = await app.bot.send_message(456, 'Other chat')
            app.calls.clear()
            mid = await incoming(app, label)
            deleted = {(c.chat_id, c.message_id) for c in app.calls if isinstance(c, DeleteMessage)}
            assert deleted == {(123, mid), (123, old)}
            assert (123, mid) not in app.messages and (123, old) not in app.messages
            assert (123, receipt.message_id) in app.messages and (456, foreign.message_id) in app.messages
            assert heading in app.navigation.views[123].screen.text
            assert not any(isinstance(c, SendMessage) and isinstance(c.reply_markup, ReplyKeyboardRemove)
                           for c in app.calls)
        finally:
            await app.close()
    asyncio.run(run())


@pytest.mark.parametrize('label', [CONNECT, ACCOUNT, INFO])
def test_delete_refusal_does_not_break_navigation(label):
    async def run():
        app = TelegramHarness(services=pilot())
        try:
            await app.text('/start')
            old = app.navigation.views[123].message_id
            app.fail_delete = 'message cannot be deleted'
            mid = await incoming(app, label)
            assert (123, mid) in app.messages
            assert app.navigation.views[123].message_id != old
            app.fail_delete = None
            await incoming(app, INFO)
            assert 'Информация' in app.navigation.views[123].screen.text
        finally:
            await app.close()
    asyncio.run(run())


def test_repeated_commands_keep_notices_banner_normal_text_and_keyboard():
    async def run():
        app = TelegramHarness(services=pilot())
        try:
            await app.text('/start')
            banner = await app.bot.send_photo(123, 'offline_banner', caption='Добро пожаловать')
            notices = [await app.bot.send_message(123, text) for text in (
                'Оплата подтверждена', 'Подписка продлена', 'Доступ скоро истекает')]
            normal = await incoming(app, 'Вопрос для поддержки')
            commands = []
            for label in (CONNECT, ACCOUNT, INFO) * 3:
                commands.append(await incoming(app, label))
            assert all((123, mid) not in app.messages for mid in commands)
            assert (123, normal) in app.messages
            assert all((123, msg.message_id) in app.messages for msg in (banner, *notices))
            assert any(isinstance(c, SendMessage) and isinstance(c.reply_markup, ReplyKeyboardMarkup)
                       and c.reply_markup.is_persistent for c in app.calls)
            deleted = {(c.chat_id, c.message_id) for c in app.calls if isinstance(c, DeleteMessage)}
            assert all((123, msg.message_id) not in deleted for msg in (banner, *notices))
            assert not any(chat != 123 for chat, _ in deleted)
        finally:
            await app.close()
    asyncio.run(run())


@pytest.mark.parametrize('patch', [
    {'chat': {'id': -10, 'type': 'group', 'title': 'Group'}},
    {'chat': {'id': 456, 'type': 'private'}},
    {'from': {'id': 123, 'is_bot': True, 'first_name': 'Bot'}},
    {'text': 'обычный текст'},
    {'forward_origin': {'type': 'user', 'date': 1, 'sender_user': {'id': 999, 'is_bot': False, 'first_name': 'Other'}}},
])
def test_cleanup_guards_never_target_other_or_normal_messages(patch):
    async def run():
        app = TelegramHarness(services=pilot())
        try:
            raw = {'message_id': 77, 'date': 1, 'chat': {'id': 123, 'type': 'private'},
                   'from': {'id': 123, 'is_bot': False, 'first_name': 'Owner'}, 'text': INFO}
            raw.update(patch)
            await remove_reply_command(Message.model_validate(raw).as_(app.bot), app.services)
            assert not any(isinstance(c, DeleteMessage) for c in app.calls)
        finally:
            await app.close()
    asyncio.run(run())


def test_cleanup_is_pilot_only_and_timeout_is_nonfatal():
    async def run():
        services = pilot()
        message = AsyncMock()
        message.from_user.id = message.chat.id = 123
        message.from_user.is_bot = False
        message.chat.type = 'private'
        message.text = INFO
        message.forward_origin = message.sender_chat = message.via_bot = None
        services.pilot_welcome_enabled = False
        await remove_reply_command(message, services)
        message.delete.assert_not_called()
        services.pilot_welcome_enabled = True
        message.delete.side_effect = TimeoutError()
        await remove_reply_command(message, services)
        message.delete.assert_awaited_once()
    asyncio.run(run())


def test_restart_uses_saved_navigation_pointer_without_deleting_notices(tmp_path):
    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'screens.db'))
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = PostgresScreenStore(sessions)
        app = TelegramHarness(services=pilot(), store=store)
        try:
            await app.text('/start')
            await incoming(app, ACCOUNT)
            previous = (await store.load(123)).message_id
            notice = await app.bot.send_message(123, 'Подписка продлена')
            app.navigation = Navigation(PostgresScreenStore(sessions))
            app.dispatcher['navigation'] = app.navigation
            app.calls.clear()
            mid = await incoming(app, INFO)
            assert (await store.load(123)).message_id != previous
            assert (123, mid) not in app.messages and (123, previous) not in app.messages
            assert (123, notice.message_id) in app.messages
            assert 'Информация' in app.navigation.views[123].screen.text
        finally:
            await app.close()
            await engine.dispose()
    asyncio.run(run())
