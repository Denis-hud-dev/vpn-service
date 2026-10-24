"""Run from repository root: python -m bot.main."""
import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from bot.observability import TelegramTimingMiddleware, SafeDependencyLogs
from bot.config import Config
from bot.handlers import create_router
from bot.services import Services
from bot.db import Database
from bot.navigation import Navigation
from bot.services.navigation import PostgresScreenStore
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


async def run(config: Config) -> None:
    database = Database(config.database_url) if config.database_url else None
    services = (Services.persistent(database.sessions,
        subscription_public_url=config.subscription_public_url,
        happ_link_secret=config.happ_link_secret,
        trial_owner_id=config.pilot_trial_owner_id,
        pilot_connect_page_enabled=config.pilot_connect_page_enabled,
        premium_account_enabled=config.pilot_premium_account_enabled,
        pilot_welcome_enabled=config.pilot_welcome_enabled,
        onboarding_state_file=config.pilot_welcome_state_file) if database else Services.demo())
    payment_poller = None
    if config.pilot_test_payments_enabled:
        from bot.services.yookassa_api import YooKassaTestClient
        from bot.services.payments import PaymentCheckoutService
        from bot.services.payment_polling import TestPaymentPoller
        client = YooKassaTestClient.from_private_files(
            config.pilot_yookassa_shop_id_file, config.pilot_yookassa_secret_file)
        services.test_checkout = PaymentCheckoutService(
            database.sessions, client, config.pilot_payment_return_url)
        services.test_payment_owner_id = config.pilot_trial_owner_id
        payment_poller = TestPaymentPoller(database.sessions, client)
    dispatcher = Dispatcher(config=config, services=services,
        navigation=Navigation(PostgresScreenStore(database.sessions) if database else None,
                              custom_emoji=config.pilot_custom_emoji_enabled))
    dispatcher.include_router(create_router())
    ready = False
    expiry_task = None
    payment_task = None
    notice_task = None

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"status": "alive"})

    async def readiness(request: web.Request) -> web.Response:
        available = ready
        if available and database:
            try:
                await asyncio.wait_for(database.ping(), timeout=3)
            except Exception:
                available = False
        return web.json_response({"ready": available}, status=200 if available else 503)

    async def startup(bot: Bot) -> None:
        nonlocal ready, expiry_task, payment_task, notice_task
        if database:
            await database.check()
            from bot.services.expiry import expiry_worker
            expiry_task = asyncio.create_task(expiry_worker(services.subscriptions))
        else:
            logger.warning("DATABASE_URL not set: demo mode, data is not persistent")
        me = await bot.get_me()
        if (config.pilot_test_payments_enabled or config.pilot_premium_account_enabled
                or config.pilot_welcome_enabled
                or config.pilot_custom_emoji_enabled) and (me.username or '').lower() != 'kudavpn_test_bot':
            raise RuntimeError('Пилотные функции разрешены только для отдельного бота')
        dispatcher["bot_username"] = me.username or "KudaVpnBot"
        if payment_poller:
            from bot.services.payment_polling import run_test_payment_poller
            payment_task = asyncio.create_task(run_test_payment_poller(payment_poller))
        if config.pilot_premium_account_enabled:
            from bot.services.premium_notices import PremiumNoticeService, run_premium_notices
            notice_task = asyncio.create_task(run_premium_notices(
                PremiumNoticeService(database.sessions, config.pilot_trial_owner_id), bot))
        ready = True
        logger.info("KudaVPN started as @%s", me.username)

    async def shutdown() -> None:
        nonlocal ready
        ready = False
        logger.info("KudaVPN stopping")

    dispatcher.startup.register(startup)
    dispatcher.shutdown.register(shutdown)
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/ready", readiness)
    runner = web.AppRunner(app, access_log=None)
    # Context manager also closes the session when startup fails.
    session = AiohttpSession(timeout=10)
    session.middleware(TelegramTimingMiddleware())
    async with Bot(config.bot_token, session=session) as bot:
        try:
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", config.health_port).start()
            await dispatcher.start_polling(bot, handle_signals=True, close_bot_session=False,
                                           allowed_updates=dispatcher.resolve_used_update_types(),
                                           polling_timeout=30, tasks_concurrency_limit=64)
        finally:
            ready = False
            if expiry_task:
                expiry_task.cancel()
                try:
                    await expiry_task
                except asyncio.CancelledError:
                    pass
            if payment_task:
                payment_task.cancel()
                try:
                    await payment_task
                except asyncio.CancelledError:
                    pass
            if notice_task:
                notice_task.cancel()
                try:
                    await notice_task
                except asyncio.CancelledError:
                    pass
            await runner.cleanup()
            await dispatcher.storage.close()
            if database:
                await database.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for handler in logging.getLogger().handlers:
        handler.addFilter(SafeDependencyLogs())
    # Avoid HTTP request logs containing a bot token in the URL.
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    try:
        config = Config.from_env()
    except (ValueError, TypeError) as exc:
        raise SystemExit(str(exc)) from None
    try:
        asyncio.run(run(config))
    except (KeyboardInterrupt, SystemExit):
        logger.info("KudaVPN stopped")
    except (SQLAlchemyError, ValueError, RuntimeError) as exc:
        logger.error("Startup/runtime failure: %s; check configuration and migrations", type(exc).__name__)
        raise SystemExit("Не удалось запустить бота. Проверьте конфигурацию и миграции базы.") from None


if __name__ == "__main__":
    main()
