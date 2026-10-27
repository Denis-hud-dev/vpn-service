"""Telegram OIDC code+PKCE, fixed endpoints, hashed durable browser sessions."""
import base64
from datetime import timedelta
import hashlib
import hmac
import logging
import re
import secrets
import time
from urllib.parse import urlencode

import aiohttp
import jwt
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from bot.db.models import User, WebIdentity, WebLoginFlow, WebSession
from bot.services.subscriptions import as_utc, utcnow

ISSUER = 'https://oauth.telegram.org'


class OIDCFailure(PermissionError):
    """Fixed operator-only reason. Never contains provider bodies or claims."""
    def __init__(self, reason):
        super().__init__('Authentication unavailable')
        self.reason=reason


def verified_telegram_id(value):
    """Normalize a signature-verified ID; never infer identity from other fields."""
    if value is None:raise OIDCFailure('profile_id_missing')
    if type(value) is int:
        identifier=value
    elif isinstance(value,str):
        if not re.fullmatch(r'[1-9][0-9]{0,18}',value):
            raise OIDCFailure('profile_id_string_invalid')
        identifier=int(value)
    else:
        raise OIDCFailure('profile_id_type_rejected')
    if not 0 < identifier < 2**63:raise OIDCFailure('profile_id_range_rejected')
    return identifier


def digest(value):
    if not isinstance(value, str) or not 32 <= len(value) <= 256:
        raise PermissionError('Authentication unavailable')
    return hashlib.sha256(value.encode()).hexdigest()


class TelegramOIDC:
    def __init__(self, client_id, secret, redirect_uri, *, transport=None):
        if not str(client_id).isdigit() or not secret or not redirect_uri.startswith('https://'):
            raise ValueError('OIDC configuration required')
        self.client_id, self._secret, self.redirect_uri = str(client_id), secret, redirect_uri
        self.transport = transport
        self._keys, self._keys_until = None, 0

    async def request(self, method, path, *, data=None):
        try:
            if self.transport:
                return await self.transport(method, path, data)
            async with aiohttp.ClientSession(trust_env=False,
                    timeout=aiohttp.ClientTimeout(total=12, connect=5)) as session:
                async with session.request(method, ISSUER+path, data=data,
                        auth=aiohttp.BasicAuth(self.client_id, self._secret) if method == 'POST' else None,
                        allow_redirects=False) as response:
                    if response.status != 200:
                        raise OIDCFailure('provider_http_'+str(response.status))
                    chunks=[]
                    total=0
                    async for chunk in response.content.iter_chunked(16384):
                        total+=len(chunk)
                        if total>65536:raise OIDCFailure('provider_response_too_large')
                        chunks.append(chunk)
                    import json
                    body=json.loads(b''.join(chunks))
                    if not isinstance(body,dict):raise OIDCFailure('provider_payload_invalid')
                    if 'error' in body:
                        allowed={'invalid_grant','invalid_client','invalid_request','unauthorized_client','unsupported_grant_type','invalid_scope'}
                        raise OIDCFailure('provider_'+body['error'] if body['error'] in allowed else 'provider_error')
                    return body
        except OIDCFailure:
            raise
        except Exception:
            # Never expose response bodies, auth codes, client secrets or tokens.
            raise OIDCFailure('provider_transport_or_payload_error') from None

    async def exchange(self, code, verifier):
        if not isinstance(code, str) or not 1 <= len(code) <= 2048:
            raise PermissionError('Authentication unavailable')
        response = await self.request('POST', '/token', data={
            'grant_type': 'authorization_code', 'code': code, 'client_id': self.client_id,
            'redirect_uri': self.redirect_uri, 'code_verifier': verifier})
        if not response.get('id_token'):raise OIDCFailure('id_token_missing')
        return await self.validate(response['id_token'])

    async def validate(self, token):
        try:
            if not isinstance(token, str) or len(token) > 16000:
                raise ValueError()
            header = jwt.get_unverified_header(token)
            if header.get('alg') != 'RS256':raise OIDCFailure('signing_algorithm_unsupported')
            if not isinstance(header.get('kid'),str):raise OIDCFailure('key_id_missing')
            if self._keys is None or self._keys_until <= time.monotonic():
                self._keys = await self.request('GET', '/.well-known/jwks.json')
                self._keys_until = time.monotonic()+300
            keys = [k for k in self._keys.get('keys', []) if k.get('kid') == header['kid']
                    and k.get('kty') == 'RSA' and k.get('use', 'sig') == 'sig'
                    and k.get('alg', 'RS256') == 'RS256']
            if len(keys) != 1:
                # Fail closed during a key rollover; the next fresh login can retry.
                self._keys_until = 0
                raise OIDCFailure('signing_key_unavailable')
            claims = jwt.decode(token, jwt.PyJWK.from_dict(keys[0]).key,
                algorithms=['RS256'], issuer=ISSUER, audience=self.client_id, leeway=10,
                options={'require': ['iss', 'aud', 'sub', 'iat', 'exp'], 'strict_aud': True})
            identifier=verified_telegram_id(claims.get('id'))
            if not isinstance(claims['sub'],str) or not 1 <= len(claims['sub']) <= 128:
                raise OIDCFailure('subject_shape_rejected')
            if type(claims['iat']) is not int or type(claims['exp']) is not int:
                raise OIDCFailure('token_time_shape_rejected')
            if claims['iat'] < time.time()-600:raise OIDCFailure('token_age_policy_rejected')
            if claims['exp'] <= claims['iat']:raise OIDCFailure('token_time_order_rejected')
            return {'subject': claims['sub'], 'telegram_id': identifier}
        except OIDCFailure:
            raise
        except jwt.ExpiredSignatureError:
            raise OIDCFailure('id_token_expired') from None
        except jwt.ImmatureSignatureError:
            raise OIDCFailure('id_token_not_yet_valid') from None
        except jwt.InvalidAudienceError:
            raise OIDCFailure('id_token_audience_rejected') from None
        except jwt.InvalidIssuerError:
            raise OIDCFailure('id_token_issuer_rejected') from None
        except jwt.InvalidSignatureError:
            raise OIDCFailure('id_token_signature_rejected') from None
        except Exception:
            raise OIDCFailure('id_token_structure_rejected') from None


