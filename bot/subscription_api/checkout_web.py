"""Separate, opt-in browser checkout. Never linked or embedded by the bot."""
import asyncio
from collections import deque
import hmac
import logging
from pathlib import Path
import time
from urllib.parse import urlsplit

from aiohttp import web

from bot.services.web_checkout import OrderUnavailable
from bot.services.yookassa_api import PaymentProviderUnavailable

ASSETS = Path(__file__).with_name('static')
COOKIE = 'kuda_test_checkout'
logger = logging.getLogger(__name__)
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
       "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def create_checkout_app(service, sessions, public_origin: str, *, local_demo=False,
                        refund_processor=None, enable_webhook=False, webhook_inbox=None):
    parsed = urlsplit(public_origin)
    if (parsed.path not in ('', '/') or parsed.query or parsed.fragment or parsed.username or parsed.password
            or not parsed.hostname or (parsed.scheme != 'https' and not
                (local_demo and parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'))):
        raise ValueError('Trusted checkout origin required')
    origin = public_origin.rstrip('/')
    # Bounded pilot throttles. Only one configured owner can authenticate; public
    # invalid requests use one shared bucket rather than attacker-controlled keys.
    buckets = {'login': deque(), 'create': deque(), 'refresh': deque(), 'webhook': deque()}
    create_lock = asyncio.Lock()

    def limit(kind, maximum, seconds):
        bucket, now = buckets[kind], time.monotonic()
        while bucket and bucket[0] <= now - seconds:
            bucket.popleft()
        if len(bucket) >= maximum:
            raise web.HTTPTooManyRequests(headers={'Retry-After': str(seconds)})
        bucket.append(now)

    @web.middleware
    async def private(request, handler):
        try:
            if request.path == '/yookassa/test-webhook':
                limit('webhook', 60, 60)
            response = await handler(request)
        except PermissionError:
            response = web.json_response({'error': 'session_unavailable'}, status=401)
        except OrderUnavailable:
            response = web.json_response({'error': 'order_unavailable'}, status=404)
        except PaymentProviderUnavailable:
            response = web.json_response({'error': 'provider_unavailable'}, status=503)
        except (ValueError, web.HTTPBadRequest):
            response = web.json_response({'error': 'invalid_request'}, status=400)
        except web.HTTPException as exc:
            response = web.json_response({'error': 'request_forbidden' if exc.status == 403 else 'request_unavailable'}, status=exc.status,
                                         headers={'Retry-After': exc.headers.get('Retry-After', '5')} if exc.status == 429 else {})
        except Exception:
            # Exceptions can contain DSNs, request bodies or provider URLs.
            response = web.json_response({'error': 'temporarily_unavailable'}, status=503)
        response.headers.update({'Cache-Control': 'no-store, private', 'Referrer-Policy': 'no-referrer',
                                 'Content-Security-Policy': CSP, 'X-Frame-Options': 'DENY',
                                 'X-Content-Type-Options': 'nosniff'})
        return response

    app = web.Application(middlewares=[private], client_max_size=4096)

    def authenticate(request, *, mutation=False):
        token = request.cookies.get(COOKIE, '')
        owner = sessions.owner(token)
        if mutation:
            if request.headers.get('Origin') != origin:
                logger.warning('checkout_request outcome=origin_rejected')
                raise web.HTTPForbidden()
            if not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), sessions.csrf(token)):
                logger.warning('checkout_request outcome=csrf_rejected')
                raise web.HTTPForbidden()
        return owner

    async def page(request):
        return web.Response(body=(ASSETS / 'checkout.html').read_bytes(), content_type='text/html')

    async def asset(request):
        name = request.match_info['name']
        if name == 'logo.png':
            return web.Response(body=(ASSETS / 'kudavpn-orbit-emblem.png').read_bytes(), content_type='image/png')
        if name not in ('checkout.css', 'checkout.js'):
            raise web.HTTPNotFound()
        return web.Response(body=(ASSETS / name).read_bytes(),
                            content_type='text/css' if name.endswith('.css') else 'application/javascript')

    async def exchange(request):
        limit('login', 10, 60)
        if request.headers.get('Origin') != origin:
            raise web.HTTPForbidden()
        data = await request.json()
        if not isinstance(data, dict) or set(data) != {'token'}:
            raise ValueError()
        token = sessions.exchange(data['token'])
        response = web.json_response({'csrf': sessions.csrf(token)})
        response.set_cookie(COOKIE, token, max_age=3600, httponly=True, secure=not local_demo,
                            samesite='Lax', path='/checkout')
        return response

    async def session(request):
        authenticate(request)
        return web.json_response({'csrf': sessions.csrf(request.cookies[COOKIE]),
                                  'test': True, 'seller_regime': 'npd', 'vat': 'not_applicable'})

    async def logout(request):
        authenticate(request, mutation=True)
        sessions.logout(request.cookies[COOKIE])
        response = web.json_response({'ok': True})
        response.del_cookie(COOKIE, path='/checkout')
        return response

    async def plans(request):
        return web.json_response({'plans': service.plans(), 'methods': ['bank_card', 'yoo_money'],
                                  'sbp_available': False, 'test': True})

    async def create(request):
        owner = authenticate(request, mutation=True)
        limit('create', 6, 60)
        data = await request.json()
        if not isinstance(data, dict) or set(data) != {'tariff_id'}:
            raise ValueError()
        async with create_lock:
            return web.json_response(await service.create(owner, data['tariff_id']))

    async def latest(request):
        return web.json_response({'order': await service.latest(authenticate(request))})

    async def state(request):
        return web.json_response(await service.state(authenticate(request), request.match_info['order']))

    async def refresh(request):
        owner = authenticate(request, mutation=True)
        limit('refresh', 1, 5)
        return web.json_response(await service.refresh(owner, request.match_info['order']))

    app.router.add_get('/checkout', page)
    app.router.add_get('/checkout/assets/{name}', asset)
    app.router.add_post('/checkout/api/session', exchange)
    app.router.add_get('/checkout/api/session', session)
    app.router.add_post('/checkout/api/logout', logout)
    app.router.add_get('/checkout/api/plans', plans)
    app.router.add_post('/checkout/api/orders', create)
    app.router.add_get('/checkout/api/orders/latest', latest)
    app.router.add_get('/checkout/api/orders/{order}', state)
    app.router.add_post('/checkout/api/orders/{order}/refresh', refresh)
    if enable_webhook:
        from bot.subscription_api.payment_webhook import install_test_payment_webhook
        install_test_payment_webhook(app, service.processor, refund_processor=refund_processor, inbox=webhook_inbox)
    return app
