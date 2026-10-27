"""Durable test-shop recovery with disposable DB/files and fake provider only."""
import asyncio
from contextlib import asynccontextmanager, closing
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import func, select

from bot.db.models import Payment, Subscription, SubscriptionHistory
from bot.services import PILOT_TARIFFS
from bot.services.checkout_sessions import CheckoutSessions
from bot.services.payment_events import add_months
from bot.services.payment_notifications import PaymentNotificationProcessor
from bot.services.payment_reconciliation import ReconciliationJournal, TestPaymentReconciler
from bot.services.payment_refunds import RefundReviews, TestRefundProcessor as RefundProcessor
from bot.services.payments import PaymentCheckoutService
from bot.services.subscriptions import as_utc, utcnow
from bot.subscription_api.payment_webhook import install_test_payment_webhook
from tests.test_yookassa_test_mode import setup


@asynccontextmanager
async def fixture(tmp_path):
    engine, sessions, fake, client, _ = await setup(tmp_path)
    now = [utcnow() + timedelta(minutes=2)]
    listing_calls, faults = [], set()

    async def list_page(kind, start, end, cursor):
        listing_calls.append((kind, start, end, cursor))
        if kind in faults or (kind, cursor) in faults:
            raise TimeoutError('simulated provider outage')
        values = [deepcopy(row) for row in fake.rows.values()
                  if ('test' in row if kind == 'payments' else 'payment_id' in row)]
        offset = int(cursor or 0)
        return values[offset:offset+1], str(offset+1) if offset+1 < len(values) else None

    client.list_page = list_page
    checkout = PaymentCheckoutService(sessions, client, 'https://site.example/account', tariffs=PILOT_TARIFFS)
    processor = PaymentNotificationProcessor(sessions, client)
    reviews = RefundReviews(CheckoutSessions(str(tmp_path / 'sessions.sqlite'), 501))
    refunds = RefundProcessor(sessions, client, reviews)
    journal = ReconciliationJournal(tmp_path / 'reconciliation.sqlite')
    receipts = SimpleNamespace(tick=AsyncMock())

    def worker():
        return TestPaymentReconciler(sessions, client, processor, refunds,
            ReconciliationJournal(journal.path), enabled=True, clock=lambda: now[0], receipt_ledger=receipts)

    async def order(plan='pilot_month_199', status='succeeded'):
        intent = await checkout.create_for_user(501, plan)
        fake.rows[intent.provider_id].update(status=status, paid=status == 'succeeded', captured_at=utcnow().isoformat())
        return intent.provider_id

    async def snapshot():
        async with sessions() as session:
            sub = await session.scalar(select(Subscription))
            count = await session.scalar(select(func.count()).select_from(SubscriptionHistory).where(SubscriptionHistory.reason == 'payment'))
            return (sub.id, as_utc(sub.expires_at), sub.status, count) if sub else (None, None, None, count)

    try:
        yield SimpleNamespace(**locals())
    finally:
        await engine.dispose()


def test_disabled_worker_makes_no_calls(tmp_path):
    async def run():
        async with fixture(tmp_path) as f:
            worker = f.worker(); worker.enabled = False
            assert await worker.tick() == {'enabled': False}
            assert f.listing_calls == [] and f.fake.calls == []
    asyncio.run(run())


@pytest.mark.parametrize('expired', [False, True])
def test_no_webhook_new_purchase_renewal_and_replayed_ack_are_once_only(tmp_path, expired):
    async def run():
        async with fixture(tmp_path) as f:
            first = await f.order()
            await f.worker().tick()
            before = await f.snapshot()
            assert before[2:] == ('active', 1)
            async with f.sessions.begin() as session:
                row = await session.scalar(select(Payment)); row.created_at = utcnow()-timedelta(minutes=2)
                if expired:
                    sub = await session.scalar(select(Subscription))
                    sub.starts_at = utcnow()-timedelta(days=40)
                    sub.expires_at = utcnow()-timedelta(days=1)
            second = await f.order('pilot_quarter_499')
            await f.worker().tick()
            after = await f.snapshot()
            assert after[0] == before[0] and after[3] == 2
            if not expired:
                assert after[1] == add_months(before[1], 3)
            else:
                assert after[1] > add_months(utcnow()-timedelta(seconds=3), 3)
            # Fulfillment committed, process died before acknowledging inbox.
            with closing(f.journal.connect()) as db, db:
                db.execute("UPDATE inbox SET state='queued',due=0 WHERE kind='payments'")
            f.now[0] += timedelta(minutes=1)
            await f.worker().tick()
            assert await f.snapshot() == after
            assert len([c for c in f.fake.calls if c[0]=='POST']) == 2
            assert f.receipts.tick.await_count == 3
    asyncio.run(run())


