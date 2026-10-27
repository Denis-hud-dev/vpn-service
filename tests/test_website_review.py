"""Closed review never creates/changes canonical users, access or payments."""
import asyncio
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import time
from urllib.parse import parse_qs,urlsplit

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient,TestServer
import pytest
from sqlalchemy import event,func,select
from sqlalchemy.ext.asyncio import async_sessionmaker,create_async_engine

from bot.db.models import Base,Payment,ReviewSession,Subscription,SubscriptionToken,User,VPNNode
from bot.services.happ import HappService,NodeReconciler
from bot.services.node_health import NodeHealthMonitor
from bot.services.subscriptions import SubscriptionService,utcnow
from bot.services.website_auth import TelegramOIDC
from bot.services.website_export import collect
from bot.services.website_review import CanonicalDataUnavailable,ReadOnlyReview,ReviewAuth,ReviewSnapshot
from bot.subscription_api.website import DEMO_COOKIE,create_website
from tests.test_happ import FakeNode
from tests.test_subscription_lifecycle import TestVerifier
from tests.test_website import IdentityProvider,key
from tests.test_yookassa_test_mode import setup
from bot.services.payments import PaymentRepository
from bot.services.payment_events import ConfirmedPayment
from bot.services import TARIFFS
from uuid import uuid4

ORIGIN='http://127.0.0.1:18106'
FEED='https://feed.example:18443'


def fixture_data():
    return {'version':1,'owner_id':501,'owner_known':True,'timestamp':time.time(),
            'account':{'display_name':'Owner','status':'active','tariff':'1 месяц — 199 ₽',
                       'expires_at':(utcnow()+timedelta(days=25)).isoformat(),'nodes':['Test node'],
                       'feed_url':FEED+'/s/'+'a'*43},
            'payments':[{'tariff':'1 месяц — 199 ₽','amount_kopecks':19900,'currency':'RUB',
                         'status':'succeeded','created_at':utcnow().isoformat(),
                         'test':True,'receipt':'test_not_required','receipt_error':None}]}


def write_snapshot(path,data):
    path.write_text(json.dumps(data));path.chmod(0o600)
    return ReviewSnapshot(path,501,FEED,root_owned=False)


@pytest.mark.parametrize('fault',['stale','future','foreign_owner','unknown_owner','wrong_feed','bad_token',
                                  'wrong_amount','live_payment','extra_payment_data','bad_mode','missing'])
def test_snapshot_fails_closed(tmp_path,fault):
    data=fixture_data(); path=tmp_path/'private.json'
    if fault=='stale':data['timestamp']-=121
    if fault=='future':data['timestamp']+=60
    if fault=='foreign_owner':data['owner_id']=502
    if fault=='unknown_owner':data['owner_known']=False
    if fault=='wrong_feed':data['account']['feed_url']='https://other.example/s/'+'a'*43
    if fault=='bad_token':data['account']['feed_url']=FEED+'/s/short'
    if fault=='wrong_amount':data['payments'][0]['amount_kopecks']=True
    if fault=='live_payment':data['payments'][0]['test']=False
    if fault=='extra_payment_data':data['payments'][0]['provider_key']='forbidden'
    reader=write_snapshot(path,data)
    if fault=='bad_mode':path.chmod(0o644)
    if fault=='missing':path.unlink()
    with pytest.raises(CanonicalDataUnavailable,match='Canonical data temporarily unavailable'):
        reader.read(501)
    with pytest.raises(PermissionError):reader.read(502)


def test_expiry_and_unavailable_nodes_remove_feed(tmp_path):
    data=fixture_data(); data['account']['expires_at']=(utcnow()-timedelta(seconds=1)).isoformat()
    reader=write_snapshot(tmp_path/'a.json',data)
    assert reader.read(501)['account']['status']=='expired'
    assert 'feed_url' not in reader.read(501)['account']
    data=fixture_data();data['account']['nodes']=[]
    assert 'feed_url' not in write_snapshot(tmp_path/'a.json',data).read(501)['account']


