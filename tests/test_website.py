"""Isolated website accounts; all identities, keys, payments and VPN are fixtures."""
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import hashlib
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock
from uuid import UUID

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import pytest
from sqlalchemy import func, select

from bot.db.models import NPDReceipt, NPDReceiptEvent, Payment, Subscription, SubscriptionHistory, User, WebLoginFlow, WebSession
from bot.services import PILOT_TARIFFS
from bot.services.payment_events import add_months
from bot.services.payment_notifications import PaymentNotificationProcessor
from bot.services.payments import PaymentCheckoutService
from bot.services.subscriptions import as_utc, utcnow
from bot.services.website import NPDLedger, WebsiteCheckout
from bot.services.website_auth import ISSUER, TelegramOIDC, WebsiteAuth, digest
from bot.subscription_api.website import COOKIE, DEMO_COOKIE, DEMO_FLOW, FLOW_COOKIE, create_website
from tests.test_yookassa_test_mode import setup

ORIGIN = 'http://127.0.0.1:18106'


@pytest.fixture(scope='module')
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class IdentityProvider:
    def __init__(self, key):
        self.key, self.calls = key, []
        self.claims = {}

    def token(self, user=501, **changes):
        now = int(time.time())
        claims = {'iss': ISSUER, 'aud': '123456', 'sub': 'subject-'+str(user),
                  'id': user, 'iat': now, 'exp': now+3600}
        claims.update(changes)
        return jwt.encode(claims, self.key, algorithm='RS256', headers={'kid':'fixture'})

    async def __call__(self, method, path, data):
        self.calls.append((method, path, data))
        if method == 'GET':
            public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
            return {'keys': [{**public, 'kid':'fixture', 'alg':'RS256', 'use':'sig'}]}
        return {'id_token': self.claims.get(data['code'], self.token(int(data['code'])))}


@asynccontextmanager
async def site(tmp_path, key, *, secure=False, closed=False):
    engine, db, fake, provider, _ = await setup(tmp_path)
    identity = IdentityProvider(key)
    oidc = TelegramOIDC('123456', 'offline-client-secret', 'https://site.example/auth/callback', transport=identity)
    auth = WebsiteAuth(db, oidc, allowed_telegram_ids={501} if closed else None)
    checkout = PaymentCheckoutService(db, provider, 'https://site.example/account', tariffs=PILOT_TARIFFS)
    processor = PaymentNotificationProcessor(db, provider)
    happ = SimpleNamespace(available_node_names_for_user=AsyncMock(return_value=('Fixture node',)),
        issue_for_user=AsyncMock(side_effect=lambda owner: 'https://vpn.example/s/fixture-'+str(owner)),
        connection_page_url=lambda url:url.replace('/s/', '/connect/'))
    service, ledger = WebsiteCheckout(db, checkout, processor, happ, None), NPDLedger(db)
    app = create_website(service, auth, ledger, 'https://site.example' if secure else ORIGIN,
                         local_demo=not secure, closed_review=closed)
    try:
        async with TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True)) as http:
            yield SimpleNamespace(engine=engine, db=db, fake=fake, provider=provider, identity=identity,
                auth=auth, http=http, ledger=ledger, service=service, processor=processor, happ=happ)
    finally:
        await engine.dispose()


async def login(p, user=501):
    destination, browser = await p.auth.begin()
    state = parse_qs(urlsplit(destination).query)['state'][0]
    token = await p.auth.finish(state, browser, str(user))
    return {'Origin':ORIGIN, 'X-CSRF-Token':p.auth.csrf(token), 'Cookie':DEMO_COOKIE+'='+token}, token


async def create_order(p, headers, plan='pilot_month_199'):
    response = await p.http.post('/api/orders', json={'tariff_id':plan}, headers=headers)
    assert response.status == 200
    return await response.json()


async def pay(p, order):
    async with p.db() as session:
        payment = await session.get(Payment, UUID(order['id']))
    p.fake.rows[payment.provider_payment_id].update(status='succeeded', paid=True, captured_at=utcnow().isoformat())
    await p.processor.process(payment.provider_payment_id)
    return payment


