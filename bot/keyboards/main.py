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


def button(text: str, destination: str, *, primary: bool = False) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=destination,
                                style="primary" if primary else None)


def main_menu(*, premium_account: bool = False,
              reply_navigation: bool = False) -> InlineKeyboardMarkup:
    if premium_account:
        # Catalog only: no external-provider purchase is initiated in Telegram.
        rows = [
            [button("💎 Купить VPN", "nav:tariffs")],
            [button("◈ Моя подписка", "nav:account")],
            [button("🚀 Подключиться через Happ", "nav:connect", primary=True)],
            [button("💬 Поддержка", "nav:help")],
        ]
        return InlineKeyboardMarkup(inline_keyboard=rows)
    return InlineKeyboardMarkup(inline_keyboard=[
        [button(CONNECT, "nav:connect")],
        [button(SUBSCRIPTION, "nav:account"), button(BUY, "nav:tariffs")],
        [button(TRAFFIC, "nav:traffic"), button(REFERRAL, "nav:bonuses")],
        [button(SUPPORT, "nav:support")],
    ])


def back_keyboard(*buttons: tuple[str, str], back: str = "nav:home",
                  primary: str | None = None) -> InlineKeyboardMarkup:
    rows = [[button(text, data, primary=data == primary)] for text, data in buttons]
    rows.append([button("⬅️ Назад", back)])
    if back != "nav:home":
        rows.append([button("⌂ Главное меню", "nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
