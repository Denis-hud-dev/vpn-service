from aiogram import F, Router
from aiogram.types import CallbackQuery
from bot.navigation import Navigation
from bot import screens


async def connect(callback: CallbackQuery, navigation: Navigation):
    await navigation.callback(callback, screens.connect())


async def instructions(callback: CallbackQuery, navigation: Navigation):
    await navigation.callback(callback, screens.happ())


def create_router() -> Router:
    router = Router(name="instructions")
    router.callback_query.register(connect, F.data.in_({"nav:connect", "device:add"}))
    router.callback_query.register(instructions, F.data.in_({"nav:happ", "instructions"}))
    return router
