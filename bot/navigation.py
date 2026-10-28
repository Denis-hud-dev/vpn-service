"""Persisted navigation: edit one screen, replace only on permanent Telegram refusal."""
import asyncio
import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Awaitable, Callable
from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, Message, LinkPreviewOptions, InlineKeyboardMarkup
from sqlalchemy.exc import SQLAlchemyError
from bot.screens import Screen, database_error
from bot.premium_emoji import decorate as decorate_emoji
from bot.services.navigation import MemoryScreenStore, ScreenStore

logger = logging.getLogger(__name__)


@dataclass
class View:
    message_id: int
    screen: Screen | None = None


class EditResult(Enum):
    UPDATED = auto()
    REPLACE = auto()
    RETRY = auto()


class Navigation:
    def __init__(self, store: ScreenStore | None = None, *, custom_emoji: bool = False):
        self.store = store or MemoryScreenStore()
        self.custom_emoji = custom_emoji
        self.button_styles = True
        self.views: dict[int, View] = {}
        self.locks: dict[int, asyncio.Lock] = {}

    def lock(self, chat_id: int) -> asyncio.Lock:
        return self.locks.setdefault(chat_id, asyncio.Lock())

    async def load(self, chat_id: int) -> View | None:
        if chat_id not in self.views:
            saved = await self.store.load(chat_id)
            if saved and saved.chat_id == chat_id:
                self.views.setdefault(chat_id, View(saved.message_id))
        return self.views.get(chat_id)

    async def remember(self, chat_id: int, message_id: int, screen: Screen | None = None) -> None:
        await self.store.save(chat_id, chat_id, message_id)
        self.views[chat_id] = View(message_id, screen)

    async def edit(self, bot, chat_id: int, message_id: int, screen: Screen, *, verify: bool = False,
                   plain: Screen | None = None) -> EditResult:
        view = self.views.get(chat_id)
        if not verify and view and view.message_id == message_id and view.screen == screen:
            return EditResult.UPDATED
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=screen.text,
                parse_mode="HTML", reply_markup=screen.keyboard,
                link_preview_options=LinkPreviewOptions(is_disabled=True))
        except TelegramBadRequest as exc:
            reason = exc.message.lower()
            if "message is not modified" not in reason:
                logger.warning("Screen edit rejected: %s", type(exc).__name__)
                permanent = ("message to edit not found", "message can't be edited", "message cannot be edited",
                             "message_id_invalid", "message edit time expired")
                if any(item in reason for item in permanent):
                    return EditResult.REPLACE
                if plain is not None and self._emoji_rejection(reason):
                    self.custom_emoji = False
                    return await self.edit(bot, chat_id, message_id, plain, verify=True)
                if self._style_rejection(reason) and self._has_button_style(screen):
                    self.button_styles = False
                    return await self.edit(bot, chat_id, message_id, self._without_button_styles(screen),
                                           verify=True,
                                           plain=self._without_button_styles(plain) if plain else None)
                return EditResult.RETRY
        except TelegramAPIError as exc:
            # Network/rate-limit errors do not prove the old message is unusable.
            logger.warning("Screen edit unavailable: %s", type(exc).__name__)
            return EditResult.RETRY
        self.views[chat_id] = View(message_id, screen)
        return EditResult.UPDATED

    @staticmethod
    def _emoji_rejection(reason: str) -> bool:
        return any(term in reason for term in ("custom emoji", "custom_emoji", "premium",
            "unsupported start tag", "can't parse entities"))

    @staticmethod
    def _style_rejection(reason: str) -> bool:
        return any(term in reason for term in ("button style", "unsupported style", "invalid style"))

    @staticmethod
    def _has_button_style(screen: Screen) -> bool:
        return any(button.style for row in screen.keyboard.inline_keyboard for button in row)

    @staticmethod
    def _without_button_styles(screen: Screen) -> Screen:
        rows = [[button.model_copy(update={"style": None}) for button in row]
                for row in screen.keyboard.inline_keyboard]
        return Screen(screen.text, InlineKeyboardMarkup(inline_keyboard=rows))

    async def replace(self, bot, chat_id: int, screen: Screen, previous: View | None,
                      *, plain: Screen | None = None) -> None:
        try:
            sent = await bot.send_message(chat_id=chat_id, text=screen.text, parse_mode="HTML",
                                          reply_markup=screen.keyboard,
                                          link_preview_options=LinkPreviewOptions(is_disabled=True))
        except TelegramBadRequest as exc:
            if self._style_rejection(exc.message.lower()) and self._has_button_style(screen):
                self.button_styles = False
                logger.warning("Styled buttons unavailable; using plain pilot navigation")
                return await self.replace(bot, chat_id, self._without_button_styles(screen), previous,
                                          plain=self._without_button_styles(plain) if plain else None)
            if plain is None or not self._emoji_rejection(exc.message.lower()):
                logger.warning("Navigation screen send unavailable: %s", type(exc).__name__)
                return
            self.custom_emoji = False
            logger.warning("Custom emoji unavailable; using plain pilot navigation")
            return await self.replace(bot, chat_id, plain, previous)
        except TelegramAPIError as exc:
            logger.warning("Navigation screen send unavailable: %s", type(exc).__name__)
            return
        try:
            # Persist the new pointer before removing anything. On DB failure,
            # roll back the new Telegram screen best-effort, retaining the old ID.
            await self.remember(chat_id, sent.message_id, screen)
        except SQLAlchemyError:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=sent.message_id)
            except TelegramAPIError:
                logger.warning("Unpersisted navigation screen could not be removed")
            raise
        if previous and previous.message_id != sent.message_id:
            try:
                # This exact ID was recorded as navigation, never arbitrary history.
                await bot.delete_message(chat_id=chat_id, message_id=previous.message_id)
            except TelegramAPIError:
                # Telegram may refuse to delete an old bot message. Retire only
                # the known navigation screen so its stale buttons and any old
                # personal connection link are no longer usable or visible.
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id, message_id=previous.message_id,
                        text="Меню обновлено. Используйте последнее сообщение бота ниже.",
                        link_preview_options=LinkPreviewOptions(is_disabled=True),
                    )
                except TelegramAPIError:
                    logger.info("Previous navigation screen could not be retired")

    async def show(self, bot, chat_id: int, screen: Screen, *, verify: bool = False,
                   bring_forward: bool = False, new_message: bool = False) -> None:
        plain = screen
        if self.custom_emoji:
            screen = decorate_emoji(screen)
        if not self.button_styles:
            screen = self._without_button_styles(screen)
            plain = self._without_button_styles(plain)
        previous = await self.load(chat_id)
        if previous and not (bring_forward or new_message):
            result = await self.edit(bot, chat_id, previous.message_id, screen, verify=verify,
                                     plain=plain if screen != plain else None)
            if result != EditResult.REPLACE:
                return
        await self.replace(bot, chat_id, screen, previous,
                           plain=plain if self.custom_emoji and screen != plain else None)

    async def callback(self, query: CallbackQuery, screen: Screen) -> None:
        if isinstance(query.message, Message):
            await self.show(query.bot, query.message.chat.id, screen)

    async def message(self, message: Message, screen: Screen, *, verify: bool = False,
                      bring_forward: bool = False) -> None:
        previous = await self.load(message.chat.id) if bring_forward else None
        # An old editable menu can be several screens above a new /start command.
        # Replace that navigation screen only when newer chat activity hides it.
        stale_position = bool(previous and message.message_id > previous.message_id + 1)
        await self.show(message.bot, message.chat.id, screen, verify=verify,
                        bring_forward=stale_position)

    @staticmethod
    def is_navigation(message: Message) -> bool:
        # Bootstrap only recognisable Premium screens from the pre-persistence
        # release. Payment receipts, notifications and support conversations stay.
        text = (message.text or "").replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        titles = {"Свобода начинается здесь.", "Ваше пространство подключения.", "Свобода подключения. Простота управления.",
                  "👤 Личный кабинет", "📱 Устройства", "💎 Тарифы",
                  "💎 Ваш выбор", "🚀 Подключить VPN", "📖 Настройка Happ", "📊 Мой трафик",
                  "🎁 Бонусы", "💬 Поддержка", "❔ Помощь", "📱 Мои устройства",
                  "◈ Навигация", "◈ Сервис временно недоступен"}
        titles.update({"🛡️ Ваше пространство подключения.", "🎫 Личный кабинет",
                       "📊 Тарифы", "📊 Ваш выбор", "🔗 Подключить VPN",
                       "🔗 Настройка Happ", "🎧 Помощь", "📱 Мои устройства",
                       "💳 Тестовый заказ", "⚡ Мой трафик", "🎧 Поддержка",
                       "◈ Kuda Smart"})
        return bool(message.from_user and message.from_user.id == message.bot.id and message.reply_markup
                    and len(lines) > 1 and lines[0] in {"KudaVPN Premium", "💎 KudaVPN Premium"}
                    and lines[1] in titles)


class NavigationMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[..., Awaitable[Any]], event, data: dict[str, Any]):
        navigation: Navigation = data["navigation"]
        callback = isinstance(event, CallbackQuery)
        message = event.message if callback else event
        if not isinstance(message, Message) or message.chat.type != "private":
            if callback:
                try:
                    await event.answer("Откройте личный чат с ботом и отправьте /start.")
                except TelegramAPIError:
                    logger.info("Callback acknowledgement unavailable")
            else:
                await event.answer("Откройте личный чат с KudaVPN для управления подпиской.")
            return
        if callback:
            if event.data == "pilot:begin":
                onboarding = getattr(data.get("services"), "onboarding", None)
                valid = bool(onboarding and await onboarding.valid_welcome(
                    event.from_user.id, message.message_id))
                try:
                    await event.answer(None if valid else "Это приветствие больше не активно. Отправьте /start.")
                except TelegramAPIError:
                    logger.info("Callback acknowledgement unavailable")
                if not valid:
                    return
                async with navigation.lock(message.chat.id):
                    return await handler(event, data)
            # Fast cached check; after restart one bounded DB read identifies stale
            # callbacks before their single acknowledgement.
            try:
                view = await asyncio.wait_for(navigation.load(message.chat.id), timeout=3)
            except (SQLAlchemyError, TimeoutError):
                try:
                    await event.answer("Данные временно недоступны. Попробуйте позже.")
                except TelegramAPIError:
                    logger.info("Callback acknowledgement unavailable")
                return
            stale = bool(view and view.message_id != message.message_id)
            unknown = not view and not navigation.is_navigation(message)
            try:
                await event.answer("Этот экран устарел. Используйте последнее меню или /start." if stale or unknown else None)
            except TelegramAPIError:
                logger.info("Callback acknowledgement unavailable")
            if stale or unknown:
                return
        async with navigation.lock(message.chat.id):
            try:
                view = await navigation.load(message.chat.id)
                if callback:
                    if view and view.message_id != message.message_id:
                        return
                    if not view:
                        await navigation.remember(message.chat.id, message.message_id)
                return await handler(event, data)
            except SQLAlchemyError as exc:
                logger.error("Database operation failed: %s", type(exc).__name__)
                # Never create an untracked screen during a storage outage.
                view = navigation.views.get(message.chat.id)
                if view:
                    await navigation.edit(message.bot, message.chat.id, view.message_id, database_error())
