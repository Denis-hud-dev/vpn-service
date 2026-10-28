"""Migration and service integration checks on temporary databases only."""
import asyncio
import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
import yaml
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandObject
from aiogram.types import Message
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import event, select, func, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from bot.config import Config
from bot.navigation import Navigation
from bot.services.navigation import PostgresScreenStore
from tests.fake_telegram import TelegramHarness
from bot.db.models import Base, User, Subscription, SubscriptionStatus, Device, VPNServer, Payment
from bot.db.session import Database
from bot.handlers.start import start
from bot.services import Services, TARIFFS
from bot.services.payments import PaymentRepository, YooKassaGateway
from bot.services.subscriptions import utcnow
from bot.services.vpn import VPNService, DisabledWireGuardGateway


async def scenario(url):
    from bot.services.payment_events import ConfirmedPayment
    async def activate(sub_id, **dates):
        # Test fixture uses a verified-event boundary rather than bypass activation.
        async with sessions.begin() as session:
            row = await session.get(Subscription, sub_id)
            if row.status == 'blocked':
                row.status = 'expired'
            if dates.get('starts_at'):
                row.starts_at = dates['starts_at']
            if dates.get('expires_at'):
                row.expires_at = dates['expires_at']
            user = await session.get(User, row.user_id)
        payment = await PaymentRepository(sessions).create_pending(user.telegram_id, TARIFFS['month'], uuid4())
        return await services.subscriptions.apply_confirmed_payment(ConfirmedPayment(
            payment.id, 'yookassa', str(uuid4()), 29900, 'RUB', utcnow()))

    engine = create_async_engine(url)
    if engine.dialect.name == 'sqlite':
        @event.listens_for(engine.sync_engine, 'connect')
        def foreign_keys(connection, record):
            connection.execute('PRAGMA foreign_keys=ON')
    cfg = AlembicConfig('alembic.ini')
    def migrate(connection, target):
        cfg.attributes['connection'] = connection
        command.upgrade(cfg, target)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(migrate, 'head')
            def validate(connection):
                assert not compare_metadata(MigrationContext.configure(connection), Base.metadata)
                assert set(inspect(connection).get_table_names()) == {'alembic_version', 'users', 'subscriptions', 'devices', 'vpn_servers', 'payments', 'active_screens', 'subscription_history', 'subscription_tokens', 'vpn_nodes', 'node_health', 'node_access', 'premium_notices', 'web_identities', 'web_login_flows', 'web_sessions', 'review_sessions', 'npd_receipts', 'npd_receipt_events', 'npd_receipt_jobs', 'npd_receipt_holds', 'npd_admin_alerts'}
            await conn.run_sync(validate)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        services = Services.persistent(sessions)
        from tests.test_subscription_lifecycle import TestVerifier
        services.subscriptions.payment_verifier = TestVerifier()
        harness = TelegramHarness(services=services, store=PostgresScreenStore(sessions))
        bot = harness.bot
        navigation = harness.navigation
        message = Message.model_validate({'message_id': 1, 'date': 1, 'chat': {'id': 101, 'type': 'private'},
            'from': {'id': 101, 'is_bot': False, 'first_name': 'Alice'}, 'text': '/start ref_202'}).as_(bot)
        await start(message, CommandObject(command='start', args='ref_202'), services, navigation)
        await start(message, CommandObject(command='start', args='ref_303'), services, navigation)
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(User)) == 1
            user = await session.scalar(select(User).where(User.telegram_id == 101))
            assert user.referrer_id == 202
            user_id = user.id
        await services.users.register(101, 'Updated', 'alice')
        other = await services.users.register(202, 'Bob', referrer_id=202)
        assert other.referrer_id is None
        first = await services.users.register(303, 'Carol')
        second = await services.users.register(303, 'Carol', referrer_id=101)
        assert first.id == second.id and second.referrer_id is None
        snapshot = await services.subscriptions.snapshot(101)
        assert snapshot.status == 'inactive' and snapshot.device_count == 0 and snapshot.traffic_bytes is None
        assert 'Статус: не активна' in await services.subscriptions.describe(101)
        assert 'нет подключённых' in await services.devices.describe(101)
        sub = await services.subscriptions.create_pending(101, 'month')
        assert sub.status == 'pending'
        assert (await services.subscriptions.create_pending(101, 'year')).id == sub.id
        with pytest.raises(ValueError):
            await services.subscriptions.set_status(sub.id, SubscriptionStatus.ACTIVE)
        now = utcnow()
        await activate(sub.id, starts_at=now, expires_at=now + timedelta(days=30))
        assert 'Статус: активна' in await services.subscriptions.describe(101)
        assert 'сервер пока не настроен' in await services.devices.connection_message(101)
        await services.subscriptions.set_status(sub.id, SubscriptionStatus.BLOCKED)
        assert 'заблокирована' in await services.subscriptions.describe(101)
        with pytest.raises(ValueError):
            await services.subscriptions.set_status(sub.id, SubscriptionStatus.PENDING)
        await activate(sub.id)
        async with sessions.begin() as session:
            row = await session.get(Subscription, sub.id)
            row.starts_at = now - timedelta(days=31)
            row.expires_at = now - timedelta(seconds=1)
        assert await services.subscriptions.expire_due() == 1
        assert await services.subscriptions.expire_due() == 0
        assert 'истекла' in await services.subscriptions.describe(101)
        await activate(sub.id, starts_at=now, expires_at=now + timedelta(days=30))
        async with sessions.begin() as session:
            server = VPNServer(name='Test', hostname='invalid.example', location='test')
            session.add(server)
            await session.flush()
            assert server.enabled is False
            server_id = server.id
            session.add(Device(user_id=user_id, subscription_id=sub.id, name='Phone', platform='ios'))
        assert 'Phone' in await services.devices.describe(101)
        assert 'Устройства: 1' in await services.subscriptions.describe(101)
        snapshot = await services.subscriptions.snapshot(101)
        assert snapshot.status == 'active' and snapshot.device_count == 1 and snapshot.tariff_label == TARIFFS['month'].label
        with pytest.raises(IntegrityError):
            async with sessions.begin() as session:
                session.add(Device(user_id=other.id, subscription_id=sub.id, name='Wrong owner', platform='ios'))
        with pytest.raises(IntegrityError):
            async with sessions.begin() as session:
                session.add(Device(user_id=user_id, subscription_id=sub.id, name='Unconfigured', platform='ios', status='active'))
        with pytest.raises(IntegrityError):
            async with sessions.begin() as session:
                session.add(Payment(user_id=user_id, tariff_id='month', amount_kopecks=-1))
        repository = PaymentRepository(sessions)
        key = uuid4()
        payment = await repository.create_pending(101, TARIFFS['month'], key)
        repeat = await repository.create_pending(101, TARIFFS['month'], key)
        assert payment.id == repeat.id and payment.amount_kopecks == 29900
        with pytest.raises(ValueError):
            await repository.create_pending(101, TARIFFS['year'], key)
        text = await services.payments.create_payment(101, TARIFFS['year'])
        assert '2490' in text
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(Payment).where(Payment.status == "pending")) == 1
        with pytest.raises(RuntimeError):
            await YooKassaGateway().create()
        with pytest.raises(RuntimeError):
            await YooKassaGateway().verify('offline')
        with pytest.raises(RuntimeError):
            await VPNService(sessions).provision_device(101, 'ios')
        with pytest.raises(RuntimeError):
            await DisabledWireGuardGateway().add_peer(server, 'public', '10.0.0.2')
        async with sessions.begin() as session:
            row = await session.get(Subscription, sub.id)
            row.starts_at = now - timedelta(days=2)
            row.expires_at = now - timedelta(days=1)
        assert 'истекла' in await services.subscriptions.describe(101)
        async with engine.begin() as conn:
            def downgrade(connection):
                cfg.attributes['connection'] = connection
                command.downgrade(cfg, 'base')
                assert set(inspect(connection).get_table_names()) == {'alembic_version'}
            await conn.run_sync(downgrade)
            await conn.run_sync(migrate, 'head')
        await harness.close()
    finally:
        await engine.dispose()


