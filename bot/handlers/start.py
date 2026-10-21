import logging
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import CallbackQuery, Message
from bot.navigation import Navigation
from bot.screens import home
from bot.services import Services, parse_referrer

logger = logging.getLogger(__name__)


async def start(message: Message, command: CommandObject, services: Services, navigation: Navigation):
    if message.from_user:
        if services.users is not None:
            await services.users.register(message.from_user.id, message.from_user.first_name,
                message.from_user.username, message.from_user.last_name, parse_referrer(command.args))
        else:
            await services.referrals.register(message.from_user.id, parse_referrer(command.args))
    # Always ask Telegram: a cached home screen may have been manually deleted.
    await navigation.message(message, home(premium_account=services.premium_account_enabled), verify=True)
    try:
        await message.delete()
    except TelegramAPIError:
        # Removing a user's command is cosmetic, not an application failure.
        logger.debug("Start command could not be removed")


async def back(callback: CallbackQuery, navigation: Navigation, services: Services):
    await navigation.callback(callback, home(premium_account=services.premium_account_enabled))


def create_router() -> Router:
    router = Router(name="start")
    router.message.register(start, CommandStart())
    router.callback_query.register(back, F.data.in_({"nav:home", "menu"}))
    return router
