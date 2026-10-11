"""Compact inline navigation; callback data stays well below 64 bytes."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CONNECT = "🚀 Подключить VPN"
SUBSCRIPTION = "👤 Личный кабинет"
BUY = "💎 Тарифы"
TRAFFIC = "📊 Мой трафик"
REFERRAL = "🎁 Бонусы"
SUPPORT = "💬 Поддержка"
# Kept for old reply-keyboard messages after the UI upgrade.
DEVICES = "📱 Мои устройства"
INSTRUCTIONS = "📖 Инструкция"


def button(text: str, destination: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=destination)


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [button(CONNECT, "nav:connect")],
        [button(SUBSCRIPTION, "nav:account"), button(BUY, "nav:tariffs")],
        [button(TRAFFIC, "nav:traffic"), button(REFERRAL, "nav:bonuses")],
        [button(SUPPORT, "nav:support")],
    ])


def back_keyboard(*buttons: tuple[str, str], back: str = "nav:home") -> InlineKeyboardMarkup:
    rows = [[button(text, data)] for text, data in buttons]
    rows.append([button("⬅️ Назад", back)])
    if back != "nav:home":
        rows.append([button("⌂ Главное меню", "nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
