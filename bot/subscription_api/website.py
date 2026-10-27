"""Independent review website. No bot changes, live keys or public webhook."""
from collections import deque
import hmac
from pathlib import Path
import time
from urllib.parse import urlsplit

from aiohttp import web

from bot.services.web_checkout import OrderUnavailable
from bot.services.yookassa_api import PaymentProviderUnavailable

ASSETS = Path(__file__).with_name('static')
COOKIE, FLOW_COOKIE = '__Host-kuda_web', '__Host-kuda_login'
DEMO_COOKIE, DEMO_FLOW = 'kuda_demo_web', 'kuda_demo_login'
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
       "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")


def create_website(service, auth, ledger, origin, *, local_demo=False, closed_review=False):
    url = urlsplit(origin)
    if (url.path or url.query or url.fragment or url.username or not url.hostname
            or (local_demo and (url.scheme != 'http' or url.hostname != '127.0.0.1'))
            or (not local_demo and url.scheme != 'https')):
        raise ValueError('Explicit HTTPS website origin required')
    cookie, flow_cookie = (DEMO_COOKIE, DEMO_FLOW) if local_demo else (COOKIE, FLOW_COOKIE)
    if closed_review and (auth.allowed_telegram_ids is None or len(auth.allowed_telegram_ids) != 1):
        raise ValueError('Exactly one trusted review owner required')
    buckets = {name: deque() for name in ('auth', 'create', 'refresh')}

    def limit(name, maximum, seconds):
        now, bucket = time.monotonic(), buckets[name]
        while bucket and bucket[0] < now-seconds:
            bucket.popleft()
        if len(bucket) >= maximum:
            raise web.HTTPTooManyRequests()
        bucket.append(now)

    @web.middleware
    async def private(request, handler):
        try:
            if closed_review and request.path not in {
                    '/auth/login', '/auth/callback', '/auth/assets/login.css', '/auth/assets/logo.png'}:
                if not (request.path == '/ready' and request.remote in {'127.0.0.1', '::1'}):
                    await auth.owner(request.cookies.get(cookie, ''))
            response = await handler(request)
        except PermissionError:
            if closed_review and request.path in {'/', '/account'} and request.method == 'GET':
                response = web.Response(body=(ASSETS/'website-login.html').read_bytes(),
                                        content_type='text/html', status=401)
            else:
                response = web.json_response({'error': 'authentication_required'}, status=401)
        except OrderUnavailable:
            response = web.json_response({'error': 'order_unavailable'}, status=404)
        except PaymentProviderUnavailable:
            response = web.json_response({'error': 'provider_unavailable'}, status=503)
        except (ValueError, TypeError, KeyError):
            response = web.json_response({'error': 'invalid_request'}, status=400)
        except web.HTTPException as error:
            # Redirects must not be raised here: Location may contain OIDC state.
            response = web.json_response({'error': 'request_unavailable'}, status=error.status)
        except Exception:
            response = web.json_response({'error': 'temporarily_unavailable'}, status=503)
        response.headers.update({'Cache-Control': 'no-store, private', 'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': CSP, 'X-Frame-Options': 'DENY', 'X-Content-Type-Options': 'nosniff'})
        if not local_demo:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    app = web.Application(middlewares=[private], client_max_size=4096)

    async def identity(request, *, mutation=False):
        token = request.cookies.get(cookie, '')
        owner = await auth.owner(token)
        if mutation and (request.headers.get('Origin') != origin
                or not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), auth.csrf(token))):
            raise web.HTTPForbidden()
        return owner

    def set_cookie(response, name, value, max_age):
        response.set_cookie(name, value, secure=not local_demo, httponly=True,
                            samesite='Lax', path='/', max_age=max_age)

    async def page(request):
        body = (ASSETS/'website.html').read_bytes()
        if request.path == '/account':
            body = body.replace(b'<body>', b'<body class="account-view">')
        return web.Response(body=body, content_type='text/html')

    async def asset(request):
        name = request.match_info['name']
        files = {'site.css': ('website.css', 'text/css'), 'base.css': ('checkout.css', 'text/css'), 'site.js': ('website.js', 'application/javascript'),
                 'logo.png': ('kudavpn-orbit-emblem.png', 'image/png'),
                 'connect.css': ('connect.css', 'text/css'), 'connect.js': ('connect.js', 'application/javascript'),
                 'kudavpn-orbit-emblem.png': ('kudavpn-orbit-emblem.png', 'image/png')}
        if name not in files:
            raise web.HTTPNotFound()
        filename, mime = files[name]
        return web.Response(body=(ASSETS/filename).read_bytes(), content_type=mime)

    async def login(request):
        limit('auth', 20, 60)
        # No user ID, return URL or caller-selected identity is accepted.
        if request.query:
            raise ValueError()
        destination, binder = await auth.begin()
        response = web.Response(status=303, headers={'Location': destination})
        set_cookie(response, flow_cookie, binder, 600)
        return response

    async def callback(request):
        limit('auth', 20, 60)
        if set(request.query) != {'state', 'code'} or any(len(request.query.getall(k)) != 1 for k in request.query):
            raise PermissionError()
        token = await auth.finish(request.query['state'], request.cookies.get(flow_cookie, ''), request.query['code'])
        response = web.Response(status=303, headers={'Location': '/account'})
        set_cookie(response, cookie, token, 3600)
        response.del_cookie(flow_cookie, path='/', secure=not local_demo, httponly=True, samesite='Lax')
        return response

    async def session(request):
        await identity(request)
        return web.json_response({'csrf': auth.csrf(request.cookies[cookie]), 'test': True})

    async def logout(request):
        await identity(request, mutation=True)
        await auth.logout(request.cookies[cookie])
        response = web.json_response({'ok': True})
        response.del_cookie(cookie, path='/', secure=not local_demo, httponly=True, samesite='Lax')
        return response

    async def plans(request):
        read_only = bool(getattr(service, 'read_only', False))
        return web.json_response({'plans': service.plans(), 'test': True, 'sbp_available': False,
                                 'methods': [] if read_only else ['bank_card', 'yoo_money'],
                                 'read_only': read_only, 'documents_approved': False})

    async def connection(request):
        from bot.subscription_api.connect_page import render_connection_page
        feed, expiry = await service.connection(await identity(request))
        return web.Response(text=render_connection_page(feed, expiry), content_type='text/html')

    async def account(request):
        return web.json_response(await service.account(await identity(request)))

    async def history(request):
        return web.json_response({'payments': await ledger.history(await identity(request))})

    async def create(request):
        owner = await identity(request, mutation=True)
        limit('create', 10, 60)
        data = await request.json()
        if not isinstance(data, dict) or set(data) != {'tariff_id'}:
            raise ValueError()
        return web.json_response(await service.create(owner, data['tariff_id']))

    async def order(request):
        return web.json_response(await service.state(await identity(request), request.match_info['order']))

    async def latest(request):
        return web.json_response({'order': await service.latest(await identity(request))})

    async def refresh(request):
        owner = await identity(request, mutation=True)
        limit('refresh', 20, 60)
        result = await service.refresh(owner, request.match_info['order'])
        await ledger.reconcile(owner)
        return web.json_response(result)

    async def documents(request):
        name = request.match_info['name']
        if name not in {'terms', 'privacy', 'support'}:
            raise web.HTTPNotFound()
        return web.Response(body=(ASSETS/('site-'+name+'.html')).read_bytes(), content_type='text/html')

    for path in ('/', '/account'):
        app.router.add_get(path, page)
    app.router.add_get('/connect', connection)
    app.router.add_get('/assets/{name}', asset)
    app.router.add_get('/auth/login', login)
    app.router.add_get('/auth/callback', callback)
    async def login_asset(request):
        if request.match_info['name'] == 'login.css':
            return web.Response(body=(ASSETS/'website-login.css').read_bytes(),content_type='text/css')
        if request.match_info['name'] == 'logo.png':
            return web.Response(body=(ASSETS/'kudavpn-orbit-emblem.png').read_bytes(),content_type='image/png')
        raise web.HTTPNotFound()
    app.router.add_get('/auth/assets/{name}', login_asset)
    app.router.add_get('/api/session', session)
    app.router.add_post('/api/logout', logout)
    app.router.add_get('/api/plans', plans)
    app.router.add_get('/api/account', account)
    app.router.add_get('/api/payments', history)
    app.router.add_post('/api/orders', create)
    app.router.add_get('/api/orders/latest', latest)
    app.router.add_get('/api/orders/{order}', order)
    app.router.add_post('/api/orders/{order}/refresh', refresh)
    app.router.add_get('/docs/{name}', documents)
    return app
