from aiogram import F, Router
from aiogram.types import InlineKeyboardButton, Message
from bot.config import Config
from bot.keyboards.main import SUPPORT, back_keyboard

router = Router(name="support")


@router.message(F.text == SUPPORT)
async def support(message: Message, config: Config):
    keyboard = back_keyboard()
    text = "💬 Поддержка KudaVPN\n\nЕсли у вас возник вопрос или проблема, напишите нам."
    if config.support_username:
        keyboard.inline_keyboard.insert(0, [InlineKeyboardButton(text="💬 Написать в поддержку", url=f"https://t.me/{config.support_username}")])
    else:
        text += "\n\nПоддержка скоро будет доступна."
    await message.answer(text, reply_markup=keyboard)