def test_review_login_has_no_canonical_user_or_subscription_writes(tmp_path,key):
    async def run():
        engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'auth.db'))
        try:
            async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
            sessions=async_sessionmaker(engine,expire_on_commit=False)
            provider=IdentityProvider(key)
            oidc=TelegramOIDC('123456','offline','https://review.example/auth/callback',transport=provider)
            auth=ReviewAuth(sessions,oidc,allowed_telegram_ids={501})
            async def login(owner):
                destination,browser=await auth.begin()
                return await auth.finish(parse_qs(urlsplit(destination).query)['state'][0],browser,str(owner))
            with pytest.raises(PermissionError):await login(502)
            token=await login(501)
            assert await auth.owner(token)==501
            async with sessions() as session:
                assert await session.scalar(select(func.count()).select_from(ReviewSession))==1
                for model in (User,Subscription,SubscriptionToken,Payment):
                    assert await session.scalar(select(func.count()).select_from(model))==0
            await auth.logout(token)
            with pytest.raises(PermissionError):await auth.owner(token)
        finally:await engine.dispose()
    asyncio.run(run())


def test_closed_dashboard_existing_link_selection_and_logout(tmp_path,key):
    async def run():
        engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'auth.db'))
        try:
            async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
            sessions=async_sessionmaker(engine,expire_on_commit=False)
            auth=ReviewAuth(sessions,None,allowed_telegram_ids={501})
            path=tmp_path/'snapshot.json';before=fixture_data()
            reader=write_snapshot(path,before);service=ReadOnlyReview(reader,ORIGIN)
            app=create_website(service,auth,service,ORIGIN,local_demo=True,closed_review=True)
            async with TestClient(TestServer(app),cookie_jar=CookieJar(unsafe=True)) as http:
                assert (await http.get('/')).status==401
                for route in ('/api/plans','/api/account','/api/payments','/connect','/docs/terms','/__demo__/login'):
                    assert (await http.get(route)).status==401
                token=await auth.new_session(501)
                headers={'Cookie':DEMO_COOKIE+'='+token,'Origin':ORIGIN,'X-CSRF-Token':auth.csrf(token)}
                plans=await(await http.get('/api/plans',headers=headers)).json()
                assert plans['read_only'] and plans['methods']==[]
                assert [p['amount_kopecks'] for p in plans['plans']]==[19900,49900,179000]
                account=await(await http.get('/api/account',headers=headers)).json()
                assert account['display_name']=='Owner' and account['connect_url']==ORIGIN+'/connect'
                assert 'feed_url' not in account
                html=await(await http.get('/connect',headers=headers)).text()
                assert before['account']['feed_url'] in html and '<svg' in html
                assert (await http.post('/api/orders',headers={'Cookie':DEMO_COOKIE+'='+token},json={'tariff_id':'pilot_month_199'})).status==403
                for _ in range(2):
                    selection=await(await http.post('/api/orders',headers=headers,json={'tariff_id':'pilot_month_199'})).json()
                    assert selection['state']=='selection_only' and 'payment_url' not in selection
                assert json.loads(path.read_text())==before
                async with sessions() as session:
                    for model in (User,Subscription,SubscriptionToken,Payment):
                        assert await session.scalar(select(func.count()).select_from(model))==0
                before['timestamp']-=121;write_snapshot(path,before)
                response=await http.get('/connect',headers=headers)
                assert response.status==503 and 'feed.example' not in await response.text()
                assert (await http.post('/api/logout',headers=headers,json={})).status==200
                assert (await http.get('/connect',headers=headers)).status==401
        finally:await engine.dispose()
    asyncio.run(run())


