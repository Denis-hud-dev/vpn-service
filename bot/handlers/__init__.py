from aiogram import Router
from aiogram.types import CallbackQuery, Message, ErrorEvent
from aiogram.filters import ExceptionTypeFilter
from sqlalchemy.exc import SQLAlchemyError
import logging
from bot.handlers import start, subscription, devices, instructions, referral, support
from bot.keyboards.main import main_menu


def create_router() -> Router:
    router = Router(name="kudavpn")

    @router.error(ExceptionTypeFilter(SQLAlchemyError))
    async def database_error(event: ErrorEvent):
        logging.getLogger(__name__).error("Database operation failed: %s", type(event.exception).__name__)
        text = "База данных временно недоступна. Попробуйте позже."
        if event.update.message:
            await event.update.message.answer(text)
        elif event.update.callback_query and isinstance(event.update.callback_query.message, Message):
            await event.update.callback_query.message.answer(text)
        return True

    for module in (start, subscription, devices, instructions, referral, support):
        router.include_router(module.router)
    fallback = Router(name="fallback")

    @fallback.message()
    async def unknown(message: Message):
        await message.answer("Я не понял команду. Используйте меню KudaVPN 👇", reply_markup=main_menu())

    @fallback.callback_query()
    async def unknown_callback(callback: CallbackQuery):
        await callback.answer("Кнопка устарела. Откройте меню через /start.")

    router.include_router(fallback)
    return router
