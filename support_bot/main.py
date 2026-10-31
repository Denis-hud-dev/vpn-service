"""python -m support_bot.main (separate process, no project startup imports)."""
import asyncio
import fcntl
import logging
import os
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from bot.observability import SafeDependencyLogs
from support_bot.application import Application, delivery_worker, dbcall
from support_bot.config import Config
from support_bot.storage import Store
from support_bot.polling import poll


async def run(config):
    os.umask(0o077)
    store = Store(config.database,config.admin_chat_id)
    # Schema changes must be applied explicitly before starting, to this DB only.
    await dbcall(store.delivery_health)
    with open(str(config.database)+'.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        async with Bot(config.token,session=AiohttpSession(timeout=10)) as bot:
            await config.validate_identity(bot)
            await dbcall(store.bind,bot.id,config.admin_chat_id)
            dispatcher = Dispatcher()
            dispatcher.include_router(Application(store,config).router)
            worker = asyncio.create_task(delivery_worker(store,config,bot))
            polling = asyncio.create_task(poll(bot,dispatcher,store))
            try:
                # Serial updates retain source ordering and complete DB transactions
                # before acknowledgement. Outgoing notifications use the durable outbox.
                done, _ = await asyncio.wait({worker,polling},return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in (worker,polling):
                    task.cancel()
                await asyncio.gather(worker,polling,return_exceptions=True)
                await dispatcher.storage.close()


def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    for handler in logging.getLogger().handlers:
        handler.addFilter(SafeDependencyLogs())
    try:
        asyncio.run(run(Config.from_env()))
    except KeyboardInterrupt:
        pass
    except Exception:
        # Avoid rendering token-bearing HTTP errors, SQL parameters or full updates.
        raise SystemExit('Support stopped safely; check private configuration, migrations and delivery health.') from None


if __name__ == '__main__':
    main()
