import logging
import asyncio
from pathlib import Path
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import CallbackQuery, Message, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.exc import SQLAlchemyError
from bot.navigation import Navigation
from bot.screens import home
from bot import screens
from bot.keyboards.reply import CONNECT, ACCOUNT, INFO, premium_reply_keyboard
from bot.handlers.instructions import connect_for_user
from bot.config import Config
from bot.services import Services, parse_referrer

logger = logging.getLogger(__name__)
BANNER = Path(__file__).resolve().parents[1] / "assets" / "welcome-premium.png"
WELCOME = ("<b>Добро пожаловать в KudaVPN.</b>\n\n"
           "<b>Свобода быть на связи.</b>\n\n"
           "Твоё пространство в интернете — без лишних границ и сложных настроек.\n\n"
           "Один доступ. Твои устройства. Подключение за несколько шагов.\n\n"
           "<i>KudaVPN Premium — технологии, которые работают для тебя</i>")


async def install_reply_keyboard(bot, user_id: int, services: Services) -> None:
    if not services.pilot_welcome_enabled or services.onboarding is None:
        return
    try:
        sent = await bot.send_message(user_id, "◆ Навигация KudaVPN доступна внизу чата.",
                                      reply_markup=premium_reply_keyboard())
        previous = await services.onboarding.record_keyboard_message(user_id, sent.message_id)
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
        first = await services.onboarding.first_start(user_id, new_user=not existed)
        if first:
            try:
                sent = await message.bot.send_photo(message.chat.id, FSInputFile(BANNER),
                    caption=WELCOME, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[[InlineKeyboardButton(
                            text="🚀 Начать пользоваться", callback_data="pilot:begin")]]))
                await services.onboarding.record_welcome(user_id, sent.message_id)
                return
            except (TelegramAPIError, OSError):
                logger.warning("Pilot welcome unavailable; showing navigation")
        await install_reply_keyboard(message.bot, user_id, services)
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


async def begin(callback: CallbackQuery, navigation: Navigation, services: Services):
    await install_reply_keyboard(callback.bot, callback.from_user.id, services)
    await navigation.show(callback.bot, callback.from_user.id,
                          await home_for_user(services, callback.from_user.id))


async def reply_connect(message: Message, navigation: Navigation, services: Services):
    if services.pilot_welcome_enabled:
        await connect_for_user(message.bot, message.chat.id, message.from_user.id,
                               navigation, services)
    else:
        await navigation.show(message.bot, message.chat.id,
                              await home_for_user(services, message.from_user.id))


async def reply_account(message: Message, navigation: Navigation, services: Services):
    if services.pilot_welcome_enabled:
        snapshot = await services.subscriptions.snapshot(message.from_user.id)
        trial_available = bool(services.trial and
            message.from_user.id == services.trial.owner_telegram_id and snapshot.status == 'inactive')
        await navigation.show(message.bot, message.chat.id, screens.account(snapshot,
            trial_available=trial_available, premium_account=True))
    else:
        await navigation.show(message.bot, message.chat.id,
                              await home_for_user(services, message.from_user.id))


async def reply_info(message: Message, navigation: Navigation, services: Services, config: Config):
    if services.pilot_welcome_enabled:
        await navigation.show(message.bot, message.chat.id,
                              screens.information(config.support_username))
    else:
        await navigation.show(message.bot, message.chat.id,
                              await home_for_user(services, message.from_user.id))


def create_router() -> Router:
    router = Router(name="start")
    router.message.register(start, CommandStart())
    router.message.register(reply_connect, F.text == CONNECT)
    router.message.register(reply_account, F.text == ACCOUNT)
    router.message.register(reply_info, F.text == INFO)
    router.callback_query.register(back, F.data.in_({"nav:home", "menu"}))
    router.callback_query.register(begin, F.data == "pilot:begin")
    return router
