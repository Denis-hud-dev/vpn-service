from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BUY = "💎 Купить VPN"
BUY_COMMANDS = (BUY, "Купить VPN")
CONNECT = "🔑 Подключиться"
ACCOUNT = "🏠 Личный кабинет"
INFO = "ℹ️ Информация"

CONNECT_COMMANDS = (CONNECT, 'Подключиться')
ACCOUNT_COMMANDS = (ACCOUNT, 'Личный кабинет')
INFO_COMMANDS = (INFO, 'Информация')
REPLY_COMMANDS = BUY_COMMANDS + CONNECT_COMMANDS + ACCOUNT_COMMANDS + INFO_COMMANDS


def premium_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=BUY)],
        [KeyboardButton(text=CONNECT)],
        [KeyboardButton(text=ACCOUNT), KeyboardButton(text=INFO)],
    ], resize_keyboard=True, is_persistent=True, input_field_placeholder="KudaVPN Premium")
