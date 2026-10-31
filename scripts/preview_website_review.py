"""Loopback fixture preview only. Never install this helper on a server."""
import argparse
import asyncio
from datetime import timedelta
import json
from pathlib import Path
import tempfile

from aiohttp import web
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.db.models import Base
from bot.services.subscriptions import utcnow
from bot.services.website_review import ReadOnlyReview,ReviewAuth,ReviewSnapshot
from bot.subscription_api.website import DEMO_COOKIE,create_website


async def build(port,directory):
    engine=create_async_engine('sqlite+aiosqlite:///'+str(directory/'auth.db'))
    async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    auth=ReviewAuth(sessions,None,allowed_telegram_ids={501})
    path=directory/'snapshot.json'
    import time
    fixture={'version':1,'owner_id':501,'owner_known':True,'timestamp':time.time(),
             'account':{'display_name':'Демо · вымышленные данные','status':'active',
                        'tariff':'1 месяц — 199 ₽','expires_at':(utcnow()+timedelta(days=25)).isoformat(),
                        'nodes':['Демо-узел · имитация'],'feed_url':'https://fixture.invalid/s/'+'a'*43},
             'payments':[{'tariff':'1 месяц — 199 ₽','amount_kopecks':19900,'currency':'RUB',
                          'status':'succeeded','created_at':utcnow().isoformat(),'test':True,
                          'receipt':'test_not_required','receipt_error':None}]}
    path.write_text(json.dumps(fixture));path.chmod(0o600)
    reader=ReviewSnapshot(path,501,'https://fixture.invalid',root_owned=False)
    service=ReadOnlyReview(reader,f'http://127.0.0.1:{port}')
    app=create_website(service,auth,service,service.origin,local_demo=True,closed_review=True)

    @web.middleware
    async def local(request,handler):
        if request.path=='/__demo__/login':
            token=await auth.new_session(501)
            response=web.Response(status=303,headers={'Location':'/account'})
            response.set_cookie(DEMO_COOKIE,token,httponly=True,samesite='Lax',path='/',max_age=3600)
            return response
        response=await handler(request)
        if request.path=='/connect' and response.status==200:
            response.body=response.body.replace(b'<body>', '<body><p role="status">ЛОКАЛЬНОЕ ДЕМО: QR и ссылка вымышленные, не импортируйте в Happ.</p>'.encode())
        return response
    app.middlewares.insert(0,local)

    async def lifecycle(app):
        async def refresh():
            while True:
                fixture['timestamp']=time.time();path.write_text(json.dumps(fixture))
                await asyncio.sleep(30)
        task=asyncio.create_task(refresh())
        try:yield
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True);await engine.dispose()
    app.cleanup_ctx.append(lifecycle)
    return app


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=18107)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='kuda-review-demo-') as directory:
        web.run_app(build(args.port,Path(directory)),host='127.0.0.1',port=args.port,access_log=None,print=None)


if __name__=='__main__':main()
