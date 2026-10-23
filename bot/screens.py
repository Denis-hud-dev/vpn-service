"""Russian HTML presentation, independent of Telegram delivery and DB writes."""
from dataclasses import dataclass
from html import escape
from datetime import datetime, timezone
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from bot.keyboards.main import back_keyboard, main_menu
from bot.keyboards.tariffs import tariffs_keyboard
from bot.services import TARIFFS
from bot.services.account import AccountSnapshot

BRAND = "<b>KudaVPN Premium</b>"


def expiry_label(value: datetime | None) -> str:
    if value is None:
        return "Нет данных"
    # SQLite fixtures may return a naive timestamp; persisted subscription times are UTC.
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return aware.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")


@dataclass(frozen=True)
class Screen:
    text: str
    keyboard: InlineKeyboardMarkup


def panel(title: str, body: str, keyboard: InlineKeyboardMarkup | None = None) -> Screen:
    return Screen(f"{BRAND}\n<b>{title}</b>\n\n┃ {body}\n\n◆ KudaVPN · личное пространство", keyboard or back_keyboard())


def home(*, premium_account: bool = False, snapshot: AccountSnapshot | None = None) -> Screen:
    if premium_account:
        statuses = {"inactive": "Не активна", "active": "Активна", "expired": "Истекла",
                    "pending": "Ожидает активации", "blocked": "Заблокирована"}
        state = (f"◆ Подписка: <b>{statuses.get(snapshot.status, 'Нет данных')}</b>"
                 if snapshot else "◆ Статус подписки — в личном кабинете")
        return Screen(f"{BRAND}\n<i>Свобода подключения. Простота управления.</i>\n\n"
                      f"{state}\n┃ Личный доступ и управление — здесь.",
                      main_menu(premium_account=True))
    intro = ("Ваш VPN и подписка всегда под рукой."
             if premium_account else "VPN, подписка и устройства — в одном месте.")
    return Screen(f"{BRAND}\n<i>Ваше пространство подключения.</i>\n\n"
                  f"◆ {intro}\n┃ Выберите действие ниже.\n╰ Безопасный доступ начинается здесь.",
                  main_menu(premium_account=premium_account))


def kuda_smart() -> Screen:
    """Informational pilot screen; no connection mode is activated here."""
    body = ("◆ <b>Умный выбор сервера — в разработке</b>\n"
            "├ Happ умеет показывать задержку серверов в подписке.\n"
            "├ Автовыбор по задержке при запуске требует отдельной проверки в Happ.\n"
            "╰ Автоматическое переключение при обрыве пока не подтверждено.\n\n"
            "◈ <b>Будущие режимы</b>\n"
            "├ Smart · лучший доступный узел\n"
            "├ Максимальная скорость · после проверки реальной пропускной способности\n"
            "╰ Выбрать страну · когда появятся проверенные узлы в других странах\n\n"
            "Сейчас эти режимы не включаются. Ваша действующая подписка работает как прежде.")
    return panel("◈ Kuda Smart", body,
                 back_keyboard(("🚀 Подключить VPN", "nav:connect"), primary="nav:connect"))


def account(snapshot: AccountSnapshot, *, trial_available: bool = False,
            premium_account: bool = False) -> Screen:
    labels = {"inactive": "Не активна", "active": "Активна", "expired": "Истекла",
              "pending": "Ожидает активации", "blocked": "Заблокирована"}
    end = expiry_label(snapshot.expires_at)
    devices = ("Подключаются через Happ" if premium_account else
               str(snapshot.device_count) if snapshot.device_count is not None else "Нет данных")
    traffic = f"{snapshot.traffic_bytes / (1024 ** 3):.2f} ГБ" if snapshot.traffic_bytes is not None else "Нет данных"
    body = (f"◆ Статус: <b>{labels.get(snapshot.status, 'Нет данных')}</b>\n"
            f"├ Тариф: {escape(snapshot.tariff_label or 'Нет данных')}\n"
            f"├ Действует до: {end}\n"
            f"├ Устройства: {devices}\n"
            f"╰ Трафик: {traffic}")
    buttons = []
    if trial_available:
        buttons.append(("🎁 Бесплатный тест — 24 часа", "pilot:grant"))
    if premium_account:
        buttons.extend((("🚀 Подключить VPN", "nav:connect"),
                        ("◈ Моя подписка", "nav:subscription"),
                        ("📱 Мои устройства", "nav:devices"),
                        ("📊 Трафик", "nav:traffic"),
                        ("💳 Оплата и автопродление", "nav:billing"),
                        ("🎁 Подарить подписку", "nav:gift"),
                        ("◈ Пригласить друга", "nav:invite")))
    else:
        buttons.extend((("💎 Тарифы", "nav:tariffs"), ("📱 Устройства", "nav:devices")))
    return panel("👤 Личный кабинет", body,
                 back_keyboard(*buttons, primary="pilot:grant" if trial_available else
                               "nav:connect" if premium_account else None))