def test_lost_provider_binding_recovers_terminal_payment_after_23h_using_get_only(tmp_path):
    async def run():
        async with fixture(tmp_path) as f:
            f.checkout.repository.bind_provider_id = AsyncMock(side_effect=OSError('DB bind failed'))
            with pytest.raises(OSError): await f.checkout.create_for_user(501, 'pilot_month_199')
            provider = next(iter(f.fake.rows))
            f.fake.rows[provider].update(status='succeeded', paid=True, captured_at=utcnow().isoformat(), confirmation={})
            async with f.sessions.begin() as session:
                row = await session.scalar(select(Payment)); row.created_at = utcnow()-timedelta(days=2)
                original_key = row.idempotency_key
            f.fake.calls.clear()
            await f.worker().tick()
            assert all(c[0]=='GET' for c in f.fake.calls)
            assert (await f.snapshot())[3] == 1
            async with f.sessions() as session:
                row = await session.scalar(select(Payment))
                assert row.provider_payment_id == provider and row.idempotency_key == original_key
    asyncio.run(run())


def test_cursor_and_jobs_survive_page_failure_and_restart(tmp_path):
    async def run():
        async with fixture(tmp_path) as f:
            await f.order(); await f.order('pilot_year_1790')
            f.faults.add(('payments','1'))
            await f.worker().tick(pages=1)
            assert (await f.snapshot())[3] == 1
            bounds = f.listing_calls[0][1:3]
            f.now[0] += timedelta(hours=1)
            result = await f.worker().tick()
            assert result['discovery_errors'] == 1 and (await f.snapshot())[3] == 1
            f.faults.clear()
            await f.worker().tick()
            assert (await f.snapshot())[3] == 2
            resumed = [c for c in f.listing_calls if c[0]=='payments' and c[3]=='1']
            assert resumed and all(c[1:3] == bounds for c in resumed)
    asyncio.run(run())


def test_api_timeout_retries_persist_and_receipt_hook_recovers_without_invoice(tmp_path):
    async def run():
        async with fixture(tmp_path) as f:
            await f.order()
            original = f.client.fetch
            f.client.fetch = AsyncMock(side_effect=TimeoutError('must not be logged'))
            await f.worker().tick()
            assert (await f.snapshot())[3] == 0
            with closing(f.journal.connect()) as db:
                assert db.execute('SELECT state,outcome FROM inbox').fetchone() == ('retry','temporarily_unavailable')
            f.client.fetch = original; f.faults.update({'payments','refunds'})
            f.now[0] += timedelta(seconds=31)
            result = await f.worker().tick()
            assert result['discovery_errors']==2 and (await f.snapshot())[3] == 1
            assert len([c for c in f.fake.calls if c[0]=='POST']) == 1
    asyncio.run(run())


@pytest.mark.parametrize('status', ['pending', 'canceled'])
def test_nonfinal_or_canceled_payment_never_grants(tmp_path, status):
    async def run():
        async with fixture(tmp_path) as f:
            provider = await f.order(status=status)
            await f.worker().tick()
            assert (await f.snapshot())[3] == 0
            if status == 'pending':
                # Object created long ago can settle after its scan window.
                f.fake.rows[provider].update(status='succeeded', paid=True, captured_at=utcnow().isoformat())
                f.now[0] += timedelta(days=2)
                f.faults.update({'payments','refunds'})
                await f.worker().tick()
                assert (await f.snapshot())[3] == 1
    asyncio.run(run())


@pytest.mark.parametrize('amount', ['199.00', '10.00'])
def test_refund_discovered_without_webhook_is_idempotent_or_reviewed(tmp_path, amount):
    async def run():
        async with fixture(tmp_path) as f:
            provider = await f.order(); await f.worker().tick(); before = await f.snapshot()
            f.fake.rows['refund-fixture'] = {'id':'refund-fixture', 'payment_id':provider,
                'status':'succeeded', 'amount':{'value':amount,'currency':'RUB'}}
            await f.worker().tick(); after = await f.snapshot()
            if amount == '10.00':
                assert after == before
            else:
                assert after[2]=='expired' and after[3]==before[3]
            f.now[0] += timedelta(minutes=2)
            await f.worker().tick()
            assert await f.snapshot() == after
            with closing(f.journal.connect()) as db:
                assert db.execute("SELECT state FROM inbox WHERE kind='refunds'").fetchone()[0] == ('review' if amount=='10.00' else 'done')
    asyncio.run(run())


