"""Run from repository root: python -m bot.main."""
import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher
from bot.config import Config
from bot.handlers import create_router
from bot.services import Services

logger = logging.getLogger(__name__)


async def run(config: Config) -> None:
    dispatcher = Dispatcher(config=config, services=Services.demo())
    dispatcher.include_router(create_router())
    ready = False

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"status": "alive"})

    async def readiness(request: web.Request) -> web.Response:
        return web.json_response({"ready": ready}, status=200 if ready else 503)

    async def startup(bot: Bot) -> None:
        nonlocal ready
        me = await bot.get_me()
        dispatcher["bot_username"] = me.username or "KudaVpnBot"
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
    async with Bot(config.bot_token) as bot:
        try:
            await runner.setup()
            await web.TCPSite(runner, "0.0.0.0", config.health_port).start()
            await dispatcher.start_polling(bot, handle_signals=True, close_bot_session=False,
                                           allowed_updates=dispatcher.resolve_used_update_types())
        finally:
            ready = False
            await runner.cleanup()
            await dispatcher.storage.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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


if __name__ == "__main__":
    main()
