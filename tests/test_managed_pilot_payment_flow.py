"""Offline payment-to-node pilot: no provider calls or real VPN credentials."""
import asyncio
import base64
from copy import deepcopy
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.db.models import Base, NodeAccess, Payment, Subscription, SubscriptionHistory, VPNNode
from bot.node_agent.service import NodeAgent
from bot.node_agent.store import LeaseStore
from bot.services import Services
from bot.services.happ import AccessDenied, HappService, NodeReconciler
from bot.services.node_health import NodeHealthMonitor
from bot.services.payment_notifications import PaymentNotificationProcessor
from bot.services.payments import PaymentCheckoutService, PaymentIntent
from bot.services.subscriptions import SubscriptionService, utcnow
from bot.services.users import UserService
from tests.fake_telegram import TelegramHarness


class SimulatedCheckout:
    async def create(self, **kwargs):
        return PaymentIntent('test-' + str(kwargs['order_id']),
                             'https://yoomoney.ru/checkout/test')


class SimulatedProviderLookup:
    def __init__(self):
        self.rows = {}
        self.calls = 0

    async def fetch(self, payment_id):
        self.calls += 1
        return deepcopy(self.rows[payment_id])


def provider_row(payment, status='succeeded'):
    return {
        'id': payment.provider_payment_id,
        'status': status,
        'paid': status == 'succeeded',
        'metadata': {'order_id': str(payment.id)},
        'amount': {'value': f'{payment.amount_kopecks / 100:.2f}', 'currency': 'RUB'},
        'captured_at': (utcnow() - timedelta(seconds=1)).isoformat(),
    }


class SimulatedXray:
    """Tracks active tunnels so a revoke must close them, not only deny re-entry."""
    def __init__(self):
        self.users = set()
        self.tunnels = set()
        self.restarts = 0

    @staticmethod
    def email(credential):
        return 'kuda-' + str(credential)

    async def managed_users(self):
        return self.users.copy()

    async def add_many(self, credentials):
        self.users.update(self.email(value) for value in credentials)

    async def add(self, credential):
        self.users.add(self.email(credential))

    async def remove(self, credential):
        self.users.remove(self.email(credential))

    async def restart(self):
        self.restarts += 1
        self.users.clear()
        self.tunnels.clear()

    def connect(self, credential):
        assert self.email(credential) in self.users
        self.tunnels.add(credential)


class SimulatedManagedGateway:
    def __init__(self, state_file):
        self.xray = SimulatedXray()
        self.store = LeaseStore(str(state_file))
        self.agent = NodeAgent(self.store, self.xray,
                               restart_xray=self.xray.restart, max_users=1)
        self.available = True

    async def apply(self, node, credential, *, enabled, expires_at, revision):
        assert node.port == 9443  # The fake never addresses the working 8443.
        if not self.available:
            raise ConnectionError('simulated agent outage')
        await self.agent.apply(credential, revision, enabled,
                               int(expires_at.timestamp()) if expires_at else None)

    async def probe(self, node):
        return (self.available and not self.agent.failed.is_set()
                and self.xray.users == {
                    self.xray.email(value) for value in self.store.desired(int(utcnow().timestamp()))})

    def close(self):
        self.store.close()


async def setup(tmp_path):
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'pilot.db'))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions.begin() as session:
        session.add(VPNNode(name='JustHost isolated pilot', hostname='203.0.113.94',
            port=9443, public_key='a' * 43, short_id='abcd',
            server_name='www.cloudflare.com', enabled=True))
    lookup = SimulatedProviderLookup()
    processor = PaymentNotificationProcessor(sessions, lookup)
    checkout = PaymentCheckoutService(sessions, SimulatedCheckout(),
                                      'https://example.org/payment-return')
    feed = HappService(sessions, 'https://subscriptions.example',
        link_key=base64.urlsafe_b64encode(bytes(range(32))).rstrip(b'=').decode())
    gateway = SimulatedManagedGateway(tmp_path / 'agent' / 'leases.db')
    return engine, sessions, lookup, processor, checkout, feed, gateway


