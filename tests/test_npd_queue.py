"""No FNS/Telegram network calls; canonical payments and simulated provider."""
import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select,func

from bot.db.models import NPDReceipt,NPDReceiptEvent,NPDReceiptJob,NPDAdminAlert,NPDReceiptHold,Subscription,SubscriptionHistory
from bot.services import PILOT_TARIFFS
from bot.services.payments import PaymentCheckoutService
from bot.services.payment_notifications import PaymentNotificationProcessor
from bot.services.npd_queue import NPDQueue,SimulatedReceipt,ReceiptPending
from bot.services.subscriptions import utcnow,as_utc
from tests.test_yookassa_test_mode import setup


class Adapter:
    environment='sandbox'
    def __init__(self):self.rows={};self.calls=[];self.fail=False;self.crash_after_registration=False;self.queue=None
    async def lookup(self,key):return self.rows.get(key)
    async def register(self,request):
        self.calls.append(request)
        if self.fail:raise RuntimeError('sensitive-provider-payload-must-not-persist')
        result=SimulatedReceipt(request.operation_key,request.amount_kopecks,'fixture_document',
                                'https://receipts.invalid/private-fixture')
        self.rows[request.operation_key]=result
        if self.queue:await self.queue.hold_refund(request.payment_id,'refund-during-call',100,provider_verified=True)
        if self.crash_after_registration:raise SystemExit('simulated process loss')
        return result


async def prepare(tmp_path):
    engine,db,fake,client,_=await setup(tmp_path)
    checkout=PaymentCheckoutService(db,client,'https://site.example/account',tariffs=PILOT_TARIFFS)
    processor=PaymentNotificationProcessor(db,client)
    intent=await checkout.create_for_user(501,'pilot_month_199')
    fake.rows[intent.provider_id].update(status='succeeded',paid=True,captured_at=utcnow().isoformat())
    await processor.process(intent.provider_id)
    async with db() as s:
        sub=await s.scalar(select(Subscription));identity=(sub.id,as_utc(sub.expires_at))
    adapter=Adapter();now=[utcnow()]
    queue=NPDQueue(db,adapter,owner_id=501,enabled=True,clock=lambda:now[0])
    return engine,db,processor,intent,adapter,now,queue,identity


def test_paid_duplicate_creates_one_simulation_and_keeps_canonical_access(tmp_path):
    async def run():
        engine,db,processor,intent,adapter,now,queue,identity=await prepare(tmp_path)
        try:
            await processor.process(intent.provider_id)
            for _ in range(2):await queue.tick()
            async with db() as s:
                assert await s.scalar(select(func.count()).select_from(NPDReceiptJob))==1
                job=await s.scalar(select(NPDReceiptJob));receipt=await s.get(NPDReceipt,job.payment_id)
                assert job.state=='simulated' and receipt.state=='test_not_required'
                assert await s.scalar(select(func.count()).select_from(SubscriptionHistory).where(SubscriptionHistory.reason=='payment'))==1
                sub=await s.scalar(select(Subscription));assert (sub.id,as_utc(sub.expires_at))==identity
            assert len(adapter.calls)==1 and adapter.calls[0].vat=='not_applicable'
            assert (await queue.details(501,job.payment_id))['simulation_url'].endswith('private-fixture')
            with pytest.raises(PermissionError):await queue.details(502,job.payment_id)
        finally:await engine.dispose()
    asyncio.run(run())


def test_failure_backoff_redaction_owner_alert_and_recovery(tmp_path):
    async def run():
        engine,db,_,_,adapter,now,queue,_=await prepare(tmp_path)
        try:
            adapter.fail=True;await queue.tick();await queue.tick()
            assert len(adapter.calls)==1
            async with db() as s:
                job=await s.scalar(select(NPDReceiptJob));assert job.state=='retry' and job.error_code=='adapter_unavailable'
                events=(await s.scalars(select(NPDReceiptEvent))).all()
                assert all('sensitive' not in str(e.error_code) for e in events)
            sender=AsyncMock();await queue.deliver_alerts(sender);await queue.deliver_alerts(sender)
            sender.assert_awaited_once();assert sender.await_args.args[0]==501
            assert 'https://' not in sender.await_args.args[1] and 'private-fixture' not in sender.await_args.args[1]
            adapter.fail=False;now[0]+=timedelta(minutes=2);await queue.tick()
            assert len(adapter.calls)==2
            await queue.deliver_alerts(sender);assert sender.await_count==2
        finally:await engine.dispose()
    asyncio.run(run())


