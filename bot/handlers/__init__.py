from aiogram import Router
from aiogram.types import CallbackQuery, Message
from bot.handlers import start, subscription, devices, instructions, referral, support
from bot.keyboards.main import main_menu


def create_router() -> Router:
    router = Router(name="kudavpn")
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
