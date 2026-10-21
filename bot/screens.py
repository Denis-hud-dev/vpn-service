"""Russian HTML presentation, independent of Telegram delivery and DB writes."""
from dataclasses import dataclass
from html import escape
from datetime import timezone
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from bot.keyboards.main import back_keyboard, main_menu
from bot.keyboards.tariffs import tariffs_keyboard
from bot.services import TARIFFS
from bot.services.account import AccountSnapshot

BRAND = "<b>KudaVPN Premium</b>"


@dataclass(frozen=True)
class Screen:
    text: str
    keyboard: InlineKeyboardMarkup


def panel(title: str, body: str, keyboard: InlineKeyboardMarkup | None = None) -> Screen:
    return Screen(f"{BRAND}\n\n<b>{title}</b>\n\n{body}", keyboard or back_keyboard())


def home(*, premium_account: bool = False) -> Screen:
    intro = ("VPN и подписка всегда под рукой.\nВыберите действие ниже."
             if premium_account else "VPN, подписка и устройства — в одном меню.\nВыберите действие ниже.")
    return Screen(f"{BRAND}\n<i>Ваше пространство подключения.</i>\n\n{intro}",
                  main_menu(premium_account=premium_account))


def account(snapshot: AccountSnapshot, *, trial_available: bool = False,
            premium_account: bool = False) -> Screen:
    labels = {"inactive": "Не активна", "active": "Активна", "expired": "Истекла",
              "pending": "Ожидает активации", "blocked": "Заблокирована"}
    end = snapshot.expires_at.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC") if snapshot.expires_at else "Нет данных"
    devices = ("Управляются в Happ" if premium_account else
               str(snapshot.device_count) if snapshot.device_count is not None else "Нет данных")
    # WireGuard metrics are not connected. Future traffic comes from the read model.
    traffic = f"{snapshot.traffic_bytes / (1024 ** 3):.2f} ГБ" if snapshot.traffic_bytes is not None else "Нет данных"
    body = (f"Статус: <b>{labels.get(snapshot.status, 'Нет данных')}</b>\n"
            f"Тариф: {escape(snapshot.tariff_label or 'Нет данных')}\n"
            f"Действует до: {end}\nУстройства: {devices}\nТрафик: {traffic}")
    buttons = []
    if trial_available:
        buttons.append(("🎁 Бесплатный тест — 24 часа", "pilot:grant"))
    if premium_account:
        buttons.extend((("🚀 Подключить VPN", "nav:connect"),
                        ("↻ Продлить подписку" if snapshot.status == "active" else "💎 Выбрать тариф",
                         "nav:renew"),
                        ("📱 Устройства", "nav:devices"), ("❔ Помощь", "nav:help")))
    else:
        buttons.extend((("💎 Тарифы", "nav:tariffs"), ("📱 Устройства", "nav:devices")))
    return panel("👤 Личный кабинет", body, back_keyboard(*buttons))


def premium_devices(status: str) -> Screen:
    if status == "active":
        body = ("Подключите Android, iOS, Windows или macOS через вашу личную HTTPS-подписку. "
                "Она остаётся прежней после продления.\n\n"
                "Отдельный список подключённых устройств и отключение одного устройства пока недоступны: "
                "доступ управляется подпиской целиком. Не передавайте личную ссылку другим.")
        buttons = (("🚀 Открыть подключение", "nav:connect"), ("📖 Инструкция Happ", "nav:happ"))
    else:
        body = "Для подключения устройства сначала активируйте подписку."
        buttons = (("💎 Выбрать тариф", "nav:tariffs"),)
    return panel("📱 Мои устройства", body, back_keyboard(*buttons, back="nav:account"))


def help_center(username: str) -> Screen:
    body = ("<b>Как подключиться?</b> Откройте раздел подключения, скопируйте личную ссылку "
            "и добавьте её в Happ из буфера обмена.\n\n"
            "<b>Как продлить?</b> В личном кабинете нажмите «Продлить подписку», выберите тариф "
            "и завершите оплату. Оставшееся оплаченное время сохранится.\n\n"
            "<b>Не открываются сайты?</b> Проверьте срок подписки и выбранный профиль в Happ. "
            "Если проблема остаётся, обратитесь в поддержку.")
    keyboard = back_keyboard(("📖 Настройка Happ", "nav:happ"),
                            ("👤 Личный кабинет", "nav:account"))
    if username:
        keyboard.inline_keyboard.insert(0, [InlineKeyboardButton(
            text="💬 Написать в поддержку", url=f"https://t.me/{username}")])
    return panel("❔ Помощь", body, keyboard)


