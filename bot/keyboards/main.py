from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

CONNECT = "🚀 Подключить VPN"
BUY = "💎 Купить / продлить"
SUBSCRIPTION = "👤 Моя подписка"
DEVICES = "📱 Мои устройства"
REFERRAL = "🎁 Пригласить друга"
INSTRUCTIONS = "📖 Инструкция"
SUPPORT = "💬 Поддержка"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=CONNECT)], [KeyboardButton(text=BUY)],
        [KeyboardButton(text=SUBSCRIPTION), KeyboardButton(text=DEVICES)],
        [KeyboardButton(text=REFERRAL)],
        [KeyboardButton(text=INSTRUCTIONS), KeyboardButton(text=SUPPORT)],
    ], resize_keyboard=True, input_field_placeholder="Выберите действие KudaVPN")


def back_keyboard(*buttons: tuple[str, str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=text, callback_data=data)] for text, data in buttons]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
