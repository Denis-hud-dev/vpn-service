from aiogram import F, Router
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import CallbackQuery, Message
from bot.keyboards.main import main_menu
from bot.services import Services, parse_referrer

router = Router(name="start")
WELCOME = "Добро пожаловать в KudaVPN 🚀\n\nБыстрый и удобный VPN для ваших устройств.\n\n🔒 Безопасное подключение\n⚡ Высокая скорость\n🌍 Доступ к любимым сервисам\n📱 Поддержка нескольких устройств"


@router.message(CommandStart())
async def start(message: Message, command: CommandObject, services: Services):
    if message.from_user:
        if services.users is not None:
            await services.users.register(message.from_user.id, message.from_user.first_name,
                message.from_user.username, message.from_user.last_name, parse_referrer(command.args))
        else:
            await services.referrals.register(message.from_user.id, parse_referrer(command.args))
    await message.answer(WELCOME, reply_markup=main_menu())


@router.callback_query(F.data == "menu")
async def back(callback: CallbackQuery):
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer("Меню KudaVPN 👇", reply_markup=main_menu())
