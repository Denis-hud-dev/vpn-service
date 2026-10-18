"""Offline Happ flow for a JustHost-style node and an unavailable peer."""
import asyncio
import base64
from uuid import UUID, uuid4
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.db.models import Base, VPNNode
from bot.services import TARIFFS
from bot.services.happ import AccessDenied, HappService, NodeReconciler
from bot.services.node_health import NodeHealthMonitor
from bot.services.payment_events import ConfirmedPayment
from bot.services.payments import PaymentRepository
from bot.services.subscriptions import SubscriptionService, utcnow
from bot.services.users import UserService
from tests.test_happ import FakeNode
from tests.test_subscription_lifecycle import TestVerifier


class OneNodeUnavailable(FakeNode):
    def __init__(self, failed_id):
        super().__init__()
        self.failed_id = failed_id

    async def apply(self, node, credential, *, enabled, expires_at, revision):
        if node.id == self.failed_id:
            raise RuntimeError('simulated unavailable node')
        await super().apply(node, credential, enabled=enabled,
                            expires_at=expires_at, revision=revision)

    async def probe(self, node):
        return node.id != self.failed_id


def test_distinct_users_keep_justhost_when_other_node_is_down(tmp_path):
    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'nodes.db'))
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            lifecycle = SubscriptionService(sessions, TestVerifier())
            feed = HappService(sessions, 'https://subscriptions.example',
                link_key=base64.urlsafe_b64encode(bytes(range(32))).rstrip(b'=').decode())
            offline_id = UUID(int=1)
            justhost_id = UUID(int=2)
            async with sessions.begin() as session:
                session.add_all([
                    VPNNode(id=offline_id, name='A Offline', hostname='198.51.100.10',
                        port=443, public_key='a' * 43, short_id='1234',
                        server_name='www.cloudflare.com', enabled=True),
                    VPNNode(id=justhost_id, name='B JustHost test', hostname='203.0.113.20',
                        port=8443, public_key='b' * 43, short_id='abcd',
                        server_name='www.cloudflare.com', enabled=True),
                ])
            subscriptions = []
            for telegram_id in (101, 202):
                await UserService(sessions).register(telegram_id, 'Test')
                pending = await PaymentRepository(sessions).create_pending(
                    telegram_id, TARIFFS['month'], uuid4())
                subscriptions.append(await lifecycle.apply_confirmed_payment(
                    ConfirmedPayment(pending.id, 'yookassa', str(uuid4()),
                                     29900, 'RUB', utcnow())))

            gateway = OneNodeUnavailable(offline_id)
            reconciler = NodeReconciler(sessions, gateway)
            for sub in subscriptions:
                with pytest.raises(RuntimeError, match='Node reconciliation incomplete'):
                    await reconciler.reconcile(sub.id)
            await NodeHealthMonitor(sessions, gateway).tick()
            links = [await feed.issue_for_user(user_id) for user_id in (101, 202)]
            assert links[0] != links[1]

            # Use the async feed directly; every paid user has a distinct
            # credential on the same healthy JustHost-style node.
            initial = []
            for link in links:
                body, _ = await feed.feed(link.rsplit('/', 1)[1])
                rows = [urlsplit(row) for row in body.splitlines()]
                assert len(rows) == 1 and rows[0].hostname == '203.0.113.20'
                assert rows[0].port == 8443
                assert parse_qs(rows[0].query)['flow'] == ['xtls-rprx-vision']
                initial.append(rows[0].username)
            assert initial[0] != initial[1]
            assert len(gateway.active) == 2

            gateway.failed_id = None
            for sub in subscriptions:
                await reconciler.reconcile(sub.id)
            await NodeHealthMonitor(sessions, gateway).tick()
            for link, credential in zip(links, initial):
                body, _ = await feed.feed(link.rsplit('/', 1)[1])
                rows = [urlsplit(row) for row in body.splitlines()]
                assert len(rows) == 2
                assert next(row.username for row in rows if row.port == 8443) == credential
            assert len(gateway.active) == 4

            renewal = await PaymentRepository(sessions).create_pending(101, TARIFFS['quarter'], uuid4())
            await lifecycle.apply_confirmed_payment(ConfirmedPayment(
                renewal.id, 'yookassa', str(uuid4()), 79900, 'RUB', utcnow()))
            await reconciler.reconcile(subscriptions[0].id)
            assert await feed.issue_for_user(101) == links[0]
            renewed_body, _ = await feed.feed(links[0].rsplit('/', 1)[1])
            assert next(urlsplit(row).username for row in renewed_body.splitlines()
                        if urlsplit(row).port == 8443) == initial[0]
            await lifecycle.set_status(subscriptions[0].id, 'blocked')
            with pytest.raises(AccessDenied):
                await feed.feed(links[0].rsplit('/', 1)[1])
            await reconciler.reconcile(subscriptions[0].id)
            assert len(gateway.active) == 2
            assert len((await feed.feed(links[1].rsplit('/', 1)[1]))[0].splitlines()) == 2

            await lifecycle.set_status(subscriptions[1].id, 'expired')
            with pytest.raises(AccessDenied):
                await feed.feed(links[1].rsplit('/', 1)[1])
            await reconciler.reconcile(subscriptions[1].id)
            assert gateway.active == {}
        finally:
            await engine.dispose()

    asyncio.run(run())
