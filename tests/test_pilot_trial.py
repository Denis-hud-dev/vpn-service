"""An offline trial never reaches a real node, Telegram, or payment provider."""
import asyncio
import base64
from datetime import timedelta
from urllib.parse import urlsplit

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.db.models import Base, NodeAccess, Payment, Subscription, SubscriptionHistory, VPNNode
from bot.services.happ import AccessDenied, HappService, NodeReconciler
from bot.services.node_health import NodeHealthMonitor
from bot.services.pilot_trial import PilotTrialService
from bot.services.subscriptions import SubscriptionService, as_utc, utcnow
from bot.services.users import UserService


class FakeIsolatedGateway:
    def __init__(self):
        self.active = set()
        self.tunnels = set()

    async def apply(self, node, credential, *, enabled, expires_at, revision):
        assert node.port == 10443  # Never the occupied 9443 or working 8443.
        if enabled:
            self.active.add(credential)
        else:
            self.active.discard(credential)
            self.tunnels.discard(credential)

    async def probe(self, node):
        return node.port == 10443


def test_owner_trial_lifecycle_is_free_isolated_and_idempotent(tmp_path):
    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'trial.db'))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await UserService(sessions).register(101, 'Owner')
            await UserService(sessions).register(202, 'Other')
            async with sessions.begin() as db:
                db.add(VPNNode(name='isolated-test-only', hostname='203.0.113.10',
                               port=10443, public_key='a' * 43, short_id='abcd',
                               server_name='www.example.org', enabled=True))
                db.add(VPNNode(name='occupied-pilot-must-be-ignored', hostname='203.0.113.11',
                               port=9443, public_key='b' * 43, short_id='ef01',
                               server_name='www.example.org', enabled=True))
            trial = PilotTrialService(sessions, 101)
            with pytest.raises(PermissionError):
                await trial.grant_once(202)
            first = await trial.grant_once(101)
            first_expiry, first_id = first.expires_at, first.id
            assert timedelta(hours=23, minutes=59) < first_expiry - utcnow() <= timedelta(hours=24)
            again = await trial.grant_once(101)
            assert again.id == first_id and as_utc(again.expires_at) == first_expiry
            key = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b'=').decode()
            feed = HappService(sessions, 'https://pilot.example.org', link_key=key,
                               trial_owner_id=101, trial_node_port=10443)
            disabled_feed = HappService(sessions, 'https://pilot.example.org', link_key=key)
            other_owner_feed = HappService(sessions, 'https://pilot.example.org', link_key=key,
                                           trial_owner_id=202, trial_node_port=10443)
            gateway = FakeIsolatedGateway()
            disabled_worker = NodeReconciler(sessions, gateway)
            await disabled_worker.reconcile(first_id)
            assert gateway.active == set()
            worker = NodeReconciler(sessions, gateway, trial_owner_id=101,
                                    trial_node_port=10443)
            await worker.reconcile(first_id)
            await NodeHealthMonitor(sessions, gateway).tick()
            link = await feed.issue_for_user(101)
            body, _ = await feed.feed(link.rsplit('/', 1)[1])
            credential = urlsplit(body.strip()).username
            assert urlsplit(body.strip()).port == 10443
            assert len(gateway.active) == 1
            async with sessions() as db:
                assert await db.scalar(select(func.count()).select_from(NodeAccess)) == 1
            gateway.tunnels.add(next(iter(gateway.active)))
            with pytest.raises(AccessDenied):
                await disabled_feed.issue_for_user(101)
            with pytest.raises(AccessDenied):
                await other_owner_feed.issue_for_user(101)
            with pytest.raises(AccessDenied):
                await feed.issue_for_user(202)
            async with sessions() as db:
                assert await db.scalar(select(func.count()).select_from(Payment)) == 0
                assert await db.scalar(select(func.count()).select_from(SubscriptionHistory).where(
                    SubscriptionHistory.reason == 'pilot_trial_grant')) == 1

            async with sessions.begin() as db:
                sub = await db.get(Subscription, first_id)
                sub.starts_at = utcnow() - timedelta(days=2)
                sub.expires_at = utcnow() - timedelta(seconds=1)
            assert await SubscriptionService(sessions).expire_due() == 1
            with pytest.raises(AccessDenied):
                await feed.feed(link.rsplit('/', 1)[1])
            await worker.reconcile(first_id)
            assert not gateway.active and not gateway.tunnels
            async with sessions() as db:
                access = await db.scalar(select(NodeAccess))
                assert access.revocation_confirmed and not access.enabled
                assert str(access.credential) == credential

            target = utcnow() + timedelta(hours=24)
            await trial.extend_to(101, target)
            await trial.extend_to(101, target)
            await worker.reconcile(first_id)
            await NodeHealthMonitor(sessions, gateway).tick()
            assert await feed.issue_for_user(101) == link
            renewed, _ = await feed.feed(link.rsplit('/', 1)[1])
            assert urlsplit(renewed.strip()).username == credential
            async with sessions() as db:
                assert await db.scalar(select(func.count()).select_from(SubscriptionHistory).where(
                    SubscriptionHistory.reason == 'pilot_trial_extend')) == 1
                assert await db.scalar(select(func.count()).select_from(Payment)) == 0
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_trial_rejects_unregistered_owner_far_extension_and_blocked_user(tmp_path):
    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'trial.db'))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            trial = PilotTrialService(sessions, 101)
            with pytest.raises(ValueError, match='register'):
                await trial.grant_once(101)
            await UserService(sessions).register(101, 'Owner')
            sub = await trial.grant_once(101)
            with pytest.raises(ValueError, match='seven days'):
                await trial.extend_to(101, utcnow() + timedelta(days=8))
            await SubscriptionService(sessions).set_status(sub.id, 'blocked')
            with pytest.raises(ValueError, match='not renewable'):
                await trial.extend_to(101, utcnow() + timedelta(days=1))
            assert (await trial.grant_once(101)).status == 'blocked'
        finally:
            await engine.dispose()
    asyncio.run(run())
