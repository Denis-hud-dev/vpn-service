import logging
import asyncio
import sqlite3
from pathlib import Path
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import CallbackQuery, Message, FSInputFile
from sqlalchemy.exc import SQLAlchemyError
from bot.navigation import Navigation
from bot.screens import home
from bot import screens
from bot.keyboards.reply import CONNECT, ACCOUNT, INFO, premium_reply_keyboard
from bot.handlers.instructions import connect_for_user
from bot.handlers.subscription import account_for_user
from bot.config import Config
from bot.services import Services, parse_referrer

logger = logging.getLogger(__name__)
BANNER = Path(__file__).resolve().parents[1] / "assets" / "welcome-premium.png"


async def install_reply_keyboard(bot, user_id: int, services: Services) -> None:
    if not services.pilot_welcome_enabled or services.onboarding is None:
        return
    try:
        sent = await bot.send_message(user_id, "◆ Навигация KudaVPN доступна внизу чата.",
                                      reply_markup=premium_reply_keyboard())
        try:
            previous = await services.onboarding.record_keyboard_message(user_id, sent.message_id)
        except (OSError, sqlite3.Error) as exc:
            # A read-only pilot state directory must not hide the navigation.
            logger.warning("Pilot keyboard state unavailable: %s", type(exc).__name__)
            return
        if previous and previous != sent.message_id:
            try:
                await bot.delete_message(user_id, previous)
            except TelegramAPIError:
                logger.debug("Previous pilot keyboard hint could not be removed")
    except TelegramAPIError:
        logger.warning("Pilot reply keyboard unavailable")


async def home_for_user(services: Services, user_id: int):
    if not services.premium_account_enabled:
        return home()
    try:
        snapshot = await asyncio.wait_for(services.subscriptions.snapshot(user_id), timeout=3)
    except (SQLAlchemyError, TimeoutError):
        # Navigation stays available without presenting an unverified status.
        snapshot = None
    return home(premium_account=True, snapshot=snapshot,
                reply_navigation=services.pilot_welcome_enabled)


async def start(message: Message, command: CommandObject, services: Services, navigation: Navigation):
    user_id = message.from_user.id if message.from_user else message.chat.id
    pilot = bool(services.pilot_welcome_enabled and services.onboarding is not None)
    existed = await services.onboarding.user_exists(user_id) if pilot else True
    if message.from_user:
        if services.users is not None:
            await services.users.register(message.from_user.id, message.from_user.first_name,
                message.from_user.username, message.from_user.last_name, parse_referrer(command.args))
        else:
            await services.referrals.register(message.from_user.id, parse_referrer(command.args))
    if pilot:
        try:
            first = await services.onboarding.first_start(user_id, new_user=not existed)
        except (OSError, sqlite3.Error) as exc:
            # Preserve /start even if the isolated onboarding ledger is unavailable.
            logger.warning("Pilot onboarding state unavailable: %s", type(exc).__name__)
            first = False
        await install_reply_keyboard(message.bot, user_id, services)
        if first:
            try:
                # Preserve the original banner as history. The immediately following
                # tracked text screen holds the live status and all main actions.
                sent = await message.bot.send_photo(message.chat.id, FSInputFile(BANNER))
                await services.onboarding.record_welcome(user_id, sent.message_id)
            except (TelegramAPIError, OSError):
                logger.warning("Pilot welcome unavailable; showing navigation")
    screen = await home_for_user(services, user_id)
    if pilot:
        # Installing ReplyKeyboard sends a new hint. Keep the live menu below
        # that hint even when /start immediately followed the previous menu.
        await navigation.show(message.bot, message.chat.id, screen,
                              verify=True, bring_forward=True)
    else:
        # A cached home screen may have been manually deleted.
        await navigation.message(message, screen, verify=True,
                                 bring_forward=services.premium_account_enabled)
    if not services.premium_account_enabled:
        try:
            await message.delete()
        except TelegramAPIError:
            logger.debug("Start command could not be removed")


async def back(callback: CallbackQuery, navigation: Navigation, services: Services):
    await navigation.callback(callback, await home_for_user(services, callback.from_user.id))


async def begin(callback: CallbackQuery, navigation: Navigation, services: Services):
    await install_reply_keyboard(callback.bot, callback.from_user.id, services)
    await navigation.show(callback.bot, callback.from_user.id,
                          await home_for_user(services, callback.from_user.id))


async def remove_reply_command(message: Message, services: Services) -> None:
    """Delete only this incoming pilot navigation command, never chat history.

    Telegram sends reply-button taps as ordinary text: exact labels are commands.
    Do not delete forwarded messages, bot messages, group messages or other chats.
    No persisted ID is needed; only the validated incoming message is targeted.
    """
    sender = message.from_user
    if (not services.pilot_welcome_enabled or message.chat.type != "private"
            or sender is None or sender.is_bot or message.chat.id != sender.id
            or message.text not in (CONNECT, ACCOUNT, INFO)
            or message.forward_origin is not None or message.sender_chat is not None
            or message.via_bot is not None):
        return
    try:
        await asyncio.wait_for(message.delete(), timeout=3)
    except (TelegramAPIError, TimeoutError) as exc:
        # Deletion is cosmetic; keep navigating even when Telegram refuses it.
        logger.debug("Pilot reply command retained: %s", type(exc).__name__)


async def reply_connect(message: Message, navigation: Navigation, services: Services):
    if services.pilot_welcome_enabled:
        await remove_reply_command(message, services)
        await connect_for_user(message.bot, message.chat.id, message.from_user.id,
                               navigation, services, new_message=True)
    else:
        await navigation.show(message.bot, message.chat.id,
                              await home_for_user(services, message.from_user.id))


async def reply_account(message: Message, navigation: Navigation, services: Services):
    if services.pilot_welcome_enabled:
        await remove_reply_command(message, services)
        await navigation.show(message.bot, message.chat.id,
            await account_for_user(services, message.from_user.id), new_message=True)
    else:
        await navigation.show(message.bot, message.chat.id,
                              await home_for_user(services, message.from_user.id))


async def reply_info(message: Message, navigation: Navigation, services: Services, config: Config):
    if services.pilot_welcome_enabled:
        await remove_reply_command(message, services)
        await navigation.show(message.bot, message.chat.id,
                              screens.information(config.support_username), new_message=True)
    else:
        await navigation.show(message.bot, message.chat.id,
                              await home_for_user(services, message.from_user.id))


async def information(callback: CallbackQuery, navigation: Navigation, config: Config):
    await navigation.callback(callback, screens.information(config.support_username))


def create_router() -> Router:
    router = Router(name="start")
    router.message.register(start, CommandStart())
    router.message.register(reply_connect, F.text == CONNECT)
    router.message.register(reply_account, F.text == ACCOUNT)
    router.message.register(reply_info, F.text == INFO)
    router.callback_query.register(information, F.data == "nav:info")
    router.callback_query.register(back, F.data.in_({"nav:home", "menu"}))
    router.callback_query.register(begin, F.data == "pilot:begin")
    return router
