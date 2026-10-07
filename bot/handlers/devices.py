from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from bot.keyboards.main import DEVICES, back_keyboard
from bot.services import Services

router = Router(name="devices")


@router.message(F.text == DEVICES)
async def devices(message: Message, services: Services):
    if message.from_user:
        await message.answer(await services.devices.describe(message.from_user.id),
                             reply_markup=back_keyboard(("➕ Добавить устройство", "device:add")))


@router.callback_query(F.data == "device:add")
async def add_device(callback: CallbackQuery, services: Services):
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(await services.devices.connection_message(callback.from_user.id),
                                     reply_markup=back_keyboard(("💎 Купить подписку", "buy")))