def subscription_details(snapshot: AccountSnapshot) -> Screen:
    labels = {"inactive": "Не активна", "active": "Активна", "expired": "Истекла",
              "pending": "Ожидает активации", "blocked": "Заблокирована"}
    end = expiry_label(snapshot.expires_at)
    body = (f"◆ Статус: <b>{labels.get(snapshot.status, 'Нет данных')}</b>\n"
            f"├ Тариф: {escape(snapshot.tariff_label or 'Не выбран')}\n"
            f"└ Действует до: {end}")
    if snapshot.status == "active":
        body += "\n\n◈ При продлении оставшееся время сохраняется, а личная ссылка Happ не меняется."
        actions = (("🚀 Подключить VPN", "nav:connect"), ("↻ Продлить подписку", "nav:renew"))
    else:
        body += "\n\n◈ Для подключения нужен активный доступ."
        actions = (("💎 Выбрать тариф", "nav:tariffs"),)
    return panel("◈ Моя подписка", body,
                 back_keyboard(*actions, back="nav:account",
                               primary="nav:connect" if snapshot.status == "active" else "nav:tariffs"))


def premium_devices(status: str) -> Screen:
    if status == "active":
        body = ("◆ Android · iPhone · Windows · macOS\n"
                "├ Добавьте личную HTTPS-подписку в Happ. После продления ссылка остаётся прежней.\n"
                "└ Список отдельных устройств и их отключение пока недоступны: "
                "сейчас доступ управляется подпиской целиком. Не передавайте личную ссылку другим.")
        buttons = (("🚀 Открыть подключение", "nav:connect"), ("📖 Инструкция Happ", "nav:happ"))
    else:
        body = "Для подключения устройства сначала активируйте подписку."
        buttons = (("💎 Выбрать тариф", "nav:tariffs"),)
    return panel("📱 Мои устройства", body,
                 back_keyboard(*buttons, back="nav:account",
                               primary="nav:connect" if status == "active" else "nav:tariffs"))


def help_center(username: str) -> Screen:
    body = ("◆ <b>Как подключиться?</b>\n├ Откройте страницу подключения, скопируйте личную ссылку и добавьте её в Happ.\n\n"
            "◆ <b>Продление</b>\n├ В личном кабинете выберите тариф и завершите оплату. "
            "Оставшееся оплаченное время сохранится.\n\n"
            "◆ <b>Если сайты не открываются</b>\n╰ Проверьте срок подписки и выбранный профиль в Happ. "
            "Если проблема остаётся, обратитесь в поддержку.")
    keyboard = back_keyboard(("🚀 Открыть подключение", "nav:connect"),
                            ("📖 Настройка Happ", "nav:happ"),
                            ("👤 Личный кабинет", "nav:account"), primary="nav:connect")
    if username:
        keyboard.inline_keyboard.insert(0, [InlineKeyboardButton(
            text="💬 Написать в поддержку", url=f"https://t.me/{username}")])
    return panel("❔ Помощь", body, keyboard)


def tariffs(*, test_payment: bool = False) -> Screen:
    note = ("Доступна только тестовая оплата без списания денег."
            if test_payment else "Оплата пока не подключена.")
    plans = list(TARIFFS.values())
    body = ("◆ Выберите срок доступа\n"
            + "\n".join(("├ " if idx < len(plans) - 1 else "╰ ") + escape(t.label)
                        for idx, t in enumerate(plans))
            + "\n\n" + note)
    return panel("💎 Тарифы", body, tariffs_keyboard())


def tariff_details(tariff_id: str, *, test_payment: bool = False) -> Screen:
    tariff = TARIFFS.get(tariff_id)
    if tariff is None:
        return panel("💎 Тарифы", "Тариф не найден. Выберите один из доступных вариантов.", back_keyboard(back="nav:tariffs"))
    if test_payment:
        return panel("💎 Ваш выбор", f"◆ <b>{escape(tariff.label)}</b>\n├ Тестовый магазин YooKassa. Настоящие деньги не списываются.\n╰ Создайте тестовый заказ кнопкой ниже.",
                     back_keyboard(("🧪 Создать тестовый заказ", f"paytest:{tariff.id}"),
                                   back="nav:tariffs", primary=f"paytest:{tariff.id}"))
    return panel("💎 Ваш выбор", f"◆ <b>{escape(tariff.label)}</b>\n╰ Оплата ещё не подключена. Платёж не создан.", back_keyboard(back="nav:tariffs"))


