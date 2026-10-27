"""Canonical test payments behind owner OIDC and a local peer-UID boundary."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select, func

from bot.db.models import Payment, Subscription, SubscriptionHistory
from bot.services import PILOT_TARIFFS
from bot.services.payment_events import add_months
from bot.services.payment_notifications import PaymentNotificationProcessor
from bot.services.payment_polling import TestPaymentPoller as Poller
from bot.services.payments import PaymentCheckoutService
from bot.services.subscriptions import as_utc, utcnow
from bot.services.website_payments import CanonicalPaymentCheckout, PaymentReview, LocalPaymentClient
from bot.services.yookassa_api import PaymentProviderUnavailable
from bot.subscription_api.payment_bridge import create_bridge, peer_uid, validate_configuration
from tests.test_yookassa_test_mode import setup

from tests.test_website import key

ORIGIN = 'https://r1276248.s2578.vm.mydomain.zone:20443'


def test_bridge_peer_permission_and_strict_request_shape(tmp_path):
    async def run():
        service=SimpleNamespace(create=AsyncMock(return_value={'state':'pending'}),
                                latest=AsyncMock(return_value=None))
        for peer in (123,124):
            async with TestClient(TestServer(create_bridge(service,501,123,peer=lambda r:peer))) as http:
                for payload in ({'action':'create','tariff_id':'pilot_month_199','owner_id':502},
                                {'action':'create','tariff_id':'month'}, {'action':'unknown'}):
                    assert (await http.post('/rpc',json=payload)).status == (400 if peer==123 else 403)
                reply=await http.post('/rpc',json={'action':'create','tariff_id':'pilot_month_199'})
                assert reply.status == (200 if peer==123 else 403)
                if peer==123:
                    assert (await (await http.post('/rpc',json={'action':'latest'})).json())=={'order':None}
        service.create.assert_awaited_once_with(501,'pilot_month_199')
        with pytest.raises(PermissionError):peer_uid(SimpleNamespace(transport=None))
    asyncio.run(run())


@pytest.mark.parametrize('plan',list(PILOT_TARIFFS))
def test_canonical_bridge_payment_duplicate_renewal_and_owner_isolation(tmp_path,plan):
    async def run():
        engine,db,fake,client,_=await setup(tmp_path)
        try:
            checkout=PaymentCheckoutService(db,client,ORIGIN+'/account',tariffs=PILOT_TARIFFS)
            processor=PaymentNotificationProcessor(db,client)
            service=CanonicalPaymentCheckout(db,checkout,processor,501)
            for purchase in range(2):
                old=None
                async with db() as session:
                    sub=await session.scalar(select(Subscription))
                    if sub:old=(sub.id,as_utc(sub.expires_at))
                order=await service.create(501,plan)
                assert order['amount_kopecks']==PILOT_TARIFFS[plan].price_rub*100
                assert (await service.create(501,plan))['id']==order['id']
                async with db() as session:
                    row=await session.get(Payment,UUID(order['id']))
                    provider=row.provider_payment_id
                with pytest.raises(LookupError):await service.state(502,order['id'])
                # Forged browser success cannot fulfill anything.
                assert (await service.state(501,order['id']))['state']=='pending'
                fake.rows[provider].update(status='succeeded',paid=True,captured_at=utcnow().isoformat())
                for _ in range(2):
                    await processor.process(provider)
                    assert (await service.refresh(501,order['id']))['state']=='succeeded'
                async with db() as session:
                    sub=await session.scalar(select(Subscription))
                    if old:
                        assert sub.id==old[0]
                        assert as_utc(sub.expires_at)==add_months(old[1],PILOT_TARIFFS[plan].months)
                    assert await session.scalar(select(func.count()).select_from(Subscription))==1
                    assert await session.scalar(select(func.count()).select_from(SubscriptionHistory).where(SubscriptionHistory.reason=='payment'))==purchase+1
                state=await service.state(501,order['id'])
                assert state['access_pending'] and 'connect_url' not in state
                if purchase == 0:
                    async with db.begin() as session:
                        saved = await session.get(Payment, UUID(order['id']))
                        saved.created_at = utcnow()-timedelta(minutes=1)
            # A new service/worker reads durable orders; no browser needed.
            assert (await CanonicalPaymentCheckout(db,checkout,processor,501).latest(501))['id']==order['id']
            assert fake.calls[-1][0]=='GET'
        finally:await engine.dispose()
    asyncio.run(run())


def test_return_from_provider_has_no_fulfillment_and_source_failure_is_closed(tmp_path):
    async def run():
        snapshot=SimpleNamespace(read=lambda owner:{'account':{'feed_url':'private','expires_at':'future'}} if owner==501 else (_ for _ in ()).throw(PermissionError()))
        payments=SimpleNamespace(request=AsyncMock(return_value={'id':'order','state':'succeeded','subscription_expires_at':'future','access_pending':True}))
        service=PaymentReview(snapshot,ORIGIN,payments)
        result=await service.create(501,'pilot_month_199')
        assert result['connect_url']==ORIGIN+'/connect' and result['access_pending'] is False
        assert 'private' not in str(result)
        with pytest.raises(PermissionError):await service.create(502,'pilot_month_199')
        payments.request.assert_awaited_once()
        snapshot.read=lambda owner:{'account':{'feed_url':'private','expires_at':'old'}}
        payments.request.return_value={'state':'succeeded','subscription_expires_at':'future','access_pending':True}
        result=await service.create(501,'pilot_month_199')
        assert result['access_pending'] and 'connect_url' not in result
        # Bridge unavailable never falls back to a fabricated purchase.
        payments.request.side_effect=PaymentProviderUnavailable()
        with pytest.raises(PaymentProviderUnavailable):await service.create(501,'pilot_month_199')
        with pytest.raises(PaymentProviderUnavailable):await LocalPaymentClient('/not/a/socket').request('latest')
    asyncio.run(run())


def test_unbound_order_keeps_original_key_after_database_bind_failure(tmp_path):
    async def run():
        engine,db,fake,client,_=await setup(tmp_path)
        try:
            checkout=PaymentCheckoutService(db,client,ORIGIN+'/account',tariffs=PILOT_TARIFFS)
            original=checkout.repository.bind_provider_id
            checkout.repository.bind_provider_id=AsyncMock(side_effect=OSError('simulated DB outage'))
            with pytest.raises(OSError):await checkout.create_for_user(501,'pilot_month_199')
            async with db.begin() as session:
                row=await session.scalar(select(Payment))
                row.created_at=utcnow()-timedelta(minutes=45)
                key=row.idempotency_key
            checkout.repository.bind_provider_id=original
            await checkout.create_for_user(501,'pilot_month_199')
            assert [call[3] for call in fake.calls if call[0]=='POST']==[key,key]
            async with db() as session:
                assert await session.scalar(select(func.count()).select_from(Payment))==1
                assert (await session.scalar(select(Payment))).provider_payment_id
                assert await session.scalar(select(func.count()).select_from(Subscription))==0
        finally:await engine.dispose()
    asyncio.run(run())


def test_uncertain_order_after_provider_idempotency_window_requires_review(tmp_path):
    async def run():
        engine,db,fake,client,_=await setup(tmp_path)
        try:
            checkout=PaymentCheckoutService(db,client,ORIGIN+'/account',tariffs=PILOT_TARIFFS)
            row=await checkout.repository.create_or_reuse_pending(501,PILOT_TARIFFS['pilot_month_199'])
            async with db.begin() as session:
                saved=await session.get(Payment,row.id)
                saved.created_at=utcnow()-timedelta(hours=25)
            with pytest.raises(ValueError,match='сверки'):await checkout.create_for_user(501,'pilot_month_199')
            assert not fake.calls
            async with db() as session:
                assert await session.scalar(select(func.count()).select_from(Payment))==1
        finally:await engine.dispose()
    asyncio.run(run())


def test_bridge_rejects_live_secret_source_database_and_extra_credentials():
    valid={'owner_id':501,'database_url':'postgresql+psycopg://kudavpn_pilot:offline@127.0.0.1/kudavpn_telegram_pilot',
           'shop_id':'1503208','secret_key':'test_offline'}
    assert validate_configuration(valid)==valid
    for delta in ({'secret_key':'live_offline'},{'shop_id':'1503209'},{'owner_id':'501'},
                  {'database_url':'postgresql+psycopg://root:offline@127.0.0.1/production'}, {'BOT_TOKEN':'offline'}):
        with pytest.raises(ValueError):validate_configuration({**valid,**delta})


def test_pending_payment_resume_uses_get_and_db_outage_polling_recovers(tmp_path):
    async def run():
        engine,db,fake,client,_=await setup(tmp_path)
        try:
            checkout=PaymentCheckoutService(db,client,ORIGIN+'/account',tariffs=PILOT_TARIFFS)
            service=CanonicalPaymentCheckout(db,checkout,PaymentNotificationProcessor(db,client),501)
            order=await service.create(501,'pilot_month_199')
            posts=len([c for c in fake.calls if c[0]=='POST'])
            restarted=CanonicalPaymentCheckout(db,checkout,PaymentNotificationProcessor(db,client),501)
            resumed=await restarted.latest(501)
            assert resumed['id']==order['id'] and resumed['payment_url'].startswith('https://yoomoney.ru/')
            assert len([c for c in fake.calls if c[0]=='POST'])==posts
            async with db() as session:row=await session.get(Payment,UUID(order['id']))
            fake.rows[row.provider_payment_id].update(status='succeeded',paid=True,captured_at=utcnow().isoformat())
            poller=Poller(db,client,jitter=lambda:0,clock=lambda:1000)
            actual=poller.processor.process
            poller.processor.process=AsyncMock(side_effect=OSError('simulated DB failure'))
            await poller.tick()
            async with db() as session:assert await session.scalar(select(func.count()).select_from(Subscription))==0
            # A restarted existing poller recovers persistent bound orders even
            # when the browser has closed; no second POST or notification needed.
            await Poller(db,client,jitter=lambda:0).tick()
            await actual(row.provider_payment_id)
            async with db() as session:
                assert (await session.get(Payment,row.id)).status=='succeeded'
                assert await session.scalar(select(func.count()).select_from(SubscriptionHistory).where(SubscriptionHistory.reason=='payment'))==1
            assert len([c for c in fake.calls if c[0]=='POST'])==posts
        finally:await engine.dispose()
    asyncio.run(run())


def test_linux_unix_socket_rejects_nonwebsite_peer(tmp_path):
    import os, socket
    if not hasattr(socket,'SO_PEERCRED'):
        pytest.skip('Linux SO_PEERCRED is exercised by GitHub CI and deployment preflight')
    async def run():
        from aiohttp import web
        path=tmp_path/'bridge.sock'
        service=SimpleNamespace(latest=AsyncMock(return_value=None))
        runner=web.AppRunner(create_bridge(service,501,os.getuid()+1),access_log=None)
        await runner.setup()
        try:
            await web.UnixSite(runner,str(path)).start()
            with pytest.raises(PaymentProviderUnavailable):
                await LocalPaymentClient(str(path),validate_socket=False).request('latest')
            service.latest.assert_not_awaited()
        finally:await runner.cleanup()
    asyncio.run(run())


def test_oidc_review_uses_canonical_orders_without_copying_users(tmp_path,key):
    from bot.db.models import Base, ReviewSession, User
    from bot.services.website_auth import TelegramOIDC
    from bot.services.website_review import ReviewAuth
    from bot.subscription_api.website import create_website, DEMO_COOKIE
    from tests.test_website import IdentityProvider
    from tests.test_website_review import fixture_data, write_snapshot
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from urllib.parse import parse_qs, urlsplit
    async def run():
        canonical,db,fake,provider,_=await setup(tmp_path)
        auth_engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'auth-only.db'))
        try:
            async with auth_engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
            auth_db=async_sessionmaker(auth_engine,expire_on_commit=False)
            identity=IdentityProvider(key)
            auth=ReviewAuth(auth_db,TelegramOIDC('123456','offline',ORIGIN+'/auth/callback',transport=identity),allowed_telegram_ids={501})
            checkout=PaymentCheckoutService(db,provider,ORIGIN+'/account',tariffs=PILOT_TARIFFS)
            canonical_service=CanonicalPaymentCheckout(db,checkout,PaymentNotificationProcessor(db,provider),501)
            class RPC:
                async def request(self,action,**args):
                    if action=='latest':return {'order':await canonical_service.latest(501)}
                    return await getattr(canonical_service,action)(501,args.get('tariff_id') or args['order_id'])
            reader=write_snapshot(tmp_path/'canonical.json',fixture_data())
            service=PaymentReview(reader,ORIGIN,RPC())
            app=create_website(service,auth,service,'http://127.0.0.1:18106',local_demo=True,closed_review=True)
            async with TestClient(TestServer(app)) as http:
                assert (await http.post('/api/orders',json={'tariff_id':'pilot_month_199'})).status==401
                target,binder=await auth.begin()
                token=await auth.finish(parse_qs(urlsplit(target).query)['state'][0],binder,'501')
                headers={'Cookie':DEMO_COOKIE+'='+token,'Origin':'http://127.0.0.1:18106','X-CSRF-Token':auth.csrf(token)}
                assert (await http.post('/api/orders',headers=headers,json={'tariff_id':'pilot_month_199','owner_id':502})).status==400
                reply=await http.post('/api/orders',headers=headers,json={'tariff_id':'pilot_month_199'})
                assert reply.status==200
                order=await reply.json()
                async with db() as session:row=await session.get(Payment,UUID(order['id']))
                fake.rows[row.provider_payment_id].update(status='succeeded',paid=True,captured_at=utcnow().isoformat())
                for _ in range(2):
                    reply=await http.post('/api/orders/'+order['id']+'/refresh',headers=headers,json={})
                    assert reply.status==200 and (await reply.json())['state']=='succeeded'
                async with db() as session:
                    assert await session.scalar(select(func.count()).select_from(SubscriptionHistory).where(SubscriptionHistory.reason=='payment'))==1
                assert (await http.get('/account?payment_success=true',headers=headers)).status==200
                async with auth_db() as session:
                    assert await session.scalar(select(func.count()).select_from(ReviewSession))==1
                    for model in (User,Payment,Subscription):
                        assert await session.scalar(select(func.count()).select_from(model))==0
        finally:
            await canonical.dispose();await auth_engine.dispose()
    asyncio.run(run())


def test_deployment_has_narrow_rollback_and_no_schema_or_vpn_operations():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    installer=(root/'deploy/website-review/enable_payments.py').read_text()
    unit=(root/'deploy/website-review/kudavpn-site-payments.service').read_text()
    assert 'pg_dump' in installer and "dump('kudavpn_telegram_pilot')" in installer
    assert 'rows_digest' in installer and "baseline('verify')" in installer
    assert 'alembic upgrade' not in installer and 'pg_restore -d' not in installer
    assert 'User=kudavpn_site_payments' in unit and 'LoadCredential=payments.json:' in unit
    assert 'RuntimeDirectoryMode=0750' in unit and 'ProtectSystem=strict' in unit
    assert "'systemctl','restart',SITE" in installer
    for protected in ('xray.service','kudavpn-telegram-pilot-bot.service','kudavpn-monitor.service'):
        assert protected not in installer
