"""Persisted navigation: edit one screen, replace only on permanent Telegram refusal."""
import asyncio
import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Awaitable, Callable
from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, Message, LinkPreviewOptions
from sqlalchemy.exc import SQLAlchemyError
from bot.screens import Screen, database_error
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
    def __init__(self, store: ScreenStore | None = None):
        self.store = store or MemoryScreenStore()
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

    async def edit(self, bot, chat_id: int, message_id: int, screen: Screen, *, verify: bool = False) -> EditResult:
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
                return EditResult.REPLACE if any(item in reason for item in permanent) else EditResult.RETRY
        except TelegramAPIError as exc:
            # Network/rate-limit errors do not prove the old message is unusable.
            logger.warning("Screen edit unavailable: %s", type(exc).__name__)
            return EditResult.RETRY
        self.views[chat_id] = View(message_id, screen)
        return EditResult.UPDATED

    async def replace(self, bot, chat_id: int, screen: Screen, previous: View | None) -> None:
        try:
            sent = await bot.send_message(chat_id=chat_id, text=screen.text, parse_mode="HTML",
                                          reply_markup=screen.keyboard,
                                          link_preview_options=LinkPreviewOptions(is_disabled=True))
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
                logger.info("Previous navigation screen could not be removed")

    async def show(self, bot, chat_id: int, screen: Screen, *, verify: bool = False) -> None:
        previous = await self.load(chat_id)
        if previous:
            result = await self.edit(bot, chat_id, previous.message_id, screen, verify=verify)
            if result != EditResult.REPLACE:
                return
        await self.replace(bot, chat_id, screen, previous)

    async def callback(self, query: CallbackQuery, screen: Screen) -> None:
        if isinstance(query.message, Message):
            await self.show(query.bot, query.message.chat.id, screen)

    async def message(self, message: Message, screen: Screen, *, verify: bool = False) -> None:
        await self.show(message.bot, message.chat.id, screen, verify=verify)

    @staticmethod
    def is_navigation(message: Message) -> bool:
        # Bootstrap only recognisable Premium screens from the pre-persistence
        # release. Payment receipts, notifications and support conversations stay.
        text = (message.text or "").replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        titles = {"Ваше пространство подключения.", "👤 Личный кабинет", "📱 Устройства", "💎 Тарифы",
                  "💎 Ваш выбор", "🚀 Подключить VPN", "📖 Настройка Happ", "📊 Мой трафик",
                  "🎁 Бонусы", "💬 Поддержка", "◈ Навигация", "◈ Сервис временно недоступен"}
        return bool(message.from_user and message.from_user.id == message.bot.id and message.reply_markup
                    and len(lines) > 1 and lines[0] == "KudaVPN Premium" and lines[1] in titles)


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
