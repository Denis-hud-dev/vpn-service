"""Persistence, safe migration and navigation-only replacement tests."""
import asyncio
from pathlib import Path
from uuid import uuid4
from unittest.mock import AsyncMock
from aiogram.methods import SendMessage, DeleteMessage, EditMessageText, AnswerCallbackQuery
from alembic import command
from alembic.config import Config
from sqlalchemy import select, inspect
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.exc import OperationalError
from bot.db.models import ActiveScreen, User
from bot.navigation import Navigation
from bot.services import Services
from bot.services.navigation import PostgresScreenStore
from tests.fake_telegram import TelegramHarness


def methods(app, kind):
    return [method for method in app.calls if isinstance(method, kind)]


async def migrated_engine(path):
    engine=create_async_engine(f'sqlite+aiosqlite:///{path}')
    async with engine.begin() as connection:
        def upgrade(conn):
            cfg=Config('alembic.ini');cfg.attributes['connection']=conn
            command.upgrade(cfg,'head')
        await connection.run_sync(upgrade)
    return engine


def test_active_screen_survives_restart_and_many_starts(tmp_path):
    async def run():
        engine=await migrated_engine(tmp_path/'screens.sqlite')
        sessions=async_sessionmaker(engine,expire_on_commit=False)
        store=PostgresScreenStore(sessions)
        app=TelegramHarness(services=Services.persistent(sessions),store=store)
        try:
            await app.text('/start')
            first=(await store.load(123)).message_id
            for _ in range(3):
                for route in ('nav:connect','nav:account','nav:tariffs','nav:traffic','nav:bonuses','nav:support','nav:home'):
                    await app.callback(route)
                app.navigation=Navigation(PostgresScreenStore(sessions))
                app.dispatcher['navigation']=app.navigation
                await app.text('/start')
            assert len(methods(app,SendMessage))==1
            assert len(app.messages)==1
            assert all(m.message_id==first for m in methods(app,EditMessageText))
            async with sessions() as session:
                row=await session.get(ActiveScreen,123)
                assert row.chat_id==123 and row.message_id==first
            # A new process rejects stale callbacks using the DB, without adoption.
            app.navigation=Navigation(store);app.dispatcher['navigation']=app.navigation
            app.calls.clear()
            await app.callback('nav:account',message_id=999)
            assert len(methods(app,AnswerCallbackQuery))==1
            assert 'устарел' in methods(app,AnswerCallbackQuery)[0].text
            assert not methods(app,SendMessage) and not methods(app,EditMessageText)
        finally:
            await app.close();await engine.dispose()
    asyncio.run(run())


def test_replaces_only_tracked_navigation_and_keeps_receipts(tmp_path):
    async def run():
        engine=await migrated_engine(tmp_path/'replace.sqlite')
        sessions=async_sessionmaker(engine,expire_on_commit=False)
        store=PostgresScreenStore(sessions)
        app=TelegramHarness(services=Services.persistent(sessions),store=store)
        try:
            await app.text()
            previous=(await store.load(123)).message_id
            for mid,body in ((900,'Оплата подтверждена'),(901,'Обращение в поддержку'),(902,'Уведомление')):
                app.messages[(123,mid)]=app.bot_message(123,mid,body)
            app.navigation=Navigation(store);app.dispatcher['navigation']=app.navigation
            app.fail_edit="message can't be edited"
            app.calls.clear()
            await app.text('/start')
            current=(await store.load(123)).message_id
            assert current!=previous
            assert len(methods(app,SendMessage))==1
            assert any(m.message_id==previous for m in methods(app,DeleteMessage))
            assert (123,previous) not in app.messages
            assert all((123,mid) in app.messages for mid in (900,901,902))
            app.fail_edit=None
            await app.text('/start')
            await app.callback('nav:home',message_id=900)
            assert len(methods(app,SendMessage))==1
            assert (await store.load(123)).message_id==current
        finally:
            await app.close();await engine.dispose()
    asyncio.run(run())


def test_delete_refusal_keeps_new_pointer_and_old_buttons_are_stale(tmp_path):
    async def run():
        engine=await migrated_engine(tmp_path/'refusal.sqlite')
        sessions=async_sessionmaker(engine,expire_on_commit=False)
        store=PostgresScreenStore(sessions)
        app=TelegramHarness(services=Services.persistent(sessions),store=store)
        try:
            await app.text()
            previous=(await store.load(123)).message_id
            app.fail_edit="message can't be edited";app.fail_delete='not enough rights'
            await app.callback('nav:traffic')
            current=(await store.load(123)).message_id
            assert previous!=current and (123,previous) in app.messages
            app.calls.clear();app.fail_edit=None
            await app.callback('nav:home',message_id=previous)
            assert not methods(app,EditMessageText) and not methods(app,SendMessage)
            assert len(methods(app,AnswerCallbackQuery))==1
            await app.text('/start')
            assert not methods(app,SendMessage)
        finally:
            await app.close();await engine.dispose()
    asyncio.run(run())


def test_migration_preserves_all_existing_user_fields(tmp_path):
    async def run():
        engine=create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "migration.sqlite"}')
        try:
            async with engine.begin() as conn:
                def migration(connection,target):
                    cfg=Config('alembic.ini');cfg.attributes['connection']=connection
                    command.upgrade(cfg,target)
                await conn.run_sync(migration,'0001_initial')
                await conn.execute(User.__table__.insert().values(id=uuid4(),telegram_id=123,
                                  username='preserved',first_name='Existing',referrer_id=456))
                before=(await conn.execute(select(User.__table__))).mappings().all()
                await conn.run_sync(migration,'head')
                after=(await conn.execute(select(User.__table__))).mappings().all()
                assert before==after
                assert await conn.run_sync(lambda c:'active_screens' in inspect(c).get_table_names())
                assert (await conn.execute(select(ActiveScreen.__table__))).all()==[]
        finally: await engine.dispose()
    asyncio.run(run())


def test_unknown_payment_callback_never_becomes_navigation():
    async def run():
        app=TelegramHarness()
        try:
            app.messages[(123,900)]=app.bot_message(123,900,'KudaVPN Premium\n\nОплата подтверждена')
            await app.callback('nav:home',message_id=900)
            assert not methods(app,SendMessage) and not methods(app,EditMessageText) and not methods(app,DeleteMessage)
            assert await app.navigation.store.load(123) is None
        finally: await app.close()
    asyncio.run(run())


def test_storage_failure_after_send_rolls_back_new_screen():
    async def run():
        app=TelegramHarness()
        try:
            await app.text()
            previous=app.navigation.views[123].message_id
            app.navigation.store.save=AsyncMock(side_effect=OperationalError('test',None,Exception('offline')))
            app.fail_edit="message can't be edited"
            await app.callback('nav:traffic')
            assert app.navigation.views[123].message_id==previous
            assert (123,previous) in app.messages
            assert len(app.messages)==1
        finally: await app.close()
    asyncio.run(run())


def test_delayed_load_does_not_overwrite_newer_pointer():
    async def run():
        from bot.services.navigation import MemoryScreenStore, ScreenReference
        store=MemoryScreenStore()
        navigation=Navigation(store)
        started=asyncio.Event();release=asyncio.Event()
        async def old_load(user_id):
            started.set();await release.wait()
            return ScreenReference(123,123,101)
        store.load=old_load
        task=asyncio.create_task(navigation.load(123))
        await started.wait()
        await navigation.remember(123,202)
        release.set()
        assert (await task).message_id==202
    asyncio.run(run())
