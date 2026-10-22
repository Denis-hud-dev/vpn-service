from aiogram import F, Router
from aiogram.types import CallbackQuery
from bot.navigation import Navigation
from bot import screens
from bot.services import Services


async def referral(callback: CallbackQuery, navigation: Navigation, services: Services):
    await navigation.callback(callback, screens.invite() if services.premium_account_enabled else screens.bonuses())


def create_router() -> Router:
    router = Router(name="referral")
    router.callback_query.register(referral, F.data.in_({"nav:bonuses", "nav:invite"}))
    return router
