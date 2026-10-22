from aiogram import F, Router
from aiogram.types import CallbackQuery
from bot.navigation import Navigation
from bot import screens
from bot.services import Services


async def traffic(callback: CallbackQuery, navigation: Navigation, services: Services):
    snapshot = await services.subscriptions.snapshot(callback.from_user.id)
    await navigation.callback(callback, screens.traffic(snapshot))


def create_router() -> Router:
    router = Router(name="traffic")
    router.callback_query.register(traffic, F.data == "nav:traffic")
    return router
