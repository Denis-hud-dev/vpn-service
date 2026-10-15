from aiohttp import web
from bot.services.happ import AccessDenied


@web.middleware
async def private_response(request, handler):
    headers = {'Cache-Control':'no-store, private', 'Referrer-Policy':'no-referrer',
               'X-Content-Type-Options':'nosniff'}
    try:
        response = await handler(request)
    except AccessDenied:
        response = web.Response(status=404, text='Subscription unavailable')
    except web.HTTPException as exc:
        response = exc
    except Exception:
        # Never log request URL, bearer token or exception text/tracebacks.
        response = web.Response(status=503, text='Temporarily unavailable')
    response.headers.update(headers)
    return response


def create_app(service):
    app = web.Application(middlewares=[private_response], client_max_size=1024)
    async def feed(request):
        body, expires = await service.feed(request.match_info['token'])
        return web.Response(text=body, content_type='text/plain', headers={
            'profile-title':'KudaVPN Premium', 'profile-update-interval':'1',
            'subscription-userinfo':f'expire={expires}'})
    app.router.add_get('/s/{token}', feed)
    return app
