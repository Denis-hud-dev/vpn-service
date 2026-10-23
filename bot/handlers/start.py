import logging
import asyncio
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import CallbackQuery, Message
from sqlalchemy.exc import SQLAlchemyError
from bot.navigation import Navigation
from bot.screens import home
from bot.services import Services, parse_referrer

logger = logging.getLogger(__name__)


async def home_for_user(services: Services, user_id: int):
    if not services.premium_account_enabled:
        return home()
    try:
        snapshot = await asyncio.wait_for(services.subscriptions.snapshot(user_id), timeout=3)
    except (SQLAlchemyError, TimeoutError):
        # Navigation stays available without presenting an unverified status.
        snapshot = None
    return home(premium_account=True, snapshot=snapshot)


async def start(message: Message, command: CommandObject, services: Services, navigation: Navigation):
    if message.from_user:
        if services.users is not None:
            await services.users.register(message.from_user.id, message.from_user.first_name,
                message.from_user.username, message.from_user.last_name, parse_referrer(command.args))
        else:
            await services.referrals.register(message.from_user.id, parse_referrer(command.args))
    # Always ask Telegram: a cached home screen may have been manually deleted.
    await navigation.message(message, await home_for_user(
        services, message.from_user.id if message.from_user else message.chat.id), verify=True,
        bring_forward=services.premium_account_enabled)
    if not services.premium_account_enabled:
        try:
            await message.delete()
        except TelegramAPIError:
            logger.debug("Start command could not be removed")


async def back(callback: CallbackQuery, navigation: Navigation, services: Services):
    await navigation.callback(callback, await home_for_user(services, callback.from_user.id))


def create_router() -> Router:
    router = Router(name="start")
    router.message.register(start, CommandStart())
    router.callback_query.register(back, F.data.in_({"nav:home", "menu"}))
    return router
