"""Status renders actual account data and reads feed eligibility without mutation."""
import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot import screens
from bot.db.models import Base, VPNNode, NodeHealth, SubscriptionToken, NodeAccess
from bot.handlers.subscription import account_for_user
from bot.services import Services, TARIFFS
from bot.services.account import AccountSnapshot
from bot.services.happ import HappService, NodeReconciler
from bot.services.node_health import NodeHealthMonitor, HEALTH_TTL_SECONDS
from bot.services.payments import PaymentRepository
from bot.services.payment_events import ConfirmedPayment
from bot.services.subscriptions import SubscriptionService, utcnow
from bot.services.users import UserService
from tests.fake_telegram import TelegramHarness
from tests.test_happ import FakeNode
from tests.test_subscription_lifecycle import TestVerifier


@pytest.mark.parametrize('status,label', [('active', 'Подписка заканчивается'), ('expired', 'Срок подписки истёк'), ('inactive', 'Подписка не активна')])
def test_status_states(status, label):
    snapshot = AccountSnapshot(status=status, tariff_label='Personal <plan>' if status != 'inactive' else None,
        expires_at=utcnow() + timedelta(days=2) if status == 'active' else None,
        device_count=999, traffic_bytes=999999999)
    screen = screens.account(snapshot, premium_account=True, node_names=('Test <EU>',))
    assert 'Kuda Status' in screen.text and label in screen.text
    assert 'Трафик:' not in screen.text and 'Устройства:' not in screen.text
    if status == 'active':
        assert 'Test &lt;EU&gt;' in screen.text and 'Personal &lt;plan&gt;' in screen.text
        assert '2 дн' in screen.text
    else:
        assert 'Серверы доступны после активации' in screen.text and 'Test &lt;EU&gt;' not in screen.text
    actions = [b.callback_data for row in screen.keyboard.inline_keyboard for b in row]
    assert all(a in actions for a in ('nav:connect', 'nav:renew', 'nav:home'))


def test_unavailable_nodes_and_renew_does_not_create_payment():
    async def run():
        services = Services.demo()
        services.premium_account_enabled = True
        services.subscriptions.snapshot = AsyncMock(return_value=AccountSnapshot(status='active'))
        services.happ = AsyncMock()
        services.happ.available_node_names_for_user.side_effect = TimeoutError()
        services.test_checkout = AsyncMock()
        services.test_payment_owner_id = 123
        screen = await account_for_user(services, 123)
        assert 'Сведения временно недоступны' in screen.text
        services.happ.issue_for_user.assert_not_called()
        app = TelegramHarness(services=services)
        try:
            await app.text('/start')
            await app.callback('nav:account')
            assert 'Kuda Status' in app.navigation.views[123].screen.text
            await app.callback('nav:renew')
            assert 'Тарифы' in app.navigation.views[123].screen.text
            services.test_checkout.create_for_user.assert_not_called()
        finally:
            await app.close()
    asyncio.run(run())


