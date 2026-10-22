from aiohttp import web
from pathlib import Path
from bot.services.happ import AccessDenied
from bot.subscription_api.connect_page import render_connection_page

ASSETS = Path(__file__).with_name('static')
PAGE_CSP = ("default-src 'none'; style-src 'self'; script-src 'self'; "
            "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


@web.middleware
async def private_response(request, handler):
    headers = {'Cache-Control':'no-store, private', 'Referrer-Policy':'no-referrer',
               'X-Content-Type-Options':'nosniff'}
    try:
        response = await handler(request)
    except AccessDenied:
        response = web.Response(status=404, text='Subscription unavailable')
    except web.HTTPException as exc:
        # aiohttp no longer supports returning HTTPException as a response.
        response = web.Response(status=exc.status, text=exc.text)
    except Exception:
        # Never log request URL, bearer token or exception text/tracebacks.
        response = web.Response(status=503, text='Temporarily unavailable')
    response.headers.update(headers)
    response.headers['Content-Security-Policy'] = PAGE_CSP
    response.headers['X-Frame-Options'] = 'DENY'
    return response


def create_app(service, *, test_payment_processor=None):
    app = web.Application(middlewares=[private_response], client_max_size=1024)
    if test_payment_processor is not None:
        from bot.subscription_api.payment_webhook import install_test_payment_webhook
        install_test_payment_webhook(app, test_payment_processor)
    async def feed(request):
        body, expires = await service.feed(request.match_info['token'])
        return web.Response(text=body, content_type='text/plain', headers={
            'profile-title':'KudaVPN Premium', 'profile-update-interval':'1',
            'subscription-userinfo':f'expire={expires}'})
    app.router.add_get('/s/{token}', feed)
    async def connection_page(request):
        token = request.match_info['token']
        _, expires = await service.feed(token)
        # Reconstruct the feed URL only after entitlement and token validation.
        feed_url = service.public_url + '/s/' + token
        return web.Response(text=render_connection_page(feed_url, expires), content_type='text/html')
    app.router.add_get('/connect/{token}', connection_page)

    async def asset(request):
        name = request.match_info['name']
        if name not in ('connect.css', 'connect.js', 'kudavpn-original-logo.jpeg'):
            raise web.HTTPNotFound()
        content_type = ('text/css' if name.endswith('.css') else
                        'application/javascript' if name.endswith('.js') else 'image/jpeg')
        return web.Response(body=(ASSETS / name).read_bytes(), content_type=content_type)
    app.router.add_get('/assets/{name}', asset)
    return app
