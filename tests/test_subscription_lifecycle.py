import asyncio
from datetime import datetime, timezone, timedelta
from uuid import uuid4
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select, func
from bot.db.models import Base, Subscription, SubscriptionHistory, Payment
from bot.services.users import UserService
from bot.services.subscriptions import SubscriptionService, utcnow
from bot.services.payments import PaymentRepository
from bot.services.payment_events import ConfirmedPayment, add_months
from bot.services import TARIFFS


class TestVerifier:
    __test__ = False
    async def verify(self, event):
        return event


def test_calendar_months():
    assert add_months(datetime(2024,1,31,tzinfo=timezone.utc),1).day == 29
    assert add_months(datetime(2025,1,31,tzinfo=timezone.utc),1).day == 28
    assert add_months(datetime(2024,2,29,tzinfo=timezone.utc),12).day == 28


def test_lifecycle(tmp_path):
    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'test.db'))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions=async_sessionmaker(engine,expire_on_commit=False)
        service=SubscriptionService(sessions, payment_verifier=TestVerifier())
        await UserService(sessions).register(900,'Test')
        async def event(tariff='month'):
            p=await PaymentRepository(sessions).create_pending(900,TARIFFS[tariff],uuid4())
            return ConfirmedPayment(p.id,'yookassa',str(uuid4()),TARIFFS[tariff].price_rub*100,'RUB',utcnow())
        first=await event()
        sub=await service.apply_confirmed_payment(first)
        end=sub.expires_at
        assert sub.status=='active'
        repeat=await service.apply_confirmed_payment(first)
        assert repeat.expires_at.replace(tzinfo=timezone.utc)==end
        second=await event('quarter')
        renewed=await service.apply_confirmed_payment(second)
        assert renewed.expires_at==add_months(end,3)
        async with sessions.begin() as session:
            row=await session.get(Subscription,sub.id)
            row.expires_at=utcnow()-timedelta(seconds=1)
            row.starts_at=utcnow()-timedelta(days=40)
        assert await service.expire_due()==1
        assert await service.expire_due()==0
        expired_payment=await event()
        renewed=await service.apply_confirmed_payment(expired_payment)
        assert renewed.expires_at > utcnow()+timedelta(days=27)
        await service.set_status(sub.id,'blocked')
        blocked=await event()
        with pytest.raises(ValueError): await service.apply_confirmed_payment(blocked)
        with pytest.raises(ValueError): await service.set_status(sub.id,'active')
        async with sessions() as session:
            assert (await session.get(Payment,blocked.payment_id)).status=='pending'
            assert await session.scalar(select(func.count()).select_from(SubscriptionHistory).where(SubscriptionHistory.reason=='payment'))==3
        from dataclasses import replace
        with pytest.raises(ValueError): await service.apply_confirmed_payment(replace(first,amount_kopecks=1))
        await engine.dispose()
    asyncio.run(run())


def test_bad_confirmation():
    with pytest.raises(ValueError):
        ConfirmedPayment(uuid4(),'yookassa','test',29900,'RUB',datetime.now())


def test_expiry_worker_recovers():
    from unittest.mock import AsyncMock, patch
    from bot.services.expiry import expiry_worker
    async def run():
        service=AsyncMock()
        service.expire_due.side_effect=[RuntimeError('test'),None]
        with patch('bot.services.expiry.asyncio.sleep',new=AsyncMock(side_effect=[None,asyncio.CancelledError()])):
            with pytest.raises(asyncio.CancelledError): await expiry_worker(service)
        assert service.expire_due.await_count==2
    asyncio.run(run())


