from aiogram import F, Router
from aiogram.types import CallbackQuery
from bot.navigation import Navigation
from bot import screens


async def traffic(callback: CallbackQuery, navigation: Navigation):
    await navigation.callback(callback, screens.traffic())


def create_router() -> Router:
    router = Router(name="traffic")
    router.callback_query.register(traffic, F.data == "nav:traffic")
    return router
