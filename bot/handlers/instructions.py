from aiogram import F, Router
from aiogram.types import CallbackQuery
from bot.navigation import Navigation
from bot import screens
from bot.services import Services
from bot.services.happ import AccessDenied


async def connect(callback: CallbackQuery, navigation: Navigation, services: Services):
    link = None
    if services.happ is not None:
        try:
            link = await services.happ.issue_for_user(callback.from_user.id)
        except AccessDenied:
            pass
    await navigation.callback(callback, screens.connect(link))


async def instructions(callback: CallbackQuery, navigation: Navigation):
    await navigation.callback(callback, screens.happ())


def create_router() -> Router:
    router = Router(name="instructions")
    router.callback_query.register(connect, F.data.in_({"nav:connect", "device:add"}))
    router.callback_query.register(instructions, F.data.in_({"nav:happ", "instructions"}))
    return router
