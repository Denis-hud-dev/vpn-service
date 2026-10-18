import asyncio
from datetime import timedelta
from uuid import uuid4
from urllib.parse import urlsplit
import pytest
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from bot.db.models import Base, VPNNode, NodeHealth, Subscription, NodeAccess, SubscriptionToken
from bot.services.happ import HappService, NodeReconciler, AccessDenied
from bot.services.node_health import NodeHealthMonitor, HEALTH_TTL_SECONDS
from bot.services.subscriptions import SubscriptionService, utcnow
from bot.services.users import UserService
from bot.services.payments import PaymentRepository
from bot.services.payment_events import ConfirmedPayment
from bot.services import TARIFFS
from bot.subscription_api.app import create_app
from tests.test_subscription_lifecycle import TestVerifier


class FakeNode:
    def __init__(self):
        self.active={};self.fail=False;self.probe_failure=False;self.probe_error=False
        self.unhealthy_nodes=set()
    async def apply(self,node,credential,*,enabled,expires_at,revision):
        key=(node.id,credential)
        if enabled:self.active[key]=expires_at
        else:self.active.pop(key,None)
        if self.fail:raise RuntimeError('simulated lost acknowledgement')
    async def probe(self,node):
        if self.probe_error:raise RuntimeError('secret node endpoint')
        return not self.probe_failure and node.id not in self.unhealthy_nodes


def test_happ_lifecycle(tmp_path):
    async def run():
        engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'happ.db'))
        async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
        sessions=async_sessionmaker(engine,expire_on_commit=False)
        lifecycle=SubscriptionService(sessions,TestVerifier())
        await UserService(sessions).register(1,'Test')
        sub=await lifecycle.create_pending(1,'month')
        feed=HappService(sessions,'https://subscriptions.example')
        with pytest.raises(AccessDenied):await feed.issue(sub.id)
        p=await PaymentRepository(sessions).create_pending(1,TARIFFS['month'],uuid4())
        await lifecycle.apply_confirmed_payment(ConfirmedPayment(p.id,'yookassa',str(uuid4()),29900,'RUB',utcnow()))
        link=await feed.issue(sub.id);token=link.rsplit('/',1)[1]
        async with sessions() as session:
            stored=await session.scalar(select(SubscriptionToken))
            assert stored.digest!=token and len(stored.digest)==64
        with pytest.raises(AccessDenied):await feed.issue(sub.id)
        async with sessions.begin() as session:
            node=VPNNode(name='Test EU',hostname='vpn.example',port=443,public_key='a'*43,
                         short_id='1234',server_name='www.cloudflare.com',enabled=True)
            session.add(node);await session.flush();node_id=node.id
        with pytest.raises(RuntimeError):await NodeReconciler(sessions).reconcile(sub.id)
        with pytest.raises(AccessDenied):await feed.feed(token)
        gateway=FakeNode();reconciler=NodeReconciler(sessions,gateway)
        await reconciler.reconcile(sub.id)
        await NodeHealthMonitor(sessions,gateway).tick()
        body,_=await feed.feed(token)
        assert len(body.splitlines())==1 and 'flow=xtls-rprx-vision' in body
        async with sessions.begin() as session:
            session.add(VPNNode(name='Test US',hostname='other.example',port=443,public_key='b'*43,
                short_id='abcd',server_name='www.cloudflare.com',enabled=True))
        await reconciler.reconcile(sub.id)
        await NodeHealthMonitor(sessions,gateway).tick()
        assert len((await feed.feed(token))[0].splitlines())==2
        async with sessions() as session:
            nodes=(await session.scalars(select(VPNNode))).all()
            failing_node=next(node for node in nodes if node.id != node_id)
        gateway.unhealthy_nodes.add(failing_node.id)
        await NodeHealthMonitor(sessions,gateway).tick()
        one_node_feed=(await feed.feed(token))[0]
        assert len(one_node_feed.splitlines())==1
        assert 'other.example' not in one_node_feed
        gateway.unhealthy_nodes.clear()
        await NodeHealthMonitor(sessions,gateway).tick()
        assert len((await feed.feed(token))[0].splitlines())==2
        client=TestClient(TestServer(create_app(feed)))
        await client.start_server()
        try:
            response=await client.get('/s/'+token)
            assert response.status==200
            assert response.headers['Cache-Control']=='no-store, private'
            assert response.headers['profile-title']=='KudaVPN Premium'
            await lifecycle.set_status(sub.id,'blocked')
            assert (await client.get('/s/'+token)).status==404
            await reconciler.reconcile(sub.id)
            assert not gateway.active
            await lifecycle.set_status(sub.id,'expired')
            p=await PaymentRepository(sessions).create_pending(1,TARIFFS['month'],uuid4())
            await lifecycle.apply_confirmed_payment(ConfirmedPayment(p.id,'yookassa',str(uuid4()),29900,'RUB',utcnow()))
            await reconciler.reconcile(sub.id)
            await feed.revoke(sub.id)
            with pytest.raises(AccessDenied):await feed.feed(token)
            with pytest.raises(AccessDenied):await feed.issue(sub.id)
            await reconciler.reconcile(sub.id)
            assert not gateway.active
            new_link=await feed.issue(sub.id)
            assert new_link!=link
            await reconciler.reconcile(sub.id)
            await NodeHealthMonitor(sessions,gateway).tick()
            async with sessions.begin() as session:
                row=await session.get(VPNNode,node_id);row.enabled=False
            await reconciler.reconcile(sub.id)
            assert len((await feed.feed(new_link.rsplit('/',1)[1]))[0].splitlines())==1
            async with sessions.begin() as session:
                row=await session.get(Subscription,sub.id)
                row.starts_at=utcnow()-timedelta(days=40)
                row.expires_at=utcnow()-timedelta(seconds=1)
            with pytest.raises(AccessDenied):await feed.feed(new_link.rsplit('/',1)[1])
            await reconciler.reconcile(sub.id)
            assert not gateway.active
        finally:
            await client.close();await engine.dispose()
    asyncio.run(run())


