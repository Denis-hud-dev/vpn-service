from aiogram import F, Router
from aiogram.types import CallbackQuery
from bot.navigation import Navigation
from bot import screens
from bot.services import Services
from bot.services.happ import AccessDenied


async def connect(callback: CallbackQuery, navigation: Navigation, services: Services):
    await connect_for_user(callback.bot, callback.message.chat.id, callback.from_user.id,
                           navigation, services)


async def connect_for_user(bot, chat_id: int, user_id: int, navigation: Navigation, services: Services,
                           *, new_message: bool = False):
    link = None
    page_url = None
    if services.happ is not None:
        try:
            link = await services.happ.issue_for_user(user_id)
            if (services.pilot_connect_page_enabled and services.trial is not None
                    and user_id == services.trial.owner_telegram_id):
                page_url = services.happ.connection_page_url(link)
        except AccessDenied:
            pass
    await navigation.show(bot, chat_id, screens.connect(
        link, page_url=page_url, premium_account=services.premium_account_enabled),
        new_message=new_message)


async def instructions(callback: CallbackQuery, navigation: Navigation):
    await navigation.callback(callback, screens.happ())


def create_router() -> Router:
    router = Router(name="instructions")
    router.callback_query.register(connect, F.data.in_({"nav:connect", "device:add"}))
    router.callback_query.register(instructions, F.data.in_({"nav:happ", "instructions"}))
    return router