@pytest.mark.parametrize('fault', ['aud','multiple_aud','iss','expired','future','missing','bad_id','bad_sub','unsigned','wrong_key'])
def test_oidc_rejects_untrusted_identity(key, fault):
    async def run():
        provider = IdentityProvider(key)
        client = TelegramOIDC('123456', 'offline', 'https://site.example/auth/callback', transport=provider)
        changes = {'aud':{'aud':'wrong'},'multiple_aud':{'aud':['123456','other']},'iss':{'iss':'https://evil.invalid'},
                   'expired':{'exp':int(time.time())-60},'future':{'iat':int(time.time())+120},
                   'missing':{'sub':None},'bad_id':{'id':True},'bad_sub':{'sub':''}}
        token = provider.token(**changes.get(fault, {}))
        if fault == 'unsigned':
            token = jwt.encode({'id':501}, '', algorithm='none', headers={'kid':'fixture'})
        if fault == 'wrong_key':
            claims = jwt.decode(token, options={'verify_signature':False})
            token = jwt.encode(claims, rsa.generate_private_key(public_exponent=65537,key_size=2048),
                               algorithm='RS256',headers={'kid':'fixture'})
        with pytest.raises(PermissionError, match='Authentication unavailable'):
            await client.validate(token)
    asyncio.run(run())


def test_oidc_state_cookie_pkce_one_use_and_preserves_existing_user(tmp_path, key):
    async def run():
        async with site(tmp_path,key) as p:
            async with p.db() as session:
                before = await session.scalar(select(User).where(User.telegram_id == 501))
                name, user_id = before.first_name, before.id
            destination,browser = await p.auth.begin()
            query = parse_qs(urlsplit(destination).query)
            state = query['state'][0]
            assert query['code_challenge_method']==['S256']
            with pytest.raises(PermissionError): await p.auth.finish(state,'x'*43,'501')
            token = await p.auth.finish(state,browser,'501')
            assert await p.auth.owner(token)==501
            verifier = next(c[2]['code_verifier'] for c in p.identity.calls if c[0]=='POST')
            import base64
            assert query['code_challenge']==[base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()]
            with pytest.raises(PermissionError): await p.auth.finish(state,browser,'501')
            async with p.db() as session:
                row = await session.get(WebSession,digest(token))
                assert row.user_id==user_id and row.digest != token
                assert (await session.get(User,user_id)).first_name==name
                assert await session.scalar(select(func.count()).select_from(WebLoginFlow))==0
            assert await WebsiteAuth(p.db,p.auth.oidc).owner(token)==501
            await p.auth.logout(token)
            with pytest.raises(PermissionError): await p.auth.owner(token)
    asyncio.run(run())


def test_expired_auth_flow_and_session_fail_closed(tmp_path,key):
    async def run():
        async with site(tmp_path,key) as p:
            destination,browser=await p.auth.begin()
            state=parse_qs(urlsplit(destination).query)['state'][0]
            p.auth.clock=lambda:utcnow()+timedelta(hours=2)
            with pytest.raises(PermissionError):await p.auth.finish(state,browser,'501')
            p.auth.clock=utcnow
            _,token=await login(p)
            p.auth.clock=lambda:utcnow()+timedelta(hours=2)
            with pytest.raises(PermissionError):await p.auth.owner(token)
    asyncio.run(run())


def test_first_verified_login_creates_account_and_identity_collision_is_rejected(tmp_path,key):
    async def run():
        async with site(tmp_path,key) as p:
            headers,_=await login(p,503)
            assert (await (await p.http.get('/api/account',headers=headers)).json())['status']=='inactive'
            await login(p,501)
            p.identity.claims['504']=p.identity.token(504,sub='subject-501')
            with pytest.raises(PermissionError):await login(p,504)
            async with p.db() as session:
                assert await session.scalar(select(User).where(User.telegram_id==504)) is None
            assert (await p.http.get('/__demo__/login')).status==404
            assert (await p.http.get('/api/session',headers={'Cookie':DEMO_COOKIE+'='+'x'*43})).status==401
    asyncio.run(run())


