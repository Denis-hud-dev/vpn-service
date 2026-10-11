from aiogram import F, Router
from aiogram.types import CallbackQuery
from bot.config import Config
from bot.navigation import Navigation
from bot import screens


async def support(callback: CallbackQuery, config: Config, navigation: Navigation):
    await navigation.callback(callback, screens.support(config.support_username))


def create_router() -> Router:
    router = Router(name="support")
    router.callback_query.register(support, F.data == "nav:support")
    return router
