from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

CONNECT = "🔑 Подключиться"
ACCOUNT = "🏠 Личный кабинет"
INFO = "ℹ️ Информация"


def premium_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=CONNECT)],
        [KeyboardButton(text=ACCOUNT), KeyboardButton(text=INFO)],
    ], resize_keyboard=True, is_persistent=True, input_field_placeholder="KudaVPN Premium")