async def pay(sessions, lookup, processor, checkout, telegram_id, key=None):
    key = key or uuid4()
    intent = await checkout.create(telegram_id, 'month', key)
    assert await checkout.create(telegram_id, 'month', key) == intent
    async with sessions() as session:
        payment = await session.scalar(select(Payment).where(
            Payment.provider_payment_id == intent.provider_id))
    lookup.rows[intent.provider_id] = provider_row(payment)
    assert await processor.process(intent.provider_id) == 'succeeded'
    return intent


def test_offline_payment_expiry_and_renewal_restore_same_uuid_and_link(tmp_path):
    async def run():
        engine, sessions, lookup, processor, checkout, feed, gateway = await setup(tmp_path)
        try:
            await UserService(sessions).register(501, 'Pilot')
            await UserService(sessions).register(502, 'Unpaid')
            with pytest.raises(AccessDenied):
                await feed.issue_for_user(501)
            with pytest.raises(AccessDenied):
                await feed.issue_for_user(502)

            first_key = uuid4()
            first = await pay(sessions, lookup, processor, checkout, 501, first_key)
            with pytest.raises(ValueError, match='не ожидает оплату'):
                await checkout.create(501, 'month', first_key)
            async with sessions() as session:
                sub = await session.scalar(select(Subscription))
                original_expiry = sub.expires_at
                sub_id = sub.id
            reconciler = NodeReconciler(sessions, gateway)
            await reconciler.reconcile(sub_id)
            await NodeHealthMonitor(sessions, gateway).tick()
            link = await feed.issue_for_user(501)
            app = TelegramHarness(services=Services.persistent(
                sessions, subscription_public_url='https://subscriptions.example',
                happ_link_secret=base64.urlsafe_b64encode(bytes(range(32))).rstrip(b'=').decode()))
            try:
                await app.text('/start', user_id=501)
                await app.callback('nav:connect', user_id=501)
                assert link in app.navigation.views[501].screen.text
            finally:
                await app.close()
            body, _ = await feed.feed(link.rsplit('/', 1)[1])
            profile, = body.splitlines()
            assert urlsplit(profile).port == 9443
            credential = urlsplit(profile).username
            assert len(gateway.xray.users) == 1
            gateway.xray.connect(credential)

            # A duplicate provider event is not a second purchase or UUID.
            assert await processor.process(first.provider_id) == 'succeeded'
            await reconciler.reconcile(sub_id)
            async with sessions() as session:
                sub = await session.get(Subscription, sub_id)
                access = await session.scalar(select(NodeAccess))
                assert sub.expires_at == original_expiry
                assert str(access.credential) == credential
                assert await session.scalar(select(func.count()).select_from(
                    SubscriptionHistory).where(SubscriptionHistory.reason == 'payment')) == 1

            # The expiry worker marks the row; the reconciler enforces it on Xray.
            async with sessions.begin() as session:
                sub = await session.get(Subscription, sub_id)
                sub.starts_at = utcnow() - timedelta(days=40)
                sub.expires_at = utcnow() - timedelta(seconds=1)
            assert await SubscriptionService(sessions).expire_due() == 1
            with pytest.raises(AccessDenied):
                await feed.feed(link.rsplit('/', 1)[1])
            await reconciler.reconcile(sub_id)
            assert gateway.xray.users == set()
            assert gateway.xray.tunnels == set()
            assert gateway.xray.restarts == 1
            async with sessions() as session:
                access = await session.scalar(select(NodeAccess))
                assert str(access.credential) == credential
                assert access.revocation_confirmed and not access.enabled

            renewal = await pay(sessions, lookup, processor, checkout, 501)
            await reconciler.reconcile(sub_id)
            await NodeHealthMonitor(sessions, gateway).tick()
            assert await feed.issue_for_user(501) == link
            renewed, _ = await feed.feed(link.rsplit('/', 1)[1])
            assert urlsplit(renewed.strip()).username == credential
            gateway.xray.connect(credential)
            assert gateway.xray.tunnels == {credential}
            assert await processor.process(renewal.provider_id) == 'succeeded'
            with pytest.raises(AccessDenied):
                await feed.issue_for_user(502)
            async with sessions() as session:
                assert await session.scalar(select(func.count()).select_from(NodeAccess)) == 1
        finally:
            gateway.close()
            await engine.dispose()
    asyncio.run(run())