def test_provider_commit_then_crash_recovers_by_same_key_without_second_receipt(tmp_path):
    async def run():
        engine,db,_,_,adapter,now,queue,_=await prepare(tmp_path)
        try:
            adapter.crash_after_registration=True
            with pytest.raises(SystemExit):await queue.tick()
            now[0]+=timedelta(minutes=3)
            await NPDQueue(db,adapter,owner_id=501,enabled=True,clock=lambda:now[0]).tick()
            assert len(adapter.calls)==1
            async with db() as s:
                job=await s.scalar(select(NPDReceiptJob));assert job.state=='simulated' and job.attempts==2
                assert await s.scalar(select(func.count()).select_from(NPDReceiptEvent).where(NPDReceiptEvent.state=='sandbox_registered'))==1
        finally:await engine.dispose()
    asyncio.run(run())


@pytest.mark.parametrize('during',[False,True])
def test_verified_refund_holds_queue_without_revoking_access(tmp_path,during):
    async def run():
        engine,db,_,_,adapter,now,queue,identity=await prepare(tmp_path)
        try:
            await queue.enqueue()
            async with db() as s:key=await s.scalar(select(NPDReceiptJob.payment_id))
            if during:adapter.queue=queue
            else:
                for _ in range(2):await queue.hold_refund(key,'verified-refund',100,provider_verified=True)
            await queue.tick()
            async with db() as s:
                job=await s.get(NPDReceiptJob,key);assert job.state=='review' and not job.receipt_url
                receipt=await s.get(NPDReceipt,key);assert receipt.state=='test_not_required'
                sub=await s.scalar(select(Subscription));assert (sub.id,as_utc(sub.expires_at))==identity
                assert await s.scalar(select(func.count()).select_from(NPDReceiptHold))==1
            if not during:assert adapter.calls==[]
        finally:await engine.dispose()
    asyncio.run(run())


def test_disabled_queue_and_wrong_environment_never_send_or_issue(tmp_path):
    async def run():
        engine,db,_,_,adapter,now,_,_=await prepare(tmp_path)
        try:
            queue=NPDQueue(db,adapter,owner_id=501)
            assert await queue.tick()=={'enabled':False}
            sender=AsyncMock();await queue.deliver_alerts(sender);sender.assert_not_awaited()
            assert not adapter.calls
            adapter.environment='live'
            with pytest.raises(ValueError):NPDQueue(db,adapter,owner_id=501,enabled=True)
            adapter.environment='sandbox'
            from types import SimpleNamespace
            from sqlalchemy.engine import make_url
            foreign=SimpleNamespace(kw={'bind':SimpleNamespace(dialect=SimpleNamespace(name='postgresql'),
                url=make_url('postgresql+psycopg://blocked@127.0.0.1/production'))})
            with pytest.raises(ValueError):NPDQueue(foreign,adapter,owner_id=501,enabled=True)
        finally:await engine.dispose()
    asyncio.run(run())


def test_exhausted_retries_require_review_and_deduplicate_admin_errors(tmp_path):
    async def run():
        engine,db,_,_,adapter,now,queue,_=await prepare(tmp_path)
        try:
            adapter.fail=True
            for _ in range(6):
                await queue.tick();now[0]+=timedelta(hours=2)
            async with db() as s:
                job=await s.scalar(select(NPDReceiptJob));assert job.state=='review' and job.attempts==5
                assert await s.scalar(select(func.count()).select_from(NPDAdminAlert))==2
                assert await s.scalar(select(func.count()).select_from(NPDReceiptEvent).where(NPDReceiptEvent.error_code=='adapter_unavailable'))==5
            assert len(adapter.calls)==5
        finally:await engine.dispose()
    asyncio.run(run())


def test_invalid_response_is_not_a_real_receipt_or_retryable_success(tmp_path):
    async def run():
        engine,db,_,_,adapter,now,queue,_=await prepare(tmp_path)
        try:
            await queue.enqueue()
            async with db() as s:job=await s.scalar(select(NPDReceiptJob))
            adapter.rows[job.operation_key]=SimulatedReceipt(job.operation_key,19900,'untrusted','https://nalog.ru/real-looking',False)
            await queue.tick()
            async with db() as s:
                job=await s.get(NPDReceiptJob,job.payment_id)
                assert job.state=='review' and job.error_code=='invalid_response' and job.receipt_url is None
                assert (await s.get(NPDReceipt,job.payment_id)).state=='test_not_required'
        finally:await engine.dispose()
    asyncio.run(run())


