import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.db.models import Base, NodeHealth, Payment, Subscription, User, VPNNode
from bot.services.admin import AdminReadService


def test_admin_identity_is_required_before_database_access():
    async def run():
        service = AdminReadService(None, frozenset({501}))
        with pytest.raises(PermissionError):
            await service.summary(502)
        with pytest.raises(PermissionError):
            await AdminReadService(None, frozenset()).summary(501)
    asyncio.run(run())


def test_admin_summary_contains_only_aggregate_data(tmp_path):
    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'admin.db'))
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            now = datetime.now(timezone.utc)
            async with sessions.begin() as session:
                user = User(telegram_id=501, first_name='Sensitive Name')
                session.add(user)
                await session.flush()
                session.add(Subscription(user_id=user.id, tariff_id='month', status='active',
                    starts_at=now, expires_at=now + timedelta(days=30)))
                session.add(Payment(user_id=user.id, tariff_id='month',
                    amount_kopecks=29900, currency='RUB', provider='yookassa', status='succeeded'))
                node = VPNNode(name='test', hostname='test.example', port=443,
                    public_key='public-only', short_id='ab', server_name='example.com', enabled=True)
                session.add(node)
                await session.flush()
                session.add(NodeHealth(node_id=node.id, available=True, checked_at=now,
                    config_digest='0' * 64))
            summary = await AdminReadService(sessions, frozenset({501})).summary(501)
            assert summary.users == 1
            assert summary.active_subscriptions == 1
            assert summary.successful_payments == 1
            assert summary.paid_kopecks == 29900
            assert summary.enabled_nodes == summary.healthy_nodes == 1
            assert 'Sensitive Name' not in repr(summary)
        finally:
            await engine.dispose()
    asyncio.run(run())