def test_sqlite_migrations_and_services(tmp_path):
    asyncio.run(scenario(f'sqlite+aiosqlite:///{tmp_path / "test.sqlite"}'))


@pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL'), reason='Disposable PostgreSQL TEST_DATABASE_URL not configured')
def test_postgresql_migrations_and_services():
    # Only an explicitly supplied EMPTY test database. Never use DATABASE_URL here.
    url = os.environ['TEST_DATABASE_URL']
    assert url.startswith('postgresql+psycopg://')
    async def run():
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
                assert not tables, 'Use a separate empty test database'
        finally:
            await engine.dispose()
        await scenario(url)
    asyncio.run(run())


def test_config_demo_and_compose(monkeypatch):
    monkeypatch.setenv('BOT_TOKEN', '123456:offline_test_token')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('SUPPORT_USERNAME', raising=False)
    config = Config.from_env()
    assert config.database_url == ''
    with pytest.raises(ValueError):
        Database('sqlite+aiosqlite://')
    compose = yaml.safe_load(Path('docker-compose.yml').read_text())
    assert compose['services']['bot']['depends_on']['migrate']['condition'] == 'service_completed_successfully'
    assert compose['services']['postgres']['ports'] == ['127.0.0.1:5432:5432']
    assert 'POSTGRES_PASSWORD:?' in compose['services']['postgres']['environment']['POSTGRES_PASSWORD']