def test_lease_and_admin_outbox_claims_exclude_competing_workers(tmp_path):
    async def run():
        engine,db,_,_,adapter,now,queue,_=await prepare(tmp_path)
        try:
            await queue.enqueue();key,token,attempt=await queue._claim()
            other=NPDQueue(db,adapter,owner_id=501,enabled=True,clock=lambda:now[0])
            assert await other._claim() is None
            await queue._finish(key,token,attempt,error='adapter_unavailable')
            sender=AsyncMock(side_effect=RuntimeError('private Telegram error'))
            await queue.deliver_alerts(sender)
            sender=AsyncMock()
            await asyncio.gather(queue.deliver_alerts(sender),other.deliver_alerts(sender))
            assert sender.await_count==1
            await queue.deliver_alerts(sender);assert sender.await_count==1
        finally:await engine.dispose()
    asyncio.run(run())


def test_refund_hold_precedes_obligation_and_rejects_unverified_or_conflicting_ids(tmp_path):
    async def run():
        engine,db,_,intent,adapter,now,queue,_=await prepare(tmp_path)
        try:
            from bot.db.models import Payment
            async with db() as s:key=await s.scalar(select(Payment.id))
            with pytest.raises(ValueError):await queue.hold_refund(key,'refund1',100)
            await queue.hold_refund(key,'refund1',100,provider_verified=True)
            with pytest.raises(ValueError):await queue.hold_refund(key,'refund1',101,provider_verified=True)
            await queue.tick()
            assert not adapter.calls
            async with db() as s:assert (await s.get(NPDReceiptJob,key)).state=='review'
        finally:await engine.dispose()
    asyncio.run(run())


def test_asynchronous_registration_is_polled_not_registered_twice(tmp_path):
    async def run():
        engine,db,_,_,adapter,now,queue,_=await prepare(tmp_path)
        try:
            async def accepted(request):
                adapter.calls.append(request)
                adapter.rows[request.operation_key]=ReceiptPending(request.operation_key)
                return adapter.rows[request.operation_key]
            adapter.register=accepted
            await queue.tick()
            async with db() as s:
                job=await s.scalar(select(NPDReceiptJob));assert job.state=='retry' and job.error_code=='registration_pending'
            now[0]+=timedelta(minutes=2)
            await queue.tick();assert len(adapter.calls)==1
            adapter.rows[job.operation_key]=SimulatedReceipt(job.operation_key,19900,'simulated_ref','https://receipts.invalid/fixture')
            now[0]+=timedelta(minutes=3)
            await queue.tick();assert len(adapter.calls)==1
            async with db() as s:assert (await s.get(NPDReceiptJob,job.payment_id)).state=='simulated'
        finally:await engine.dispose()
    asyncio.run(run())


def test_existing_verified_refund_processor_records_one_canonical_receipt_hold(tmp_path):
    async def run():
        from types import SimpleNamespace
        from unittest.mock import Mock
        from bot.services.payment_refunds import TestRefundProcessor
        engine,db,processor,intent,adapter,now,queue,identity=await prepare(tmp_path)
        try:
            await queue.enqueue()
            processor.lookup.fetch_refund=AsyncMock(return_value={'id':'verified-api-refund',
                'payment_id':intent.provider_id,'status':'succeeded','amount':{'value':'199.00','currency':'RUB'}})
            refund=TestRefundProcessor(db,processor.lookup,SimpleNamespace(record=Mock()),receipt_queue=queue)
            for _ in range(2):assert await refund.process('verified-api-refund',intent.provider_id)=='review'
            await queue.tick();assert not adapter.calls
            async with db() as s:
                assert await s.scalar(select(func.count()).select_from(NPDReceiptHold))==1
                sub=await s.scalar(select(Subscription));assert (sub.id,as_utc(sub.expires_at))==identity
                assert (await s.scalar(select(NPDReceiptJob))).state=='review'
        finally:await engine.dispose()
    asyncio.run(run())