def tariffs(*, test_payment: bool = False) -> Screen:
    note = ("Доступна только тестовая оплата без списания денег."
            if test_payment else "Оплата пока не подключена.")
    return panel("💎 Тарифы", "Выберите срок подписки.\n\n" + note, tariffs_keyboard())


def tariff_details(tariff_id: str, *, test_payment: bool = False) -> Screen:
    tariff = TARIFFS.get(tariff_id)
    if tariff is None:
        return panel("💎 Тарифы", "Тариф не найден. Выберите один из доступных вариантов.", back_keyboard(back="nav:tariffs"))
    if test_payment:
        return panel("💎 Ваш выбор", f"<b>{escape(tariff.label)}</b>\n\nТолько тестовый магазин YooKassa: настоящие деньги не списываются.\nНажмите кнопку, чтобы создать тестовый заказ.",
                     back_keyboard(("🧪 Создать тестовый заказ", f"paytest:{tariff.id}"), back="nav:tariffs"))
    return panel("💎 Ваш выбор", f"<b>{escape(tariff.label)}</b>\n\nОплата ещё не подключена.\nПлатёж не создан.", back_keyboard(back="nav:tariffs"))


def test_checkout(tariff_id: str, confirmation_url: str) -> Screen:
    tariff = TARIFFS[tariff_id]
    keyboard = back_keyboard(("👤 Проверить подписку", "nav:account"), back="nav:tariffs")
    keyboard.inline_keyboard.insert(0, [InlineKeyboardButton(text="🧪 Открыть тестовую оплату", url=confirmation_url)])
    return panel("🧪 Тестовый заказ", f"<b>{escape(tariff.label)}</b>\n\nОткройте тестовую страницу YooKassa. После подтверждения бот независимо проверит платёж в тестовом магазине и обновит подписку. Обычно это занимает до минуты. Настоящие деньги не списываются.", keyboard)


def connect(link: str | None = None, *, page_url: str | None = None) -> Screen:
    keyboard = back_keyboard(("📖 Настройка Happ", "nav:happ"), ("💎 Тарифы", "nav:tariffs"))
    if link:
        keyboard.inline_keyboard.insert(0, [InlineKeyboardButton(text="🔗 Открыть подписку", url=link)])
        if page_url:
            keyboard.inline_keyboard.insert(0, [InlineKeyboardButton(text="✦ KudaVPN Premium · Подключить", url=page_url)])
        body = ("Подключение через <b>Happ</b>.\n\nСкопируйте личную HTTPS-ссылку и импортируйте её в Happ:\n"
                f"<code>{escape(link)}</code>\n\nНе передавайте ссылку другим: она даёт доступ к подписке.")
    else:
        body = ("Подключение через <b>Happ</b>.\n\nПерсональная ссылка появится после активации "
                "оплаченной подписки и проверки VPN-сервера.")
    return panel("🚀 Подключить VPN", body, keyboard)


def happ() -> Screen:
    return panel("📖 Настройка Happ", "<b>Подключение на Android:</b>\n\n1. Установите Happ.\n2. Откройте раздел «Подключить VPN» и скопируйте личную ссылку, если она доступна.\n3. Импортируйте подписку в Happ из буфера обмена.\n4. Выберите сервер и включите VPN.\n\nЕсли ссылка пока недоступна, проверьте статус подписки позже.", back_keyboard(back="nav:connect"))


def traffic() -> Screen:
    return panel("📊 Мой трафик", "Использовано: <b>Нет данных</b>\n\nУчёт трафика ещё не подключён.")


def bonuses() -> Screen:
    return panel("🎁 Бонусы", "Реферальная программа ещё не запущена.\n\nБонусы и начисления пока недоступны.")


def support(username: str) -> Screen:
    keyboard = back_keyboard()
    body = "Вопрос о подписке или подключении?"
    if username:
        keyboard.inline_keyboard.insert(0, [InlineKeyboardButton(text="💬 Написать в поддержку", url=f"https://t.me/{username}")])
        body += "\nСвяжитесь с командой KudaVPN."
    else:
        body += "\n\nКонтакт поддержки пока не настроен."
    return panel("💬 Поддержка", body, keyboard)


def database_error() -> Screen:
    return panel("◈ Сервис временно недоступен", "Не удалось загрузить данные.\nПопробуйте вернуться в меню чуть позже.")