def test_lifecycle_and_database_readiness(monkeypatch):
    """Exercise startup/readiness/shutdown without opening ports or Telegram."""
    from types import SimpleNamespace
    import bot.main as app_module

    database = SimpleNamespace(sessions=object(), check=AsyncMock(), ping=AsyncMock(), close=AsyncMock())
    runner = SimpleNamespace(setup=AsyncMock(), cleanup=AsyncMock())
    site = SimpleNamespace(start=AsyncMock())
    apps = []
    monkeypatch.setattr(app_module, 'Database', lambda url: database)
    monkeypatch.setattr(app_module.Services, 'persistent', lambda sessions, **kwargs: Services.demo())
    def make_runner(app, **kwargs):
        apps.append(app)
        return runner
    monkeypatch.setattr(app_module.web, 'AppRunner', make_runner)
    monkeypatch.setattr(app_module.web, 'TCPSite', lambda *a, **kw: site)
    monkeypatch.setattr(Bot, 'get_me', AsyncMock(return_value=SimpleNamespace(username='KudaVpnBot')))
    async def polling(dispatcher, bot, **kwargs):
        handlers = {route.resource.canonical: route.handler for route in apps[0].router.routes()}
        assert (await handlers['/ready'](None)).status == 503
        assert (await handlers['/health'](None)).status == 200
        await dispatcher.emit_startup(bot=bot)
        assert (await handlers['/ready'](None)).status == 200
        database.ping.side_effect = RuntimeError('offline test DB failure')
        assert (await handlers['/ready'](None)).status == 503
        database.ping.side_effect = None
        await dispatcher.emit_shutdown()
        assert (await handlers['/ready'](None)).status == 503
    monkeypatch.setattr(Dispatcher, 'start_polling', polling)
    asyncio.run(app_module.run(Config('123456:offline_test_token', database_url='offline-placeholder')))
    database.close.assert_awaited_once()
    database.check.assert_awaited_once()
    assert database.ping.await_count == 2
    runner.cleanup.assert_awaited_once()
