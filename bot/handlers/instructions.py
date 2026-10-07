from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from bot.keyboards.main import INSTRUCTIONS, back_keyboard

router = Router(name="instructions")
PLATFORMS = {"ios": "🍎 iPhone / iPad", "android": "🤖 Android", "windows": "🪟 Windows", "macos": "💻 macOS"}


@router.message(F.text == INSTRUCTIONS)
async def instructions(message: Message):
    await message.answer("📖 Инструкция KudaVPN\n\nВыберите платформу:",
                         reply_markup=back_keyboard(*((label, f"instruction:{key}") for key, label in PLATFORMS.items())))


@router.callback_query(F.data == "instructions")
async def platforms(callback: CallbackQuery):
    await callback.answer()
    if isinstance(callback.message, Message):
        await instructions(callback.message)


@router.callback_query(F.data.startswith("instruction:"))
async def platform(callback: CallbackQuery):
    label = PLATFORMS.get((callback.data or "").partition(":")[2])
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(f"{label or 'Платформа не найдена'}\n\nИнструкция появится после подключения VPN-сервера.",
                                     reply_markup=back_keyboard(("📖 Выбрать платформу", "instructions")))