class WebsiteAuth:
    session_model = WebSession

    def __init__(self, sessions, oidc, *, clock=utcnow, allowed_telegram_ids=None):
        self.sessions, self.oidc, self.clock = sessions, oidc, clock
        self.allowed_telegram_ids = None if allowed_telegram_ids is None else frozenset(allowed_telegram_ids)
        if self.allowed_telegram_ids is not None and (not self.allowed_telegram_ids or any(
                type(value) is not int or not 0 < value < 2**63 for value in self.allowed_telegram_ids)):
            raise ValueError('Explicit review account allowlist required')

    def permitted(self, owner):
        return self.allowed_telegram_ids is None or owner in self.allowed_telegram_ids

    async def begin(self):
        state, browser, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        async with self.sessions.begin() as session:
            await session.execute(delete(WebLoginFlow).where(WebLoginFlow.expires_at <= self.clock()))
            await session.execute(delete(self.session_model).where(self.session_model.expires_at <= self.clock()))
            session.add(WebLoginFlow(digest=digest(state), browser_digest=digest(browser),
                verifier=verifier, expires_at=self.clock()+timedelta(minutes=10)))
        url = ISSUER+'/auth?'+urlencode({'client_id': self.oidc.client_id,
            'redirect_uri': self.oidc.redirect_uri, 'response_type': 'code', 'scope': 'openid profile',
            'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256'})
        return url, browser

    async def authenticate_code(self, state, browser, code):
        async with self.sessions.begin() as session:
            # DELETE RETURNING is atomic in PostgreSQL and SQLite: one callback wins.
            flow = (await session.execute(delete(WebLoginFlow).where(
                WebLoginFlow.digest == digest(state), WebLoginFlow.browser_digest == digest(browser),
                WebLoginFlow.expires_at > self.clock()).returning(WebLoginFlow.verifier))).scalar_one_or_none()
            if flow is None:
                raise PermissionError('Authentication unavailable')
        try:
            identity = await self.oidc.exchange(code, flow)
        except OIDCFailure as error:
            logging.warning('website_auth outcome=%s',error.reason)
            raise PermissionError('Authentication unavailable') from None
        if not self.permitted(identity['telegram_id']):
            # Reject before writing User, WebIdentity or WebSession.
            logging.warning('website_auth outcome=owner_not_allowed')
            raise PermissionError('Authentication unavailable')
        return identity

    async def finish(self, state, browser, code):
        identity = await self.authenticate_code(state, browser, code)
        # Only authenticated claims reach the existing user registry. No URL/user body ID.
        async with self.sessions.begin() as session:
            insert = pg_insert if session.bind.dialect.name == 'postgresql' else sqlite_insert
            await session.execute(insert(User).values(telegram_id=identity['telegram_id'], first_name='KudaVPN')
                .on_conflict_do_nothing(index_elements=[User.telegram_id]))
            user = await session.scalar(select(User).where(User.telegram_id == identity['telegram_id']))
            await session.execute(insert(WebIdentity).values(subject=identity['subject'], user_id=user.id)
                .on_conflict_do_nothing())
            bound = await session.get(WebIdentity, identity['subject'])
            if bound is None or bound.user_id != user.id:
                raise PermissionError('Authentication unavailable')
        return await self.new_session(user.id)

    async def new_session(self, user_id):
        """Internal only; production HTTP routes can reach it only after verified OIDC."""
        token = secrets.token_urlsafe(32)
        async with self.sessions.begin() as session:
            owner = await session.scalar(select(User.telegram_id).where(User.id == user_id))
            if owner is None or not self.permitted(owner):
                raise PermissionError('Authentication unavailable')
            session.add(WebSession(digest=digest(token), user_id=user_id,
                expires_at=self.clock()+timedelta(hours=1)))
        return token

    async def owner(self, token):
        async with self.sessions() as session:
            owner = await session.scalar(select(User.telegram_id).join(WebSession).where(
                WebSession.digest == digest(token), WebSession.expires_at > self.clock()))
        if owner is None or not self.permitted(owner):
            raise PermissionError('Authentication unavailable')
        return owner

    @staticmethod
    def csrf(token):
        digest(token)
        return hmac.new(token.encode(), b'kudavpn-website-csrf', hashlib.sha256).hexdigest()

    async def logout(self, token):
        async with self.sessions.begin() as session:
            await session.execute(delete(WebSession).where(WebSession.digest == digest(token)))