def test_agent_outage_hides_feed_and_retries_revocation_without_affecting_others(tmp_path):
    async def run():
        engine, sessions, lookup, processor, checkout, feed, gateway = await setup(tmp_path)
        try:
            await UserService(sessions).register(601, 'Pilot')
            other = await UserService(sessions).register(602, 'Other')
            await pay(sessions, lookup, processor, checkout, 601)
            async with sessions() as session:
                sub = await session.scalar(select(Subscription))
                sub_id = sub.id
            reconciler = NodeReconciler(sessions, gateway)
            gateway.available = False
            with pytest.raises(RuntimeError, match='Node reconciliation incomplete'):
                await reconciler.reconcile(sub_id)
            await NodeHealthMonitor(sessions, gateway).tick()
            with pytest.raises(AccessDenied):
                await feed.issue_for_user(601)
            assert not gateway.xray.users
            async with sessions() as session:
                access = await session.scalar(select(NodeAccess))
                credential = access.credential
                assert not access.enabled and access.desired_enabled

            gateway.available = True
            await reconciler.reconcile(sub_id)
            await NodeHealthMonitor(sessions, gateway).tick()
            link = await feed.issue_for_user(601)
            gateway.xray.connect(credential)
            await pay(sessions, lookup, processor, checkout, 602)
            async with sessions() as session:
                other_sub = await session.scalar(select(Subscription).where(
                    Subscription.user_id == other.id))
            with pytest.raises(RuntimeError, match='Node reconciliation incomplete'):
                await reconciler.reconcile(other_sub.id)
            with pytest.raises(AccessDenied):
                await feed.issue_for_user(602)
            assert gateway.xray.users == {gateway.xray.email(credential)}
            await SubscriptionService(sessions).set_status(sub_id, 'blocked')
            with pytest.raises(AccessDenied):
                await feed.feed(link.rsplit('/', 1)[1])
            gateway.available = False
            with pytest.raises(RuntimeError, match='Node reconciliation incomplete'):
                await reconciler.reconcile(sub_id)
            async with sessions() as session:
                access = await session.scalar(select(NodeAccess))
                assert not access.desired_enabled and not access.revocation_confirmed
                assert access.credential == credential
            gateway.available = True
            await reconciler.reconcile(sub_id)
            assert not gateway.xray.users and not gateway.xray.tunnels
            async with sessions() as session:
                access = await session.scalar(select(NodeAccess))
                assert access.revocation_confirmed and not access.enabled
                assert access.credential == credential
            with pytest.raises(AccessDenied):
                await feed.issue_for_user(602)

            # A payment cannot silently override an administrative block.
            renewal_intent = await checkout.create(601, 'month', uuid4())
            async with sessions() as session:
                renewal_payment = await session.scalar(select(Payment).where(
                    Payment.provider_payment_id == renewal_intent.provider_id))
            lookup.rows[renewal_intent.provider_id] = provider_row(renewal_payment)
            with pytest.raises(ValueError, match='требуется решение администратора'):
                await processor.process(renewal_intent.provider_id)
            assert gateway.xray.users == set()
            await SubscriptionService(sessions).set_status(sub_id, 'expired')
            assert await processor.process(renewal_intent.provider_id) == 'succeeded'
            await reconciler.reconcile(sub_id)
            await NodeHealthMonitor(sessions, gateway).tick()
            assert await feed.issue_for_user(601) == link
            async with sessions() as session:
                access = await session.scalar(select(NodeAccess).where(
                    NodeAccess.subscription_id == sub_id))
                assert access.credential == credential
                assert access.enabled and len(gateway.xray.users) == 1
        finally:
            gateway.close()
            await engine.dispose()
    asyncio.run(run())