def test_https_required():
    for url in ['http://example.com','https://user:pass@example.com','https://example.com/x']:
        with pytest.raises(ValueError):HappService(None,url)


def test_unhealthy_stale_or_changed_node_is_not_advertised(tmp_path, caplog):
    async def run():
        engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'health.db'))
        try:
            async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
            sessions=async_sessionmaker(engine,expire_on_commit=False)
            await UserService(sessions).register(22,'Test')
            payment=await PaymentRepository(sessions).create_pending(22,TARIFFS['month'],uuid4())
            sub=await SubscriptionService(sessions,TestVerifier()).apply_confirmed_payment(
                ConfirmedPayment(payment.id,'yookassa',str(uuid4()),29900,'RUB',utcnow()))
            feed=HappService(sessions,'https://subscriptions.example')
            token=(await feed.issue(sub.id)).rsplit('/',1)[1]
            async with sessions.begin() as session:
                node=VPNNode(name='Test NL',hostname='node.example',port=443,
                    public_key='a'*43,short_id='abcd',server_name='www.cloudflare.com',enabled=True)
                session.add(node);await session.flush();node_id=node.id
            gateway=FakeNode()
            await NodeReconciler(sessions,gateway).reconcile(sub.id)
            with pytest.raises(AccessDenied):await feed.feed(token)
            monitor=NodeHealthMonitor(sessions,gateway)
            await monitor.tick()
            assert len((await feed.feed(token))[0].splitlines())==1
            async with sessions.begin() as session:
                health=await session.get(NodeHealth,node_id)
                health.checked_at=utcnow()-timedelta(seconds=HEALTH_TTL_SECONDS+1)
            with pytest.raises(AccessDenied):await feed.feed(token)
            await monitor.tick()
            gateway.probe_failure=True
            await monitor.tick()
            with pytest.raises(AccessDenied):await feed.feed(token)
            gateway.probe_failure=False;gateway.probe_error=True
            await monitor.tick()
            assert 'secret node endpoint' not in caplog.text
            gateway.probe_error=False
            await monitor.tick()
            assert len((await feed.feed(token))[0].splitlines())==1
            async with sessions.begin() as session:
                node=await session.get(VPNNode,node_id)
                node.hostname='replacement.example'
            with pytest.raises(AccessDenied):await feed.feed(token)
            await monitor.tick()
            assert 'replacement.example' in (await feed.feed(token))[0]
        finally:await engine.dispose()
    asyncio.run(run())


def test_lost_node_acknowledgement(tmp_path):
    async def run():
        engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'ack.db'))
        try:
            async with engine.begin() as c:await c.run_sync(Base.metadata.create_all)
            sessions=async_sessionmaker(engine,expire_on_commit=False)
            await UserService(sessions).register(2,'Test')
            p=await PaymentRepository(sessions).create_pending(2,TARIFFS['month'],uuid4())
            sub=await SubscriptionService(sessions,TestVerifier()).apply_confirmed_payment(
                ConfirmedPayment(p.id,'yookassa',str(uuid4()),29900,'RUB',utcnow()))
            feed=HappService(sessions,'https://subscriptions.example')
            token=(await feed.issue(sub.id)).rsplit('/',1)[1]
            async with sessions.begin() as session:
                session.add(VPNNode(name='Test',hostname='test.example',port=443,public_key='a'*43,
                    short_id='abcd',server_name='www.cloudflare.com',enabled=True))
            gateway=FakeNode();gateway.fail=True
            reconciler=NodeReconciler(sessions,gateway)
            with pytest.raises(RuntimeError):await reconciler.reconcile(sub.id)
            assert gateway.active  # Remote add happened, acknowledgement was lost.
            with pytest.raises(AccessDenied):await feed.feed(token)
            await feed.revoke(sub.id)
            with pytest.raises(AccessDenied):await feed.issue(sub.id)
            with pytest.raises(RuntimeError):await reconciler.reconcile(sub.id)
            async with sessions() as session:
                access=await session.scalar(select(NodeAccess))
                assert access.desired_enabled is False
                assert access.revocation_confirmed is False
            gateway.fail=False
            await reconciler.reconcile(sub.id)
            assert not gateway.active
            async with sessions() as session:
                access=await session.scalar(select(NodeAccess))
                assert access.revocation_confirmed is True
            await feed.issue(sub.id)  # Rotation only after confirmed node removal.
        finally:await engine.dispose()
    asyncio.run(run())


