from aiogram import F, Router
from aiogram.types import Message
from bot.keyboards.main import REFERRAL, back_keyboard

router = Router(name="referral")


@router.message(F.text == REFERRAL)
async def referral(message: Message, bot_username: str):
    if message.from_user:
        link = f"https://t.me/{bot_username}?start=ref_{message.from_user.id}"
        await message.answer(f"🎁 Приглашайте друзей в KudaVPN\n\nЗа приглашённых пользователей в будущем будут начисляться бонусные дни.\n\nВаша персональная ссылка:\n{link}", reply_markup=back_keyboard())