def test_two_accounts_cannot_read_or_buy_for_each_other(tmp_path,key):
    async def run():
        async with site(tmp_path,key) as p:
            assert (await p.http.get('/api/account?telegram_id=501')).status==401
            assert (await p.http.get('/auth/login?telegram_id=501')).status==400
            a,_=await login(p,501); b,_=await login(p,502)
            for payload in [{'tariff_id':'pilot_month_199','telegram_id':502}, {'tariff_id':'pilot_month_199','amount_kopecks':1}]:
                assert (await p.http.post('/api/orders',json=payload,headers=a)).status==400
            assert (await p.http.post('/api/orders',json={'tariff_id':'pilot_month_199'},headers={**a,'Origin':'https://evil.invalid'})).status==403
            assert (await p.http.post('/api/orders',json={'tariff_id':'pilot_month_199'},headers={**a,'X-CSRF-Token':'forged'})).status==403
            order=await create_order(p,a)
            assert (await p.http.get('/api/orders/'+order['id'],headers=b)).status==404
            assert (await p.http.post('/api/orders/'+order['id']+'/refresh',json={},headers=b)).status==404
            await pay(p,order)
            rows=(await (await p.http.get('/api/payments',headers=a)).json())['payments']
            assert len(rows)==1 and rows[0]['receipt']=='test_not_required'
            assert (await (await p.http.get('/api/payments',headers=b)).json())['payments']==[]
            account=await (await p.http.get('/api/account',headers=a)).json()
            assert account['status']=='active' and account['connect_url'].endswith('501')
            assert 'connect_url' not in await (await p.http.get('/api/account',headers=b)).json()
            assert (await p.http.get('/yookassa/test-webhook')).status==404
            assert (await p.http.post('/api/receipts',json={},headers=a)).status==404
    asyncio.run(run())


def test_purchase_renewal_duplicates_and_receipt_crash_recovery(tmp_path,key):
    async def run():
        async with site(tmp_path,key) as p:
            headers,_=await login(p)
            first=await create_order(p,headers)
            assert (await create_order(p,headers))['id']==first['id']
            payment=await pay(p,first)
            await p.processor.process(payment.provider_payment_id)
            async with p.db() as session:
                assert await session.scalar(select(func.count()).select_from(NPDReceipt))==0
                sub=await session.scalar(select(Subscription)); end=as_utc(sub.expires_at); identity=sub.id
            # A worker/restart recovers the obligation even if the user never opens history.
            for _ in range(2):await NPDLedger(p.db).tick()
            with pytest.raises(ValueError,match='environment mismatch'):
                await NPDLedger(p.db,test=False).history(501)
            second=await create_order(p,headers,'pilot_quarter_499')
            payment2=await pay(p,second)
            for _ in range(2):await p.processor.process(payment2.provider_payment_id)
            await p.ledger.tick()
            async with p.db() as session:
                sub=await session.get(Subscription,identity)
                assert as_utc(sub.expires_at)==add_months(end,3)
                assert await session.scalar(select(func.count()).select_from(SubscriptionHistory).where(SubscriptionHistory.reason=='payment'))==2
                assert await session.scalar(select(func.count()).select_from(NPDReceipt))==2
                assert await session.scalar(select(func.count()).select_from(NPDReceiptEvent))==2
            with pytest.raises(PermissionError):
                await p.ledger.record(payment.id,'issued',operation='operator_fixture_1',document_ref='fixture',operator_confirmed=True)
    asyncio.run(run())


def test_live_receipt_states_are_offline_operator_only_and_idempotent(tmp_path,key):
    async def run():
        async with site(tmp_path,key) as p:
            h,_=await login(p);order=await create_order(p,h);payment=await pay(p,order)
            # Offline ledger semantics only, not a live gateway or actual fiscal receipt.
            ledger=NPDLedger(p.db,test=False);await ledger.tick()
            with pytest.raises(PermissionError):await ledger.record(payment.id,'issued',operation='fixture_operation1',document_ref='fixture')
            await ledger.record(payment.id,'error',operation='fixture_operation1',error_code='my_tax_unavailable',operator_confirmed=True)
            for _ in range(2):await ledger.record(payment.id,'issued',operation='fixture_operation2',document_ref='private_fixture',operator_confirmed=True)
            with pytest.raises(ValueError):await ledger.record(payment.id,'issued',operation='fixture_operation2',document_ref='other',operator_confirmed=True)
            await ledger.record(payment.id,'delivered',operation='fixture_delivery1',operator_confirmed=True)
            async with p.db.begin() as session:
                row=await session.get(Payment,payment.id);row.status='refunded'
            await ledger.tick()
            await ledger.record(payment.id,'error',operation='fixture_operation3',error_code='operator_review',operator_confirmed=True)
            await ledger.tick()
            await ledger.record(payment.id,'voided',operation='fixture_operation4',operator_confirmed=True)
            rows=await ledger.history(501)
            assert rows[0]['receipt']=='voided' and 'document_ref' not in rows[0]
    asyncio.run(run())


