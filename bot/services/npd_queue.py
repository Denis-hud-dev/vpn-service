"""Disabled-by-default SANDBOX dispatcher over canonical receipt obligations.

No HTTP adapter, FNS credentials, startup registration or Telegram client.
Injected simulations may exercise crash recovery; they never mark an actual
NPDReceipt issued/delivered. All external registration remains unavailable.
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime,timedelta
import hashlib
from pathlib import Path
import re
import tempfile
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID,uuid4

from sqlalchemy import select,update,or_,and_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from bot.db.models import (NPDReceipt,NPDReceiptEvent,NPDReceiptJob,NPDReceiptHold,
                          NPDAdminAlert,Payment,SubscriptionHistory,User)
from bot.services.npd_receipts import NPDLedger
from bot.services.subscriptions import as_utc,utcnow


@dataclass(frozen=True)
class ReceiptRequest:
    operation_key: str
    payment_id: UUID
    buyer_id: UUID  # Internal account reference, not a phone/email/Telegram ID.
    tariff_id: str
    amount_kopecks: int
    paid_at: datetime
    currency: str = 'RUB'
    regime: str = 'npd'
    vat: str = 'not_applicable'


@dataclass(frozen=True)
class SimulatedReceipt:
    operation_key: str
    amount_kopecks: int
    document_ref: str
    url: str
    simulated: bool = True


@dataclass(frozen=True)
class ReceiptPending:
    operation_key: str


class SandboxAdapter(Protocol):
    environment: str
    async def lookup(self,operation_key: str) -> SimulatedReceipt | ReceiptPending | None: ...
    async def register(self,request: ReceiptRequest) -> SimulatedReceipt | ReceiptPending: ...


def operation_key(payment_id):
    return hashlib.sha256(('npd:sandbox:v1:'+str(payment_id)).encode()).hexdigest()


def insert(session,model):
    return (pg_insert if session.bind.dialect.name=='postgresql' else sqlite_insert)(model)


class NPDQueue:
    def __init__(self,sessions,adapter: SandboxAdapter,*,owner_id: int,enabled=False,clock=utcnow):
        if type(owner_id) is not int or owner_id<=0 or type(enabled) is not bool or adapter.environment!='sandbox':
            raise ValueError('Explicit owner and sandbox-only adapter required')
        bind=sessions.kw.get('bind');url=bind.url
        offline=(bind.dialect.name=='sqlite' and url.database is not None
                 and (url.database==':memory:' or Path(url.database).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())))
        pilot=(bind.dialect.name=='postgresql' and url.host=='127.0.0.1'
               and url.database=='kudavpn_telegram_pilot' and url.username=='kudavpn_pilot')
        if not (offline or pilot):raise ValueError('Disposable or approved isolated test database required')
        self.sessions,self.adapter,self.owner_id=sessions,adapter,owner_id
        self.enabled,self.clock=enabled,clock
        self.ledger=NPDLedger(sessions,test=True)

    async def _event(self,s,payment_id,key,state,error=None,document=None):
        await s.execute(insert(s,NPDReceiptEvent).values(operation=key,payment_id=payment_id,
            state=state,error_code=error,document_ref=document).on_conflict_do_nothing())

    async def _alert(self,s,payment_id,kind):
        await s.execute(insert(s,NPDAdminAlert).values(event_key=f'npd:{payment_id}:{kind}',
            payment_id=payment_id,kind=kind,state='pending').on_conflict_do_nothing())

    async def enqueue(self):
        if not self.enabled:return 0
        await self.ledger.tick()  # Unique obligation recovers a lost post-payment callback.
        async with self.sessions.begin() as s:
            rows=(await s.scalars(select(NPDReceipt).join(Payment).outerjoin(NPDReceiptJob)
                .where(NPDReceiptJob.payment_id.is_(None),NPDReceipt.environment=='test',
                       Payment.status=='succeeded').limit(100))).all()
            for receipt in rows:
                held=await s.scalar(select(NPDReceiptHold.refund_id).where(NPDReceiptHold.payment_id==receipt.payment_id).limit(1))
                await s.execute(insert(s,NPDReceiptJob).values(payment_id=receipt.payment_id,
                    operation_key=operation_key(receipt.payment_id),state='review' if held else 'queued',
                    error_code='refund_review' if held else None,attempts=0,due_at=as_utc(self.clock()))
                    .on_conflict_do_nothing())
            return len(rows)

    async def hold_refund(self,payment_id,refund_id,amount_kopecks,*,provider_verified=False):
        if not self.enabled:return
        if (provider_verified is not True or not isinstance(refund_id,str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',refund_id)
                or type(amount_kopecks) is not int or amount_kopecks<=0):
            raise ValueError('Verified refund required')
        async with self.sessions.begin() as s:
            payment=await s.get(Payment,payment_id)
            if not payment or payment.currency!='RUB' or amount_kopecks>payment.amount_kopecks:
                raise ValueError('Refund does not match canonical payment')
            prior=await s.get(NPDReceiptHold,refund_id)
            if prior and (prior.payment_id!=payment_id or prior.amount_kopecks!=amount_kopecks):
                raise ValueError('Refund identity conflict')
            await s.execute(insert(s,NPDReceiptHold).values(refund_id=refund_id,payment_id=payment_id,
                amount_kopecks=amount_kopecks,state='review').on_conflict_do_nothing())
            await s.execute(update(NPDReceiptJob).where(NPDReceiptJob.payment_id==payment_id)
                .values(state='review',error_code='refund_review',lease_token=None,lease_until=None))
            await self._alert(s,payment_id,'refund_review')
        # Never change Payment grants, subscription time, UUID or a registered receipt.

    async def _claim(self):
        now=as_utc(self.clock());token=uuid4().hex
        eligible=or_(and_(NPDReceiptJob.state.in_(['queued','retry']),NPDReceiptJob.due_at<=now),
                     and_(NPDReceiptJob.state=='processing',NPDReceiptJob.lease_until<=now))
        async with self.sessions.begin() as s:
            key=await s.scalar(select(NPDReceiptJob.payment_id).where(eligible)
                .order_by(NPDReceiptJob.due_at).limit(1))
            if key is None:return None
            result=await s.execute(update(NPDReceiptJob).where(NPDReceiptJob.payment_id==key,eligible)
                .values(state='processing',lease_token=token,lease_until=now+timedelta(minutes=2),
                        attempts=NPDReceiptJob.attempts+1).returning(NPDReceiptJob.attempts))
            attempt=result.scalar_one_or_none()
            return (key,token,attempt) if attempt is not None else None

    async def _request(self,key):
        async with self.sessions() as s:
            payment=await s.get(Payment,key);receipt=await s.get(NPDReceipt,key)
            grant=await s.scalar(select(SubscriptionHistory.id).where(
                SubscriptionHistory.payment_id==key,SubscriptionHistory.reason=='payment'))
            held=await s.scalar(select(NPDReceiptHold.refund_id).where(NPDReceiptHold.payment_id==key).limit(1))
            if held:return None,'refund_review'
            if (not payment or not receipt or not grant or payment.status!='succeeded'
                    or payment.currency!='RUB' or payment.paid_at is None or receipt.environment!='test'
                    or receipt.state!='test_not_required' or receipt.amount_kopecks!=payment.amount_kopecks):
                return None,'source_mismatch'
            return ReceiptRequest(operation_key(key),key,payment.user_id,payment.tariff_id,
                                  payment.amount_kopecks,as_utc(payment.paid_at)),None

    @staticmethod
    def _valid(result,request):
        if (not isinstance(result,SimulatedReceipt) or result.simulated is not True
                or not isinstance(result.document_ref,str) or not isinstance(result.url,str)
                or type(result.amount_kopecks) is not int):return False
        try:url=urlsplit(result.url)
        except (ValueError,TypeError):return False
        return (result.operation_key==request.operation_key and result.amount_kopecks==request.amount_kopecks
            and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,128}',result.document_ref))
            and url.scheme=='https' and bool(url.hostname) and url.hostname.endswith('.invalid')
            and not url.username and not url.password and len(result.url)<=512)

    async def _finish(self,key,token,attempt,*,result=None,error=None):
        async with self.sessions.begin() as s:
            job=await s.scalar(select(NPDReceiptJob).where(NPDReceiptJob.payment_id==key).with_for_update())
            if job.state!='processing' or job.lease_token!=token:return  # Stale result after refund/new lease.
            held=await s.scalar(select(NPDReceiptHold.refund_id).where(NPDReceiptHold.payment_id==key).limit(1))
            if held:error='refund_review';result=None
            payment=await s.get(Payment,key)
            if payment.status!='succeeded':error='source_mismatch';result=None
            if error:
                job.state='review' if attempt>=5 or error not in {'adapter_unavailable','registration_pending'} else 'retry'
                job.error_code=error;job.due_at=as_utc(self.clock())+timedelta(seconds=min(3600,60*2**min(attempt-1,6)))
                await self._event(s,key,f'attempt:{job.operation_key}:{attempt}',job.state,error)
                await self._alert(s,key,job.state)
            else:
                job.state='simulated';job.error_code=None
                job.document_ref=result.document_ref;job.receipt_url=result.url
                await self._event(s,key,'simulated:'+job.operation_key,'sandbox_registered',document=result.document_ref)
                if attempt>1:await self._alert(s,key,'recovered')
            job.lease_token=None;job.lease_until=None

    async def tick(self,*,limit=10):
        if not self.enabled:return {'enabled':False}
        if not 1<=limit<=100:raise ValueError('Bounded job count required')
        await self.enqueue();processed=0
        for _ in range(limit):
            claim=await self._claim()
            if claim is None:break
            key,token,attempt=claim;request,error=await self._request(key);result=None
            if not error:
                try:
                    async with asyncio.timeout(20):
                        result=await self.adapter.lookup(request.operation_key)
                        if result is None:result=await self.adapter.register(request)
                    if isinstance(result,ReceiptPending) and result.operation_key==request.operation_key:
                        error='registration_pending';result=None
                    elif not self._valid(result,request):error='invalid_response';result=None
                except Exception:
                    error='adapter_unavailable'  # Never persist exception text or provider payloads.
            await self._finish(key,token,attempt,result=result,error=error);processed+=1
        return {'enabled':True,'processed':processed}

    async def details(self,owner,payment_id):
        async with self.sessions() as s:
            row=(await s.execute(select(Payment,NPDReceiptJob).join(User,User.id==Payment.user_id)
                .join(NPDReceiptJob,NPDReceiptJob.payment_id==Payment.id)
                .where(Payment.id==payment_id,User.telegram_id==owner))).one_or_none()
            if row is None:raise PermissionError('Receipt unavailable')
            payment,job=row
            refund=await s.scalar(select(NPDReceiptHold.refund_id).where(NPDReceiptHold.payment_id==payment_id).limit(1))
            return {'payment_id':payment.id,'buyer_id':payment.user_id,'tariff_id':payment.tariff_id,
                'amount_kopecks':payment.amount_kopecks,'state':job.state,'attempts':job.attempts,
                'error':job.error_code,'simulation_url':job.receipt_url,'refund_review':refund is not None}

    async def deliver_alerts(self,sender):
        if not self.enabled:return
        now=as_utc(self.clock())
        async with self.sessions() as s:
            keys=(await s.scalars(select(NPDAdminAlert.event_key).where(or_(NPDAdminAlert.state=='pending',
                and_(NPDAdminAlert.state=='sending',NPDAdminAlert.lease_until<=now))).limit(20))).all()
        for key in keys:
            token=uuid4().hex
            async with self.sessions.begin() as s:
                claimed=await s.execute(update(NPDAdminAlert).where(NPDAdminAlert.event_key==key,
                    or_(NPDAdminAlert.state=='pending',and_(NPDAdminAlert.state=='sending',NPDAdminAlert.lease_until<=now)))
                    .values(state='sending',lease_token=token,lease_until=now+timedelta(minutes=2))
                    .returning(NPDAdminAlert.kind))
                kind=claimed.scalar_one_or_none()
            if kind is None:continue
            try:
                case=hashlib.sha256(key.encode()).hexdigest()[:10]
                async with asyncio.timeout(20):
                    await sender(self.owner_id,'KudaVPN · тест очереди НПД: '+kind+' · '+case+'. Документ ФНС не выдавался.')
            except Exception:
                state='pending'
            else:state='sent'
            async with self.sessions.begin() as s:
                await s.execute(update(NPDAdminAlert).where(NPDAdminAlert.event_key==key,NPDAdminAlert.lease_token==token)
                    .values(state=state,lease_token=None,lease_until=None))