def test_export_reuses_existing_feed_with_only_select_queries(tmp_path):
    async def run():
        engine,sessions,_,_,_=await setup(tmp_path)
        try:
            lifecycle=SubscriptionService(sessions,TestVerifier())
            await lifecycle.create_pending(501,'month')
            payment=await PaymentRepository(sessions).create_pending(501,TARIFFS['month'],uuid4())
            await lifecycle.apply_confirmed_payment(ConfirmedPayment(payment.id,'yookassa',str(uuid4()),29900,'RUB',utcnow()))
            async with sessions.begin() as session:
                session.add(VPNNode(name='Test node',hostname='vpn.example',port=10443,public_key='a'*43,
                                    short_id='1234',server_name='www.cloudflare.com',enabled=True))
                sub=await session.scalar(select(Subscription))
            gateway=FakeNode()
            import base64
            happ=HappService(sessions,FEED,link_key=base64.urlsafe_b64encode(b'x'*32).rstrip(b'=').decode(),trial_owner_id=501,trial_node_port=10443)
            await NodeReconciler(sessions,gateway).reconcile(sub.id)
            await NodeHealthMonitor(sessions,gateway).tick()
            original=await happ.issue_for_user(501)
            def select_only(conn,cursor,statement,parameters,context,executemany):
                assert statement.lstrip().upper().startswith('SELECT'), 'Exporter attempted a write'
            event.listen(engine.sync_engine,'before_cursor_execute',select_only)
            try:
                async with sessions() as session:
                    data=await collect(session,happ,501)
                    other=await collect(session,happ,502)
                assert data['account']['feed_url']==original
                assert data['account']['nodes']==['Test node']
                assert len(data['payments'])==1
                assert other['account']['status']=='inactive' and other['payments']==[]
                assert 'feed_url' not in other['account']
            finally:event.remove(engine.sync_engine,'before_cursor_execute',select_only)
        finally:await engine.dispose()
    asyncio.run(run())


def test_review_settings_refuse_shared_ports_credentials_and_database(monkeypatch):
    from bot.subscription_api.website_main import settings,ORIGIN as HTTPS
    monkeypatch.setenv('KUDAVPN_SITE_MODE','review-test')
    monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://kudavpn_website_test:fixture@127.0.0.1/kudavpn_website_test')
    monkeypatch.setenv('SITE_PUBLIC_ORIGIN',HTTPS)
    for name in ('SITE_TEST_SECRET_FILE','SITE_TEST_SHOP_FILE','SITE_HAPP_SECRET_FILE','HAPP_LINK_SECRET','BOT_TOKEN','YOOKASSA_SECRET_KEY'):
        monkeypatch.delenv(name,raising=False)
    assert settings()[2:]==(18106,'127.0.0.1')
    monkeypatch.setenv('SITE_APPROVED_PUBLIC_REVIEW','1');monkeypatch.setenv('SITE_PORT','20443')
    assert settings()[2:]==(20443,'0.0.0.0')
    monkeypatch.setenv('SITE_PORT','19443')
    with pytest.raises(ValueError):settings()
    monkeypatch.setenv('SITE_PORT','20443');monkeypatch.setenv('BOT_TOKEN','forbidden')
    with pytest.raises(ValueError):settings()
    monkeypatch.delenv('BOT_TOKEN');monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://kudavpn_pilot:fixture@127.0.0.1/kudavpn_telegram_pilot')
    with pytest.raises(ValueError):settings()


def test_review_peer_auth_requires_exact_socket_and_unprivileged_role(monkeypatch):
    from bot.subscription_api.website_main import settings,ORIGIN as HTTPS
    monkeypatch.setenv('KUDAVPN_SITE_MODE','review-test')
    monkeypatch.setenv('SITE_PUBLIC_ORIGIN',HTTPS)
    monkeypatch.setenv('SITE_PORT','18106')
    monkeypatch.delenv('SITE_APPROVED_PUBLIC_REVIEW',raising=False)
    for suffix in ('?host=/tmp','?host=/var/run/postgresql&service=other',''):
        monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://kudavpn_website_test@/kudavpn_website_test'+suffix)
        with pytest.raises(ValueError):settings()
    monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://kudavpn_website_test@/kudavpn_website_test?host=/var/run/postgresql')
    assert settings()[0].password is None
    monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://postgres@/kudavpn_website_test?host=/var/run/postgresql')
    with pytest.raises(ValueError):settings()