def test_postgres_parallel_payments():
    """Explicit disposable test DB only; isolate all objects in a random schema."""
    import os
    from sqlalchemy import text
    url=os.environ.get('LIFECYCLE_TEST_DATABASE_URL')
    if not url:
        pytest.skip('No disposable PostgreSQL test database configured')
    async def run():
        schema='lifecycle_test_'+uuid4().hex
        admin=create_async_engine(url)
        engine=None
        try:
            async with admin.begin() as conn:
                await conn.execute(text('CREATE SCHEMA '+schema))
            engine=create_async_engine(url,connect_args={'options':'-c search_path='+schema})
            async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
            sessions=async_sessionmaker(engine,expire_on_commit=False)
            service=SubscriptionService(sessions, payment_verifier=TestVerifier())
            await UserService(sessions).register(777,'Test')
            events=[]
            for _ in range(2):
                p=await PaymentRepository(sessions).create_pending(777,TARIFFS['month'],uuid4())
                events.append(ConfirmedPayment(p.id,'yookassa',str(uuid4()),29900,'RUB',utcnow()))
            await asyncio.gather(*(service.apply_confirmed_payment(e) for e in [events[0],events[0],events[1]]))
            async with sessions() as session:
                sub=await session.scalar(select(Subscription))
                assert sub.expires_at==add_months(add_months(sub.starts_at,1),1)
                assert await session.scalar(select(func.count()).select_from(SubscriptionHistory).where(SubscriptionHistory.reason=='payment'))==2
                assert await session.scalar(select(func.count()).select_from(Subscription))==1
            # Expiry and renewal racing must converge to one active entitlement.
            async with sessions.begin() as session:
                row=await session.get(Subscription,sub.id)
                row.starts_at=utcnow()-timedelta(days=40)
                row.expires_at=utcnow()-timedelta(seconds=1)
            p=await PaymentRepository(sessions).create_pending(777,TARIFFS['month'],uuid4())
            renewal=ConfirmedPayment(p.id,'yookassa',str(uuid4()),29900,'RUB',utcnow())
            await asyncio.gather(service.expire_due(),service.apply_confirmed_payment(renewal),service.expire_due())
            async with sessions() as session:
                refreshed=await session.get(Subscription,sub.id)
                assert refreshed.status=='active' and refreshed.expires_at>utcnow()
                assert await session.scalar(select(func.count()).select_from(SubscriptionHistory).where(SubscriptionHistory.payment_id==p.id))==1
            await asyncio.gather(service.set_status(sub.id,'blocked'),service.set_status(sub.id,'blocked'))
            async with sessions() as session:
                assert await session.scalar(select(func.count()).select_from(SubscriptionHistory).where(SubscriptionHistory.reason=='status'))==1
        finally:
            if engine:await engine.dispose()
            async with admin.begin() as conn:await conn.execute(text('DROP SCHEMA IF EXISTS '+schema+' CASCADE'))
            await admin.dispose()
    asyncio.run(run())


@pytest.mark.parametrize('mode',['disabled','mismatch','error'])
def test_provider_confirmation_required(tmp_path, mode):
    from dataclasses import replace
    class RejectVerifier:
        async def verify(self,event):
            if mode=='error': raise RuntimeError('Provider unavailable')
            return replace(event,provider_payment_id='different')
    async def run():
        engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'verification.db'))
        try:
            async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
            sessions=async_sessionmaker(engine,expire_on_commit=False)
            await UserService(sessions).register(800,'Test')
            p=await PaymentRepository(sessions).create_pending(800,TARIFFS['month'],uuid4())
            event=ConfirmedPayment(p.id,'yookassa','claimed-success',29900,'RUB',utcnow())
            service=SubscriptionService(sessions,payment_verifier=None if mode=='disabled' else RejectVerifier())
            with pytest.raises((RuntimeError,ValueError)):
                await service.apply_confirmed_payment(event)
            async with sessions() as session:
                assert await session.scalar(select(func.count()).select_from(Subscription))==0
                assert await session.scalar(select(func.count()).select_from(SubscriptionHistory))==0
                assert (await session.get(Payment,p.id)).status=='pending'
        finally:await engine.dispose()
    asyncio.run(run())
