"""Opt-in Telegram custom emoji for the isolated Premium pilot bot.

IDs come from Telegram's getStickerSet('KudaVPNPremium') response. The pack is
public; no bot credential or personal subscription is stored here. Plain text
labels remain meaningful if a Telegram client does not display button icons.
"""
from dataclasses import dataclass

from aiogram.types import InlineKeyboardMarkup

from bot.screens import Screen


@dataclass(frozen=True)
class Emoji:
    id: str
    alt: str


PACK_NAME = "KudaVPNPremium"
EMOJI = {
    "connect": Emoji("5389102861672032499", "🔗"),
    "protect": Emoji("5388843759179966039", "🛡️"),
    "premium": Emoji("5389052035029051661", "💎"),
    "subscription": Emoji("5388799654160802152", "🎫"),
    "plans": Emoji("5388557190372042096", "📊"),
    "devices": Emoji("5386482673923432320", "📱"),
    "payment": Emoji("5388594440623400247", "💳"),
    "renew": Emoji("5388740340662446382", "🔄"),
    "support": Emoji("5386862739874425252", "🎧"),
    "gift": Emoji("5389078930114262933", "🎁"),
    "server": Emoji("5389009961529420076", "🖥️"),
    "status": Emoji("5386381201026100094", "🟢"),
    "speed": Emoji("5389113418701645443", "⚡"),
    "world": Emoji("5388847156499095904", "🌍"),
}

# Only fixed copy is decorated. Never replace inside personal URLs or user data.
HEADINGS = {
    "◈ Kuda Status · Личный кабинет": ("status", "Kuda Status · Личный кабинет"),
    "🚀 Подключить VPN": ("connect", "Подключить VPN"),
    "👤 Личный кабинет": ("subscription", "Личный кабинет"),
    "◈ Моя подписка": ("subscription", "Моя подписка"),
    "◷ История подписки": ("subscription", "История подписки"),
    "✦ Стартовые тарифы": ("plans", "Стартовые тарифы"),
    "💳 Оплата и автопродление": ("payment", "Оплата и автопродление"),
    "🎁 Подарить подписку": ("gift", "Подарить подписку"),
    "◈ Пригласить друга": ("world", "Пригласить друга"),
    "💎 Тарифы": ("plans", "Тарифы"),
    "💎 Ваш выбор": ("plans", "Ваш выбор"),
    "📱 Мои устройства": ("devices", "Мои устройства"),
    "❔ Помощь": ("support", "Помощь"),
    "📖 Настройка Happ": ("connect", "Настройка Happ"),
    "🧪 Тестовый заказ": ("payment", "Тестовый заказ"),
    "📊 Мой трафик": ("speed", "Мой трафик"),
    "🎁 Бонусы": ("gift", "Бонусы"),
    "💬 Поддержка": ("support", "Поддержка"),
    "◈ Kuda Smart": ("world", "Kuda Smart"),
}

BUTTONS = {
    "🔑 Подключиться": ("connect", "Подключиться"),
    "↻ Продлить": ("renew", "Продлить"),
    "🚀 Подключить VPN": ("connect", "Подключить VPN"),
    "🚀 Открыть подключение": ("connect", "Открыть подключение"),
    "🔗 Открыть подписку": ("subscription", "Открыть подписку"),
    "✦ KudaVPN Premium · Подключить": ("premium", "KudaVPN Premium · Подключить"),
    "👤 Личный кабинет": ("subscription", "Личный кабинет"),
    "◈ Моя подписка": ("subscription", "Моя подписка"),
    "◷ История подписки": ("subscription", "История подписки"),
    "✦ Стартовые тарифы · скоро": ("plans", "Стартовые тарифы · скоро"),
    "💎 Тарифы и продление": ("plans", "Тарифы и продление"),
    "📊 Трафик": ("speed", "Трафик"),
    "💳 Оплата и автопродление": ("payment", "Оплата и автопродление"),
    "🎁 Подарить подписку": ("gift", "Подарить подписку"),
    "◈ Пригласить друга": ("world", "Пригласить друга"),
    "❔ Инструкции и поддержка": ("support", "Инструкции и поддержка"),
    "👤 Проверить подписку": ("status", "Проверить подписку"),
    "💎 Тарифы": ("plans", "Тарифы"),
    "💎 Выбрать тариф": ("plans", "Выбрать тариф"),
    "↻ Продлить подписку": ("renew", "Продлить подписку"),
    "📱 Устройства": ("devices", "Устройства"),
    "📱 Мои устройства": ("devices", "Мои устройства"),
    "❔ Помощь": ("support", "Помощь"),
    "📖 Инструкция Happ": ("connect", "Инструкция Happ"),
    "📖 Настройка Happ": ("connect", "Настройка Happ"),
    "💬 Поддержка": ("support", "Поддержка"),
    "💬 Написать в поддержку": ("support", "Написать в поддержку"),
    "🎁 Бесплатный тест — 24 часа": ("gift", "Бесплатный тест — 24 часа"),
    "🎁 Бонусы": ("gift", "Бонусы"),
    "📊 Мой трафик": ("speed", "Мой трафик"),
    "◈ Smart · скоро": ("world", "Smart · скоро"),
    "🎁 Пригласить": ("gift", "Пригласить"),
    "🎧 Поддержка": ("support", "Поддержка"),
    "🧪 Создать тестовый заказ": ("payment", "Создать тестовый заказ"),
    "🧪 Открыть тестовую оплату": ("payment", "Открыть тестовую оплату"),
}


def tag(name: str) -> str:
    icon = EMOJI[name]
    return f'<tg-emoji emoji-id="{icon.id}">{icon.alt}</tg-emoji>'


def decorate(screen: Screen) -> Screen:
    """Add only officially supported HTML entities and inline button icons."""
    text = screen.text.replace("<b>KudaVPN Premium</b>",
                               f"<b>{tag('premium')} KudaVPN Premium</b>", 1)
    for heading, (name, label) in HEADINGS.items():
        text = text.replace(f"<b>{heading}</b>", f"<b>{tag(name)} {label}</b>", 1)
    # This fixed status line appears only on the account screen.
    if "<b>Личный кабинет</b>" not in text and "Статус: <b>" in text:
        text = text.replace("Статус: <b>", f"{tag('status')} Статус: <b>", 1)
    rows = []
    for row in screen.keyboard.inline_keyboard:
        new_row = []
        for button in row:
            match = BUTTONS.get(button.text)
            if match is None and button.callback_data and button.callback_data.startswith("tariff:"):
                match = ("plans", button.text)
            if match:
                name, label = match
                new_row.append(button.model_copy(update={"text": label,
                    "icon_custom_emoji_id": EMOJI[name].id}))
            else:
                new_row.append(button)
        rows.append(new_row)
    return Screen(text, InlineKeyboardMarkup(inline_keyboard=rows))
