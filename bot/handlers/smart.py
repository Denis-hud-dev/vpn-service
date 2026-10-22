"""Kuda Smart preview is reachable only in the isolated Premium bot."""
from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.navigation import Navigation
from bot.screens import home, kuda_smart
from bot.services import Services


async def show(callback: CallbackQuery, navigation: Navigation, services: Services):
    screen = kuda_smart() if services.premium_account_enabled else home()
    await navigation.callback(callback, screen)


def create_router() -> Router:
    router = Router(name="smart")
    router.callback_query.register(show, F.data == "nav:smart")
    return router
