from aiogram import Router
from aiogram.types import CallbackQuery, Message
from bot.handlers import start, subscription, devices, instructions, referral, support, traffic, smart
from bot.navigation import Navigation, NavigationMiddleware
from bot.screens import home, panel
from bot.observability import UpdateTimingMiddleware
from bot.services import Services


def create_router() -> Router:
    router = Router(name="kudavpn")
    router.message.outer_middleware(UpdateTimingMiddleware())
    router.callback_query.outer_middleware(UpdateTimingMiddleware())
    router.message.outer_middleware(NavigationMiddleware())
    router.callback_query.outer_middleware(NavigationMiddleware())
    for module in (start, subscription, devices, instructions, referral, support, traffic, smart):
        router.include_router(module.create_router())
    fallback = Router(name="fallback")

    @fallback.message()
    async def unknown(message: Message, navigation: Navigation, services: Services):
        # Old reply keyboard buttons and arbitrary text return to the inline menu.
        await navigation.message(message, home(premium_account=services.premium_account_enabled))

    @fallback.callback_query()
    async def unknown_callback(callback: CallbackQuery, navigation: Navigation):
        await navigation.callback(callback, panel("◈ Навигация", "Кнопка устарела. Вернитесь в главное меню."))

    router.include_router(fallback)
    return router