def test_checkout(tariff_id: str, confirmation_url: str) -> Screen:
    tariff = TARIFFS[tariff_id]
    keyboard = back_keyboard(("👤 Проверить подписку", "nav:account"), back="nav:tariffs")
    keyboard.inline_keyboard.insert(0, [InlineKeyboardButton(
        text="🧪 Открыть тестовую оплату", url=confirmation_url, style="primary")])
    return panel("🧪 Тестовый заказ", f"◆ <b>{escape(tariff.label)}</b>\n├ Откройте тестовую страницу YooKassa.\n├ После подтверждения бот проверит платёж и обновит подписку. Обычно это занимает до минуты.\n╰ Настоящие деньги не списываются.", keyboard)


def connect(link: str | None = None, *, page_url: str | None = None,
            premium_account: bool = False) -> Screen:
    keyboard = back_keyboard(("📖 Настройка Happ", "nav:happ"), ("💎 Тарифы", "nav:tariffs"))
    if link:
        keyboard.inline_keyboard.insert(0, [InlineKeyboardButton(
            text="🔗 Открыть подписку", url=link,
            style="primary" if premium_account and not page_url else None)])
        if page_url:
            keyboard.inline_keyboard.insert(0, [InlineKeyboardButton(
                text="✦ KudaVPN Premium · Подключить", url=page_url,
                style="primary" if premium_account else None)])
        body = ("◆ Подключение через <b>Happ</b>\n"
                "├ Откройте фирменную страницу или скопируйте личную HTTPS-ссылку:\n"
                f"<code>{escape(link)}</code>\n"
                "╰ Не передавайте ссылку другим: она даёт доступ к подписке.")
    else:
        body = ("◆ Подключение через <b>Happ</b>\n"
                "╰ Личная ссылка появится после активации подписки и проверки VPN-сервера.")
    return panel("🚀 Подключить VPN", body, keyboard)


def happ() -> Screen:
    return panel("📖 Настройка Happ", "◆ <b>Android и iPhone</b>\n"
                 "├ Установите Happ на своё устройство.\n"
                 "├ Откройте «Подключить VPN» и скопируйте личную ссылку.\n"
                 "├ В Happ нажмите «+» и добавьте подписку из буфера обмена.\n"
                 "╰ Выберите KudaVPN и включите VPN.\n\n"
                 "Если ссылка пока недоступна, проверьте статус подписки позже.",
                 back_keyboard(back="nav:connect"))


def traffic(snapshot: AccountSnapshot | None = None) -> Screen:
    if snapshot and snapshot.traffic_bytes is not None:
        body = f"◆ Использовано: <b>{snapshot.traffic_bytes / (1024 ** 3):.2f} ГБ</b>\n└ Данные относятся к вашей подписке."
    else:
        body = "◆ Использовано: <b>Нет данных</b>\n└ Узел пока не передаёт персональную статистику. Мы не показываем условный ноль."
    return panel("📊 Мой трафик", body, back_keyboard(back="nav:account"))


def billing(*, test_payment: bool = False) -> Screen:
    body = ("◆ Автопродление: <b>выключено</b>\n"
            "├ Повторных списаний нет. Продление запускается вручную после выбора тарифа.\n"
            "└ Оплата доступна только в изолированном тестовом магазине."
            if test_payment else
            "◆ Автопродление: <b>выключено</b>\n└ Оплата для этого аккаунта пока недоступна.")
    return panel("💳 Оплата и автопродление", body,
                 back_keyboard(("💎 Тарифы и продление", "nav:tariffs"), back="nav:account"))


def gift() -> Screen:
    return panel("🎁 Подарить подписку",
                 "◆ Подарочные подписки пока не выдаются.\n"
                 "└ Мы добавим эту возможность после отдельного учёта получателя и безопасной передачи доступа.",
                 back_keyboard(back="nav:account"))


def invite() -> Screen:
    return panel("◈ Пригласить друга",
                 "◆ Приглашения и бонусы пока не запущены.\n"
                 "└ Бонус за приглашение не начисляется; персональную ссылку VPN передавать нельзя.",
                 back_keyboard(back="nav:account"))


def bonuses() -> Screen:
    return panel("🎁 Бонусы", "◆ Реферальная программа ещё не запущена.\n╰ Бонусы и начисления пока недоступны.")


def support(username: str) -> Screen:
    keyboard = back_keyboard()
    body = "◆ Вопрос о подписке или подключении?"
    if username:
        keyboard.inline_keyboard.insert(0, [InlineKeyboardButton(text="💬 Написать в поддержку", url=f"https://t.me/{username}")])
        body += "\n╰ Свяжитесь с командой KudaVPN."
    else:
        body += "\n╰ Контакт поддержки пока не настроен."
    return panel("💬 Поддержка", body, keyboard)


def database_error() -> Screen:
    return panel("◈ Сервис временно недоступен", "◆ Не удалось загрузить данные.\n╰ Попробуйте вернуться в меню чуть позже.")