def test_node_names_require_own_entitlement_health_and_lease(tmp_path):
    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'status.db'))
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            await UserService(sessions).register(1, 'Owner')
            await UserService(sessions).register(2, 'Other')
            payment = await PaymentRepository(sessions).create_pending(1, TARIFFS['month'], uuid4())
            lifecycle = SubscriptionService(sessions, TestVerifier())
            sub = await lifecycle.apply_confirmed_payment(ConfirmedPayment(
                payment.id, 'yookassa', str(uuid4()), 29900, 'RUB', utcnow()))
            service = HappService(sessions, 'https://subscriptions.example', trial_node_port=10443)
            await service.issue(sub.id)
            async with sessions.begin() as session:
                node = VPNNode(name='Pilot', hostname='node.example', port=10443, public_key='a'*43,
                    short_id='abcd', server_name='www.cloudflare.com', enabled=True)
                session.add(node)
                await session.flush()
                node_id = node.id
            gateway = FakeNode()
            reconciler = NodeReconciler(sessions, gateway)
            await reconciler.reconcile(sub.id)
            monitor = NodeHealthMonitor(sessions, gateway)
            await monitor.tick()
            before = dict(gateway.active)
            assert await service.available_node_names_for_user(1) == ('Pilot',)
            assert await service.available_node_names_for_user(2) == ()
            assert await service.available_node_names_for_user(999) == ()
            assert gateway.active == before
            async with sessions.begin() as session:
                health = await session.get(NodeHealth, node_id)
                health.checked_at = utcnow() - timedelta(seconds=HEALTH_TTL_SECONDS+1)
            assert await service.available_node_names_for_user(1) == ()
            await monitor.tick()
            async with sessions.begin() as session:
                access = await session.scalar(select(NodeAccess))
                access.applied_expires_at = utcnow() - timedelta(seconds=1)
            assert await service.available_node_names_for_user(1) == ()
            await reconciler.reconcile(sub.id)
            assert await service.available_node_names_for_user(1) == ('Pilot',)
            async with sessions.begin() as session:
                token = await session.scalar(select(SubscriptionToken))
                token.revoked = True
            assert await service.available_node_names_for_user(1) == ()
            await lifecycle.set_status(sub.id, 'expired')
            assert await service.available_node_names_for_user(1) == ()
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_compact_status_moves_secondary_actions_into_management():
    status = screens.account(AccountSnapshot(), premium_account=True)
    actions = [b.callback_data for row in status.keyboard.inline_keyboard for b in row]
    assert actions == ['nav:connect', 'nav:renew', 'nav:subscription', 'nav:devices', 'nav:manage', 'nav:home']
    assert status.keyboard.inline_keyboard[0][0].style == 'primary'
    management = screens.account_management()
    secondary = [b.callback_data for row in management.keyboard.inline_keyboard for b in row]
    assert all(a in secondary and a not in actions for a in (
        'nav:history', 'nav:traffic', 'nav:billing', 'nav:gift', 'nav:invite'))
    assert 'nav:account' in secondary


def test_stale_active_snapshot_never_claims_access_or_available_server():
    snapshot = AccountSnapshot(status='active', expires_at=utcnow() - timedelta(seconds=1))
    text = screens.account(snapshot, premium_account=True, node_names=('Old node',)).text
    assert 'Срок подписки истёк' in text
    assert 'Подписка активна' not in text and 'Доступен' not in text and 'Old node' not in text


def test_management_back_and_connection_preserve_personal_link():
    async def run():
        services = Services.demo()
        services.premium_account_enabled = True
        services.happ = AsyncMock()
        services.happ.issue_for_user.return_value = 'https://subscriptions.example/s/' + 'a' * 43
        services.happ.available_node_names_for_user.return_value = ('Test',)
        services.subscriptions.snapshot = AsyncMock(return_value=AccountSnapshot(
            status='active', expires_at=utcnow() + timedelta(days=3)))
        services.test_checkout = AsyncMock()
        services.test_payment_owner_id = 123
        app = TelegramHarness(services=services)
        try:
            await app.text('/start')
            for route, expected in (('nav:account', 'Kuda Status'), ('nav:manage', 'Управление'),
                                    ('nav:history', 'История подписки'), ('nav:account', 'Kuda Status'),
                                    ('nav:connect', 'Ваш персональный доступ готов'), ('nav:account', 'Kuda Status'),
                                    ('nav:renew', 'Тарифы')):
                await app.callback(route)
                assert expected in app.navigation.views[123].screen.text
            services.happ.issue_for_user.assert_awaited_once_with(123)
            services.test_checkout.create_for_user.assert_not_called()
        finally:
            await app.close()
    asyncio.run(run())


@pytest.mark.parametrize('names', [None, ()])
def test_status_does_not_claim_availability_without_verified_nodes(names):
    text = screens.account(AccountSnapshot(status='active'), premium_account=True, node_names=names).text
    assert 'Доступен' not in text and 'Германия' not in text
    assert 'Нет данных' in text
