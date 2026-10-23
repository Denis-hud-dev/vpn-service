from aiogram import F, Router
from aiogram.types import CallbackQuery
from bot.navigation import Navigation
from bot import screens
from bot.services import Services
from bot.services.happ import AccessDenied


async def connect(callback: CallbackQuery, navigation: Navigation, services: Services):
    link = None
    page_url = None
    private_chat = getattr(getattr(callback.message, 'chat', None), 'type', None) == 'private'
    if private_chat and services.happ is not None:
        try:
            link = await services.happ.issue_for_user(callback.from_user.id)
            if (services.pilot_connect_page_enabled and services.trial is not None
                    and callback.from_user.id == services.trial.owner_telegram_id):
                page_url = services.happ.connection_page_url(link)
        except AccessDenied:
            pass
    await navigation.callback(callback, screens.connect(
        link, page_url=page_url, premium_account=services.premium_account_enabled))


async def instructions(callback: CallbackQuery, navigation: Navigation):
    await navigation.callback(callback, screens.happ())


def create_router() -> Router:
    router = Router(name="instructions")
    router.callback_query.register(connect, F.data.in_({"nav:connect", "device:add"}))
    router.callback_query.register(instructions, F.data.in_({"nav:happ", "instructions"}))
    return router
