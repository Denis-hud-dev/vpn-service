from aiogram import F, Router
from aiogram.types import CallbackQuery
from bot.navigation import Navigation
from bot import screens
from bot.services import Services


async def buy(callback: CallbackQuery, navigation: Navigation):
    await navigation.callback(callback, screens.tariffs())


async def select_tariff(callback: CallbackQuery, navigation: Navigation):
    # Selection is presentation only. Future payment confirmation stays separate.
    await navigation.callback(callback, screens.tariff_details((callback.data or "").partition(":")[2]))


async def subscription(callback: CallbackQuery, services: Services, navigation: Navigation):
    snapshot = await services.subscriptions.snapshot(callback.from_user.id)
    await navigation.callback(callback, screens.account(snapshot))


def create_router() -> Router:
    router = Router(name="subscription")
    router.callback_query.register(buy, F.data.in_({"nav:tariffs", "buy"}))
    router.callback_query.register(select_tariff, F.data.startswith("tariff:"))
    router.callback_query.register(subscription, F.data == "nav:account")
    return router
