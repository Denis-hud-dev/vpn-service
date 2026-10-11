from html import escape
from aiogram import F, Router
from aiogram.types import CallbackQuery
from bot.keyboards.main import back_keyboard
from bot.navigation import Navigation
from bot.screens import panel
from bot.services import Services


async def devices(callback: CallbackQuery, services: Services, navigation: Navigation):
    text = await services.devices.describe(callback.from_user.id)
    body = text.partition("\n\n")[2] or text
    if len(body) > 2400:
        body = body[:2400] + "\n…"
    await navigation.callback(callback, panel("📱 Устройства", escape(body), back_keyboard(back="nav:account")))


def create_router() -> Router:
    router = Router(name="devices")
    router.callback_query.register(devices, F.data == "nav:devices")
    return router
