"""KudaVPN Control foundation; no real node or payment access."""
import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.db.models import Base, DeviceProfile, NodeAccess, ProfileTraffic, Subscription, User, VPNNode
from bot.services.control import ControlDenied, ControlService, account_sample
from bot.services.subscriptions import utcnow
from bot.node_agent.xray_cli import XrayCLI, XrayCommandError


def test_traffic_counter_repeated_reset_and_missing_source():
    now = utcnow()
    row = ProfileTraffic(profile_id=uuid4(), total_up=0, total_down=0,
                         last_up=None, last_down=None, complete=False)
    account_sample(row, up=100, down=200, generation='process-a', sampled_at=now)
    assert (row.total_up, row.total_down, row.complete) == (0, 0, False)
    account_sample(row, up=120, down=280, generation='process-a', sampled_at=now + timedelta(seconds=1))
    assert (row.total_up, row.total_down) == (20, 80)
    account_sample(row, up=120, down=280, generation='process-a', sampled_at=now + timedelta(seconds=1))
    assert (row.total_up, row.total_down) == (20, 80)
    row.complete = True  # A verified source can establish a continuous zero baseline.
    account_sample(row, up=7, down=9, generation='process-b', sampled_at=now + timedelta(seconds=2))
    assert (row.total_up, row.total_down, row.complete) == (20, 80, False)
    account_sample(row, up=5, down=12, generation='process-b', sampled_at=now + timedelta(seconds=3))
    assert (row.total_up, row.total_down, row.complete) == (20, 80, False)
    with pytest.raises(ValueError):
        account_sample(row, up=-1, down=12, generation='process-b', sampled_at=now + timedelta(seconds=4))


def test_xray_stats_parser_rejects_malformed_or_duplicated_counters():
    async def run():
        cli = XrayCLI('/unused/local/xray', '127.0.0.1:10085', 'test', 10443)
        credential = uuid4()
        name = f'user>>>{cli.email(credential)}>>>traffic>>>uplink'
        calls = []

        async def response(*args):
            calls.append(args)
            return json.dumps({'stat': [{'name': name, 'value': 17}]})

        cli._command = response
        assert await cli.user_stats() == {credential: (17, None)}
        assert str(credential) not in str(calls)
        async def duplicate(*args):
            return json.dumps({'stat': [{'name': name, 'value': 17},
                                        {'name': name, 'value': 17}]})
        cli._command = duplicate
        with pytest.raises(XrayCommandError):
            await cli.user_stats()
        async def invalid(*args):
            return json.dumps({'stat': [{'name': name, 'value': -1}]})
        cli._command = invalid
        with pytest.raises(XrayCommandError):
            await cli.user_stats()
    asyncio.run(run())


def test_profile_registry_owns_rows_enforces_limit_and_never_grants_access():
    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions.begin() as session:
                alice = User(telegram_id=101, first_name='Alice')
                bob = User(telegram_id=202, first_name='Bob')
                node = VPNNode(name='test-only', hostname='example.test', port=10443,
                               public_key='test', short_id='12345678', server_name='example.test', enabled=True)
                session.add_all((alice, bob, node))
                await session.flush()
                session.add_all((
                    Subscription(user_id=alice.id, tariff_id='month', status='active',
                                 starts_at=utcnow(), expires_at=utcnow() + timedelta(days=1), device_limit=1),
                    Subscription(user_id=bob.id, tariff_id='month', status='active',
                                 starts_at=utcnow(), expires_at=utcnow() + timedelta(days=1), device_limit=1)))
                node_id = node.id
            control = ControlService(sessions)
            profile_id = await control.reserve_profile(101, node_id, 'Мой телефон', 'android')
            assert await control.list_profiles(202) == []
            listed = await control.list_profiles(101)
            assert len(listed) == 1 and listed[0].state == 'pending' and listed[0].last_seen_at is None
            with pytest.raises(ControlDenied, match='limit'):
                await control.reserve_profile(101, node_id, 'Second', 'macos')
            with pytest.raises(ControlDenied):
                await control.reserve_profile(202, node_id, '\nunsafe', 'android')
            with pytest.raises(ControlDenied, match='Dedicated node already assigned'):
                await control.reserve_profile(202, node_id, 'Bob phone', 'android')
            async with sessions() as session:
                profile = await session.scalar(select(DeviceProfile).where(DeviceProfile.id == profile_id))
                assert profile.applied_expires_at is None and profile.lease_revision == 0
            assert await control.traffic(101, profile_id) is None
            async with sessions.begin() as session:
                session.add(ProfileTraffic(profile_id=profile_id, total_up=30, total_down=40,
                                           last_up=30, last_down=40, source_generation='test', complete=True))
            assert await control.traffic(101, profile_id) == 70
            assert await control.traffic(202, profile_id) is None
            async with sessions.begin() as session:
                bob_sub = await session.scalar(select(Subscription).where(Subscription.user_id == bob.id))
                session.add(NodeAccess(subscription_id=bob_sub.id, node_id=node_id,
                                       enabled=True, desired_enabled=True))
            with pytest.raises(ControlDenied, match='Legacy shared access'):
                await control.reserve_profile(202, node_id, 'Bob phone', 'android')
        finally:
            await engine.dispose()
    asyncio.run(run())