def test_forged_amount_is_reviewed_not_bound_or_granted(tmp_path):
    async def run():
        async with fixture(tmp_path) as f:
            await f.order(); provider = next(iter(f.fake.rows))
            f.fake.rows[provider]['amount']['value']='0.01'
            await f.worker().tick()
            assert (await f.snapshot())[3] == 0
            with closing(f.journal.connect()) as db:
                assert db.execute('SELECT state,outcome FROM inbox').fetchone() == ('review','verification_failed')
    asyncio.run(run())


def test_webhook_ack_is_durable_untrusted_body_cannot_grant_and_duplicates_are_safe(tmp_path):
    async def run():
        async with fixture(tmp_path) as f:
            provider = await f.order(status='pending'); worker = f.worker()
            app = web.Application()
            install_test_payment_webhook(app, f.processor, refund_processor=f.refunds, inbox=worker)
            async with TestClient(TestServer(app)) as http:
                for key in (provider, provider, 'unknown-payment'):
                    response = await http.post('/yookassa/test-webhook', json={
                        'type':'notification','event':'payment.succeeded',
                        'object':{'id':key,'paid':True,'amount':{'value':'999999'}}})
                    assert response.status == 200
                assert (await f.snapshot())[3] == 0
                with closing(f.journal.connect()) as db:
                    assert db.execute('SELECT count(*) FROM inbox').fetchone()[0] == 1
                await worker.tick()
                assert (await f.snapshot())[3] == 0
                f.fake.rows[provider].update(status='succeeded',paid=True,captured_at=utcnow().isoformat())
                f.now[0] += timedelta(seconds=31)
                await f.worker().tick(); before = await f.snapshot()
                assert before[3] == 1
                await http.post('/yookassa/test-webhook',json={'type':'notification','event':'payment.succeeded','object':{'id':provider}})
                await f.worker().tick()
                assert await f.snapshot() == before
    asyncio.run(run())


def test_journal_permissions_and_symlinks_are_rejected(tmp_path):
    path = tmp_path/'sync.sqlite'
    ReconciliationJournal(path)
    assert path.stat().st_mode & 0o777 == 0o600
    path.chmod(0o644)
    with pytest.raises(ValueError): ReconciliationJournal(path)
    link=tmp_path/'link'; link.symlink_to(path)
    with pytest.raises(OSError): ReconciliationJournal(link)


def test_refund_before_delayed_payment_retry_does_not_leave_refunded_access(tmp_path):
    async def run():
        async with fixture(tmp_path) as f:
            provider = await f.order(status='pending')
            await f.worker().tick()
            f.fake.rows[provider].update(status='succeeded',paid=True,captured_at=utcnow().isoformat())
            f.fake.rows['early-refund'] = {'id':'early-refund','payment_id':provider,
                'status':'succeeded','amount':{'value':'199.00','currency':'RUB'}}
            # Parent inbox retry is still in backoff; refund is due now.
            await f.worker().tick()
            assert (await f.snapshot())[2:] == ('expired', 1)
            f.now[0] += timedelta(seconds=31)
            await f.worker().tick()
            assert (await f.snapshot())[2:] == ('expired', 1)
    asyncio.run(run())


def test_separate_worker_lease_is_nonblocking_and_never_locks_sqlite(tmp_path):
    import fcntl
    async def run():
        async with fixture(tmp_path) as f:
            await f.order()
            with f.journal.lease.open('rb') as lease:
                fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
                assert (await f.worker().tick())['busy'] is True
                # Database access remains possible while the runner is leased.
                with closing(f.journal.connect()) as db:
                    assert db.execute('SELECT count(*) FROM sync').fetchone()[0] == 0
            await f.worker().tick()
            assert (await f.snapshot())[3] == 1
    asyncio.run(run())


def test_multi_page_cursor_cycle_never_advances_checkpoint(tmp_path):
    async def run():
        async with fixture(tmp_path) as f:
            provider = await f.order()
            async def cycle(kind, start, end, cursor):
                if kind=='refunds':return [],None
                return [deepcopy(f.fake.rows[provider])], 'A' if cursor!='A' else 'B'
            f.client.list_page=cycle
            result=await f.worker().tick(pages=3)
            assert result['discovery_errors']==1
            with closing(f.journal.connect()) as db:
                assert db.execute("SELECT cursor,lower_at FROM sync WHERE kind='payments'").fetchone()[0]=='B'
            assert (await f.snapshot())[3] == 1
    asyncio.run(run())
