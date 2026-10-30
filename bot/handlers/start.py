import logging
import asyncio
import sqlite3
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import CallbackQuery, Message, FSInputFile
from sqlalchemy.exc import SQLAlchemyError
from bot.navigation import Navigation
from bot.screens import home
from bot import screens
from bot.keyboards.reply import (CONNECT, ACCOUNT, INFO, CONNECT_COMMANDS, ACCOUNT_COMMANDS,
                                 INFO_COMMANDS, BUY_COMMANDS, REPLY_COMMANDS, premium_reply_keyboard)
from bot.premium_emoji import decorate_reply
from bot.handlers.instructions import connect_for_user
from bot.handlers.subscription import account_for_user
from bot.config import Config
from bot.services import Services, parse_referrer
from bot.branding import WELCOME_BANNER

logger = logging.getLogger(__name__)
BANNER = WELCOME_BANNER


async def install_reply_keyboard(bot, user_id: int, services: Services, *, custom_emoji: bool = False) -> None:
    if not services.pilot_welcome_enabled or services.onboarding is None:
        return
    try:
        greeting = await home_for_user(services, user_id)
        plain = premium_reply_keyboard()
        keyboard = decorate_reply(plain) if custom_emoji else plain
        try:
            sent = await bot.send_message(user_id, greeting.text, parse_mode="HTML", reply_markup=keyboard)
        except TelegramBadRequest as exc:
            if not custom_emoji or not Navigation._emoji_rejection(exc.message.lower()):
                raise
            # Server-side capability rejection: retain the original Unicode labels.
            sent = await bot.send_message(user_id, greeting.text, parse_mode="HTML", reply_markup=plain)
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
        welcomed = False
        if first:
            try:
                # Preserve the original banner as history. The immediately following
                # tracked text screen holds the inline main actions.
                keyboard = premium_reply_keyboard()
                try:
                    sent = await message.bot.send_photo(message.chat.id, FSInputFile(BANNER),
                        caption=screens.first_welcome_text(), parse_mode="HTML",
                        reply_markup=decorate_reply(keyboard) if navigation.custom_emoji else keyboard)
                except TelegramBadRequest as exc:
                    if not navigation.custom_emoji or not Navigation._emoji_rejection(exc.message.lower()):
                        raise
                    sent = await message.bot.send_photo(message.chat.id, FSInputFile(BANNER),
                        caption=screens.first_welcome_text(), parse_mode="HTML", reply_markup=keyboard)
                await services.onboarding.record_welcome(user_id, sent.message_id)
                welcomed = True
            except (TelegramAPIError, OSError):
                logger.warning("Pilot welcome unavailable; showing navigation")
        if not welcomed:
            await install_reply_keyboard(message.bot, user_id, services, custom_emoji=navigation.custom_emoji)
    screen = screens.main_actions() if pilot else await home_for_user(services, user_id)
    if pilot:
        # A meaningful greeting carries ReplyKeyboard; this message carries inline actions.
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
    await install_reply_keyboard(callback.bot, callback.from_user.id, services,
                                 custom_emoji=navigation.custom_emoji)
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
            or message.text not in REPLY_COMMANDS
            or message.forward_origin is not None or message.sender_chat is not None
            or message.via_bot is not None):
        return
    try:
        await asyncio.wait_for(message.delete(), timeout=3)
    except (TelegramAPIError, TimeoutError) as exc:
        # Deletion is cosmetic; keep navigating even when Telegram refuses it.
        logger.debug("Pilot reply command retained: %s", type(exc).__name__)


async def reply_buy(message: Message, navigation: Navigation, services: Services):
    if services.pilot_welcome_enabled:
        await remove_reply_command(message, services)
        await navigation.show(message.bot, message.chat.id,
                              screens.tariffs(premium_account=True), new_message=True)
    else:
        await navigation.show(message.bot, message.chat.id,
                              await home_for_user(services, message.from_user.id))


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
    router.message.register(reply_buy, F.text.in_(BUY_COMMANDS))
    router.message.register(reply_connect, F.text.in_(CONNECT_COMMANDS))
    router.message.register(reply_account, F.text.in_(ACCOUNT_COMMANDS))
    router.message.register(reply_info, F.text.in_(INFO_COMMANDS))
    router.callback_query.register(information, F.data == "nav:info")
    router.callback_query.register(back, F.data.in_({"nav:home", "menu"}))
    router.callback_query.register(begin, F.data == "pilot:begin")
    return router
