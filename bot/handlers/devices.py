from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from bot.keyboards.main import DEVICES, back_keyboard

router = Router(name="devices")


@router.message(F.text == DEVICES)
async def devices(message: Message):
    await message.answer("📱 Ваши устройства\n\nУ вас пока нет подключённых устройств.\n\nПосле покупки подписки здесь можно будет подключить:\n• iPhone / iPad\n• Android\n• Windows\n• macOS",
                         reply_markup=back_keyboard(("➕ Добавить устройство", "device:add")))


@router.callback_query(F.data == "device:add")
async def add_device(callback: CallbackQuery):
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer("Для подключения устройства нужна активная подписка. VPN-сервер скоро будет подключён.",
                                     reply_markup=back_keyboard(("💎 Купить подписку", "buy")))
