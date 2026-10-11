from aiogram import F, Router
from aiogram.types import CallbackQuery
from bot.navigation import Navigation
from bot import screens


async def referral(callback: CallbackQuery, navigation: Navigation):
    await navigation.callback(callback, screens.bonuses())


def create_router() -> Router:
    router = Router(name="referral")
    router.callback_query.register(referral, F.data == "nav:bonuses")
    return router