def test_revoke_before_link_issuance_prevents_provisioning(tmp_path):
    async def run():
        engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'early_revoke.db'))
        try:
            async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
            sessions=async_sessionmaker(engine,expire_on_commit=False)
            await UserService(sessions).register(8,'Test')
            payment=await PaymentRepository(sessions).create_pending(8,TARIFFS['month'],uuid4())
            sub=await SubscriptionService(sessions,TestVerifier()).apply_confirmed_payment(
                ConfirmedPayment(payment.id,'yookassa',str(uuid4()),29900,'RUB',utcnow()))
            async with sessions.begin() as session:
                session.add(VPNNode(name='Test',hostname='test.example',port=443,
                    public_key='a'*43,short_id='abcd',server_name='www.cloudflare.com',enabled=True))
            gateway=FakeNode();reconciler=NodeReconciler(sessions,gateway)
            service=HappService(sessions,'https://subscriptions.example')
            await service.revoke(sub.id)
            await reconciler.reconcile(sub.id)
            assert not gateway.active
            # Explicit link rotation is possible only after node revocation.
            await service.issue(sub.id)
            await reconciler.reconcile(sub.id)
            assert len(gateway.active)==1
        finally:await engine.dispose()
    asyncio.run(run())


def test_postgres_parallel_link_issuance():
    import os
    from sqlalchemy import text
    url=os.getenv('LIFECYCLE_TEST_DATABASE_URL')
    if not url:pytest.skip('Disposable PostgreSQL not configured')
    async def run():
        schema='happ_test_'+uuid4().hex
        admin=create_async_engine(url);engine=None
        try:
            async with admin.begin() as conn:await conn.execute(text('CREATE SCHEMA '+schema))
            engine=create_async_engine(url,connect_args={'options':'-c search_path='+schema})
            async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
            sessions=async_sessionmaker(engine,expire_on_commit=False)
            await UserService(sessions).register(3,'Test')
            payment=await PaymentRepository(sessions).create_pending(3,TARIFFS['month'],uuid4())
            sub=await SubscriptionService(sessions,TestVerifier()).apply_confirmed_payment(
                ConfirmedPayment(payment.id,'yookassa',str(uuid4()),29900,'RUB',utcnow()))
            service=HappService(sessions,'https://subscriptions.example')
            results=await asyncio.gather(service.issue(sub.id),service.issue(sub.id),return_exceptions=True)
            assert sum(isinstance(x,str) for x in results)==1
            assert sum(isinstance(x,AccessDenied) for x in results)==1
            async with sessions.begin() as session:
                session.add(VPNNode(name='PG',hostname='test.example',port=443,public_key='a'*43,
                    short_id='abcd',server_name='www.cloudflare.com',enabled=True))
            gateway=FakeNode();reconciler=NodeReconciler(sessions,gateway)
            await asyncio.gather(reconciler.reconcile(sub.id),reconciler.reconcile(sub.id))
            assert len(gateway.active)==1
            await service.revoke(sub.id)
            await asyncio.gather(reconciler.reconcile(sub.id),reconciler.reconcile(sub.id))
            assert not gateway.active
        finally:
            if engine:await engine.dispose()
            async with admin.begin() as conn:await conn.execute(text('DROP SCHEMA IF EXISTS '+schema+' CASCADE'))
            await admin.dispose()
    asyncio.run(run())


def test_reconciler_tick_pages_and_limits_parallel_work(tmp_path, caplog):
    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'reconcile.db'))
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            expected = set()
            for telegram_id in range(100, 111):
                await UserService(sessions).register(telegram_id, 'Test')
                sub = await SubscriptionService(sessions).create_pending(telegram_id, 'month')
                expected.add(sub.id)

            class TrackingReconciler(NodeReconciler):
                def __init__(self):
                    super().__init__(sessions, batch_size=4, max_concurrent=3)
                    self.active = self.maximum = 0
                    self.seen = []
                    self.failed = False

                async def reconcile(self, subscription_id):
                    self.active += 1
                    self.maximum = max(self.maximum, self.active)
                    self.seen.append(subscription_id)
                    try:
                        await asyncio.sleep(.01)
                        if not self.failed:
                            self.failed = True
                            raise RuntimeError('simulated node failure')
                    finally:
                        self.active -= 1

            worker = TrackingReconciler()
            await worker.tick()
            assert set(worker.seen) == expected
            assert len(worker.seen) == len(expected)
            assert worker.maximum == 3
            assert worker.active == 0
            assert caplog.text.count('Node reconciliation failed; retry scheduled') == 1
            assert 'simulated node failure' not in caplog.text
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_reconciler_requires_positive_limits():
    with pytest.raises(ValueError):
        NodeReconciler(None, batch_size=0)
    with pytest.raises(ValueError):
        NodeReconciler(None, max_concurrent=0)
