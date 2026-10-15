"""One stable Happ link is delivered only to its entitled Telegram owner."""
import asyncio
import base64
from datetime import timedelta
from uuid import uuid4

import pytest
from aiogram.methods import AnswerCallbackQuery, SendMessage
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from alembic import command
from alembic.config import Config as AlembicConfig

from bot.db.models import Base, Subscription, SubscriptionToken, User, VPNNode
from bot.services import Services, TARIFFS
from bot.services.happ import AccessDenied, HappService, NodeReconciler, token_digest
from bot.services.node_health import NodeHealthMonitor
from bot.services.payment_events import ConfirmedPayment
from bot.services.payments import PaymentRepository
from bot.services.subscriptions import SubscriptionService, utcnow
from bot.services.users import UserService
from tests.fake_telegram import TelegramHarness
from tests.test_happ import FakeNode
from tests.test_subscription_lifecycle import TestVerifier

TEST_KEY = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b'=').decode()


def test_stable_private_link_and_fail_closed_bot_screen(tmp_path):
    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'delivery.db'))
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        app = None
        try:
            await UserService(sessions).register(123, 'Owner')
            await UserService(sessions).register(456, 'Other')
            service = SubscriptionService(sessions, TestVerifier())
            paid = await PaymentRepository(sessions).create_pending(123, TARIFFS['month'], uuid4())
            sub = await service.apply_confirmed_payment(ConfirmedPayment(
                paid.id, 'yookassa', str(uuid4()), 29900, 'RUB', utcnow()))
            async with sessions.begin() as session:
                session.add(VPNNode(name='NL', hostname='vpn.example', port=443,
                    public_key='a' * 43, short_id='abcd', server_name='www.cloudflare.com', enabled=True))
            gateway = FakeNode()
            happ = HappService(sessions, 'https://subscriptions.example', link_key=TEST_KEY)
            services = Services.persistent(sessions,
                subscription_public_url='https://subscriptions.example', happ_link_secret=TEST_KEY)
            app = TelegramHarness(services=services)
            await app.text('/start')
            await app.callback('nav:connect')
            assert 'https://' not in app.navigation.views[123].screen.text
            await NodeReconciler(sessions, gateway).reconcile(sub.id)
            await NodeHealthMonitor(sessions, gateway).tick()
            await app.callback('nav:connect')
            screen = app.navigation.views[123].screen
            link = next(button.url for row in screen.keyboard.inline_keyboard for button in row if button.url)
            assert link and link in screen.text
            assert (await happ.feed(link.rsplit('/', 1)[1]))[0].count('vless://') == 1
            async with sessions() as session:
                token = await session.scalar(select(SubscriptionToken))
                assert token.derivation_nonce and link.rsplit('/', 1)[1] not in token.digest
            for route in ('nav:home', 'nav:connect', 'nav:account', 'nav:connect'):
                await app.callback(route)
            assert link in app.navigation.views[123].screen.text
            assert len([call for call in app.calls if isinstance(call, SendMessage)]) == 1
            second = await PaymentRepository(sessions).create_pending(123, TARIFFS['quarter'], uuid4())
            await service.apply_confirmed_payment(ConfirmedPayment(
                second.id, 'yookassa', str(uuid4()), 79900, 'RUB', utcnow()))
            assert await happ.issue_for_user(123) == link
            await app.text('/start', user_id=456)
            await app.callback('nav:connect', user_id=456)
            assert 'https://' not in app.navigation.views[456].screen.text
            assert len([call for call in app.calls if isinstance(call, AnswerCallbackQuery)]) >= 7
            gateway.probe_failure = True
            await NodeHealthMonitor(sessions, gateway).tick()
            await app.callback('nav:connect')
            assert 'https://' not in app.navigation.views[123].screen.text
            gateway.probe_failure = False
            await NodeHealthMonitor(sessions, gateway).tick()
            assert await happ.issue_for_user(123) == link
            async with sessions.begin() as session:
                row = await session.get(Subscription, sub.id)
                row.starts_at = utcnow() - timedelta(days=40)
                row.expires_at = utcnow() - timedelta(seconds=1)
            with pytest.raises(AccessDenied):
                await happ.issue_for_user(123)
            await app.callback('nav:connect')
            assert 'https://' not in app.navigation.views[123].screen.text
        finally:
            if app:
                await app.close()
            await engine.dispose()
    asyncio.run(run())


def test_link_key_validation_and_rotation_safety(tmp_path):
    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'rotation.db'))
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await UserService(sessions).register(111, 'Owner')
            payment = await PaymentRepository(sessions).create_pending(111, TARIFFS['month'], uuid4())
            sub = await SubscriptionService(sessions, TestVerifier()).apply_confirmed_payment(
                ConfirmedPayment(payment.id, 'yookassa', str(uuid4()), 29900, 'RUB', utcnow()))
            stable = HappService(sessions, 'https://subscriptions.example', link_key=TEST_KEY)
            first = await stable.issue(sub.id)
            assert await stable.issue(sub.id) == first
            wrong_key = base64.urlsafe_b64encode(bytes(reversed(range(32)))).rstrip(b'=').decode()
            with pytest.raises(AccessDenied):
                await HappService(sessions, 'https://subscriptions.example', link_key=wrong_key).issue(sub.id)
            async with sessions() as session:
                row = await session.scalar(select(SubscriptionToken))
                assert row.digest != first.rsplit('/', 1)[1]
            with pytest.raises(ValueError):
                HappService(sessions, 'https://subscriptions.example', link_key='invalid')
            await UserService(sessions).register(222, 'Legacy')
            old_payment = await PaymentRepository(sessions).create_pending(222, TARIFFS['month'], uuid4())
            old_sub = await SubscriptionService(sessions, TestVerifier()).apply_confirmed_payment(
                ConfirmedPayment(old_payment.id, 'yookassa', str(uuid4()), 29900, 'RUB', utcnow()))
            old_link = await HappService(sessions, 'https://subscriptions.example').issue(old_sub.id)
            with pytest.raises(AccessDenied):
                await stable.issue(old_sub.id)
            async with sessions() as session:
                old_row = await session.scalar(select(SubscriptionToken).where(
                    SubscriptionToken.subscription_id == old_sub.id))
                assert old_row.digest == token_digest(old_link.rsplit('/', 1)[1])
                assert old_row.derivation_nonce is None
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_nonce_migration_preserves_old_issued_link_digest(tmp_path):
    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'migration.db'))
        try:
            async with engine.begin() as connection:
                def upgrade(raw, target):
                    config = AlembicConfig('alembic.ini')
                    config.attributes['connection'] = raw
                    command.upgrade(config, target)
                await connection.run_sync(upgrade, '0006_node_health')
                user_id, sub_id, digest = uuid4(), uuid4(), 'a' * 64
                await connection.execute(User.__table__.insert().values(
                    id=user_id, telegram_id=42, first_name='Existing'))
                await connection.execute(Subscription.__table__.insert().values(
                    id=sub_id, user_id=user_id, tariff_id='month', status='pending'))
                await connection.execute(text('INSERT INTO subscription_tokens '
                    '(id, subscription_id, digest, revoked) VALUES (:id, :sub, :digest, 0)'),
                    {'id': uuid4().hex, 'sub': sub_id.hex, 'digest': digest})
                await connection.run_sync(upgrade, 'head')
                row = (await connection.execute(text('SELECT digest, derivation_nonce FROM subscription_tokens'))).one()
                assert row == (digest, None)
        finally:
            await engine.dispose()
    asyncio.run(run())