def test_secure_cookie_callback_and_no_secret_headers(tmp_path,key):
    async def run():
        async with site(tmp_path,key,secure=True) as p:
            response=await p.http.get('/auth/login',allow_redirects=False)
            binder=response.cookies[FLOW_COOKIE].value
            state=parse_qs(urlsplit(response.headers['Location']).query)['state'][0]
            response=await p.http.get('/auth/callback?state='+state+'&code=501',
                headers={'Cookie':FLOW_COOKIE+'='+binder},allow_redirects=False)
            assert response.status==303 and response.headers['Location']=='/account'
            cookie=response.cookies[COOKIE]
            assert cookie['secure'] and cookie['httponly'] and cookie['samesite']=='Lax' and cookie['path']=='/'
            assert 'no-store' in response.headers['Cache-Control'] and response.headers['Referrer-Policy']=='no-referrer'
            assert cookie.value not in await response.text()
            assert 'code=' not in response.headers['Location']
            for path in ['/','/account','/docs/terms','/docs/privacy','/docs/support']:
                page=await p.http.get(path);assert page.status==200
                assert 'no-store' in page.headers['Cache-Control']
            data=await (await p.http.get('/api/plans')).json()
            assert [r['amount_kopecks'] for r in data['plans']]==[19900,49900,179000]
            assert data['sbp_available'] is False and data['documents_approved'] is False
    asyncio.run(run())


def test_concurrent_oidc_callbacks_can_issue_only_one_session(tmp_path,key):
    async def run():
        async with site(tmp_path,key) as p:
            destination,browser=await p.auth.begin()
            state=parse_qs(urlsplit(destination).query)['state'][0]
            results=await asyncio.gather(*(p.auth.finish(state,browser,'501') for _ in range(2)),return_exceptions=True)
            assert sum(isinstance(r,str) for r in results)==1
            assert sum(isinstance(r,PermissionError) for r in results)==1
    asyncio.run(run())


def test_closed_review_denies_anonymous_and_foreign_identity_before_writes(tmp_path,key):
    async def run():
        async with site(tmp_path,key,closed=True) as p:
            for path in ['/','/account','/docs/terms','/docs/privacy','/api/plans','/assets/site.js']:
                response=await p.http.get(path)
                assert response.status==401
                assert '199' not in await response.text()
            assert (await p.http.get('/auth/assets/login.css')).status==200
            assert (await p.http.get('/auth/assets/logo.png')).status==200
            for foreign in (502,503):
                with pytest.raises(PermissionError):await login(p,foreign)
            async with p.db() as session:
                from bot.db.models import WebIdentity
                assert await session.scalar(select(func.count()).select_from(WebIdentity))==0
                assert await session.scalar(select(func.count()).select_from(WebSession))==0
                assert await session.scalar(select(User).where(User.telegram_id==503)) is None
                existing=await session.scalar(select(User).where(User.telegram_id==502))
                with pytest.raises(PermissionError):await p.auth.new_session(existing.id)
            headers,token=await login(p,501)
            assert (await p.http.get('/',headers=headers)).status==200
            assert (await p.http.get('/api/plans',headers=headers)).status==200
            p.auth.allowed_telegram_ids=frozenset({502})
            with pytest.raises(PermissionError):await p.auth.owner(token)
    asyncio.run(run())


def test_closed_review_requires_explicit_single_owner(tmp_path,key):
    async def run():
        async with site(tmp_path,key) as p:
            with pytest.raises(ValueError):
                create_website(p.service,p.auth,p.ledger,ORIGIN,local_demo=True,closed_review=True)
    asyncio.run(run())


@pytest.mark.parametrize('fault',[None,'live','old_db','vpn_port','http','query'])
def test_review_entrypoint_cannot_use_live_or_existing_pilot_database(monkeypatch,fault):
    from bot.subscription_api.website_main import settings, ORIGIN as REVIEW_ORIGIN
    monkeypatch.setenv('KUDAVPN_SITE_MODE','review-test')
    monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://kudavpn_website_test@127.0.0.1/kudavpn_website_test')
    monkeypatch.setenv('SITE_PUBLIC_ORIGIN',REVIEW_ORIGIN)
    monkeypatch.setenv('SITE_PORT','18106')
    if fault=='live':monkeypatch.setenv('KUDAVPN_SITE_MODE','live')
    if fault=='old_db':monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://kudavpn_pilot@127.0.0.1/kudavpn_telegram_pilot')
    if fault=='vpn_port':monkeypatch.setenv('SITE_PORT','10443')
    if fault=='http':monkeypatch.setenv('SITE_PUBLIC_ORIGIN','http://site.example')
    if fault=='query':monkeypatch.setenv('SITE_PUBLIC_ORIGIN','https://site.example?owner=501')
    if fault:
        with pytest.raises(ValueError):settings()
    else:
        assert settings()[2]==18106
