"""Russian HTML presentation, independent of Telegram delivery and DB writes."""
from dataclasses import dataclass
from html import escape
from datetime import datetime, timezone
from math import ceil
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from bot.keyboards.main import back_keyboard, main_menu
from bot.keyboards.tariffs import tariffs_keyboard
from bot.services import TARIFFS
from bot.services.account import AccountSnapshot, SubscriptionEvent
from bot.startup_pricing import MONTHLY_RUB, QUARTERLY_RUB, ANNUAL_RUB, saving_against_monthly

BRAND = "<b>KudaVPN Premium</b>"


def expiry_label(value: datetime | None) -> str:
    if value is None:
        return "Нет данных"
    # SQLite fixtures may return a naive timestamp; persisted subscription times are UTC.
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return aware.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")


def remaining_label(snapshot: AccountSnapshot) -> str:
    if snapshot.status != "active":
        return "Нет активной подписки"
    if snapshot.expires_at is None:
        return "Нет данных"
    end = (snapshot.expires_at.replace(tzinfo=timezone.utc) if snapshot.expires_at.tzinfo is None
           else snapshot.expires_at)
    seconds = (end - datetime.now(timezone.utc)).total_seconds()
    if seconds <= 0:
        return "Срок истёк"
    if seconds < 3600:
        return f"{ceil(seconds / 60)} мин"
    hours = ceil(seconds / 3600)
    days, hours = divmod(hours, 24)
    return f"{days} дн {hours} ч" if days and hours else f"{days} дн" if days else f"{hours} ч"


@dataclass(frozen=True)
class Screen:
    text: str
    keyboard: InlineKeyboardMarkup


def panel(title: str, body: str, keyboard: InlineKeyboardMarkup | None = None) -> Screen:
    return Screen(f"{BRAND}\n<b>{title}</b>\n\n┃ {body}\n\n◆ KudaVPN · личное пространство", keyboard or back_keyboard())


def home(*, premium_account: bool = False, snapshot: AccountSnapshot | None = None,
         reply_navigation: bool = False) -> Screen:
    if premium_account:
        statuses = {"inactive": "Не активна", "active": "Активна", "expired": "Истекла",
                    "pending": "Ожидает активации", "blocked": "Заблокирована"}
        state = (f"◆ Подписка: <b>{statuses.get(snapshot.status, 'Нет данных')}</b>"
                 if snapshot else "◆ Статус подписки — в личном кабинете")
        return Screen(f"{BRAND}\n<i>Свобода подключения. Простота управления.</i>\n\n"
                      f"{state}\n┃ Личный доступ и управление — здесь.",
                      main_menu(premium_account=True, reply_navigation=reply_navigation))
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


def information(support_username: str = "") -> Screen:
    support_line = (f"@{escape(support_username)}" if support_username else
                    "контакт пока не настроен")
    body = ("◆ <b>KudaVPN Premium</b> — личный доступ и удобное управление подпиской.\n"
            "├ Happ: Android и iPhone; инструкции для Windows и macOS — в разделе подключения.\n"
            "├ Подключение: откройте личную страницу, выберите устройство и импортируйте подписку в Happ.\n"
            f"├ Поддержка: {support_line}.\n"
            "╰ Условия использования и политика конфиденциальности пока не опубликованы в боте.")
    return panel("ℹ️ Информация", body,
                 back_keyboard(("🚀 Подключить VPN", "nav:connect"),
                               ("📖 Инструкция", "nav:happ"),
                               ("💬 Поддержка", "nav:support")))


def account(snapshot: AccountSnapshot, *, trial_available: bool = False,
            premium_account: bool = False, node_names: tuple[str, ...] | None = None) -> Screen:
    if premium_account:
        return kuda_status(snapshot, trial_available=trial_available, node_names=node_names)
    labels = {"inactive": "Не активна", "active": "Активна", "expired": "Истекла",
              "pending": "Ожидает активации", "blocked": "Заблокирована"}
    devices = str(snapshot.device_count) if snapshot.device_count is not None else "Нет данных"
    traffic = f"{snapshot.traffic_bytes / (1024 ** 3):.2f} ГБ" if snapshot.traffic_bytes is not None else "Нет данных"
    body = (f"◆ Статус: <b>{labels.get(snapshot.status, 'Нет данных')}</b>\n"
            f"├ Тариф: {escape(snapshot.tariff_label or 'Нет данных')}\n"
            f"├ Действует до: {expiry_label(snapshot.expires_at)}\n"
            f"├ Осталось: {remaining_label(snapshot)}\n"
            f"├ Устройства: {devices}\n"
            f"╰ Трафик: {traffic}")
    buttons = [("🎁 Бесплатный тест — 24 часа", "pilot:grant")] if trial_available else []
    buttons.extend((("💎 Тарифы", "nav:tariffs"), ("📱 Устройства", "nav:devices")))
    return panel("👤 Личный кабинет", body,
                 back_keyboard(*buttons, primary="pilot:grant" if trial_available else None))


def kuda_status(snapshot: AccountSnapshot, *, trial_available: bool = False,
                node_names: tuple[str, ...] | None = None) -> Screen:
    # Expiry remains authoritative even if a stale snapshot still says active.
    status = snapshot.status
    if status == "active" and snapshot.expires_at is not None:
        end = snapshot.expires_at.replace(tzinfo=timezone.utc) if snapshot.expires_at.tzinfo is None else snapshot.expires_at
        if end <= datetime.now(timezone.utc):
            status = "expired"
    states = {"active": "🟢 Подписка активна", "expired": "◈ Срок подписки истёк",
              "inactive": "◈ Подписка не активна", "pending": "◈ Ожидает активации",
              "blocked": "◈ Доступ заблокирован"}
    expiry = expiry_label(snapshot.expires_at)
    # The full timestamp remains in «Моя подписка»; the summary names UTC explicitly.
    if snapshot.expires_at is not None:
        expiry = expiry.split(" ")[0] + " · UTC"
    remaining = remaining_label(snapshot) if status == snapshot.status else "Срок истёк"
    if status != "active":
        nodes = "Серверы доступны после активации подписки."
    elif node_names is None:
        nodes = "Сведения временно недоступны. Попробуйте позже."
    elif not node_names:
        nodes = "Сейчас нет подтверждённых доступных серверов. Попробуйте позже."
    else:
        nodes = "\n".join(f"<b>{escape(name[:100])}</b>\n🟢 Доступен · проверен сервером"
                          for name in node_names[:4])
        if len(node_names) > 4:
            nodes += f"\nЕщё {len(node_names) - 4} — в подписке Happ."
    body = (f"<b>{states.get(status, '◈ Статус недоступен')}</b>\n\n"
            f"◆ Тариф: <b>{escape(snapshot.tariff_label or 'Не сохранён')}</b>\n"
            f"├ Осталось: {remaining}\n"
            f"╰ Действует до: {expiry}\n\n"
            f"🌐 <b>ВАШИ СЕРВЕРЫ</b>\n{nodes}\n\n"
            "<i>Свобода подключения. Простота управления.</i>")
    actions = [("🔵 Подключиться", "nav:connect"), ("💎 Продлить подписку", "nav:renew")]
    if trial_available:
        actions.insert(0, ("🎁 Бесплатный тест — 24 часа", "pilot:grant"))
    keyboard = back_keyboard(*actions, primary="nav:connect")
    keyboard.inline_keyboard[-1:-1] = [
        [InlineKeyboardButton(text="📋 Моя подписка", callback_data="nav:subscription"),
         InlineKeyboardButton(text="📱 Мои устройства", callback_data="nav:devices")],
        [InlineKeyboardButton(text="⚙️ Управление", callback_data="nav:manage")],
    ]
    return Screen(f"{BRAND}\n<b>✦ Kuda Status</b>\n\n{body}", keyboard)


def account_management() -> Screen:
    return panel("⚙️ Управление",
                 "◆ История и дополнительные возможности вашей подписки.\n"
                 "╰ Доступность каждого действия указана внутри раздела.",
                 back_keyboard(("◷ История подписки", "nav:history"),
                               ("📊 Трафик", "nav:traffic"),
                               ("💳 Оплата и автопродление", "nav:billing"),
                               ("🎁 Подарить подписку", "nav:gift"),
                               ("◈ Пригласить друга", "nav:invite"),
                               back="nav:account"))


def subscription_details(snapshot: AccountSnapshot, *, premium_account: bool = False) -> Screen:
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
    if premium_account:
        actions += (("◷ История подписки", "nav:history"),)
    return panel("◈ Моя подписка", body,
                 back_keyboard(*actions, back="nav:account",
                               primary="nav:connect" if snapshot.status == "active" else "nav:tariffs"))


def subscription_history(events: list[SubscriptionEvent] | None) -> Screen:
    if events is None:
        body = "◆ История временно недоступна. Попробуйте позже."
    elif not events:
        body = "◆ Пока нет записанных изменений подписки."
    else:
        labels = {
            "created": "Подписка создана", "pilot_trial_grant": "Пробный доступ активирован",
            "pilot_trial_extend": "Пробный срок продлён", "expired": "Срок истёк",
        }
        status_labels = {"blocked": "Доступ заблокирован", "active": "Доступ активен",
                         "expired": "Срок истёк", "pending": "Ожидает активации"}
        lines = []
        for event in events:
            if event.reason == "payment":
                renewed = bool(event.old_expires_at and event.new_starts_at
                               and event.old_expires_at > event.new_starts_at)
                label = "Подписка продлена" if renewed else "Оплата подтверждена · доступ активирован"
            elif event.reason == "status":
                label = status_labels.get(event.new_status, "Статус изменён")
            else:
                label = labels.get(event.reason, "Срок или статус изменён")
            date = expiry_label(event.occurred_at)
            end = f" · до {expiry_label(event.new_expires_at)}" if event.new_expires_at else ""
            lines.append(f"◆ {date}\n└ {label}{end}")
        body = "\n\n".join(lines)
    body += "\n\n◈ Показаны только изменения подписки из базы. История платежей и чеки пока недоступны."
    return panel("◷ История подписки", body, back_keyboard(back="nav:account"))


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


def tariffs(*, test_payment: bool = False, premium_account: bool = False) -> Screen:
    note = ("Доступна только тестовая оплата без списания денег."
            if test_payment else "Оплата пока не подключена.")
    plans = list(TARIFFS.values())
    body = ("◆ Выберите срок доступа\n"
            + "\n".join(("├ " if idx < len(plans) - 1 else "╰ ") + escape(t.label)
                        for idx, t in enumerate(plans))
            + "\n\n" + note)
    keyboard = tariffs_keyboard()
    if premium_account:
        body = "◆ Ниже — действующие тарифы тестовой оплаты.\n\n" + body
        keyboard.inline_keyboard.insert(0, [InlineKeyboardButton(
            text="✦ Стартовые тарифы · скоро", callback_data="nav:launch_tariffs")])
    return panel("💎 Тарифы", body, keyboard)


def launch_tariffs() -> Screen:
    """Display-only proposal. Checkout continues to use the existing prices."""
    body = ("◆ <b>Стартовые тарифы — в подготовке</b>\n"
            f"├ 🟢 1 месяц — <b>{MONTHLY_RUB} ₽</b>\n"
            f"├ 🔵 3 месяца — <b>{QUARTERLY_RUB} ₽</b>\n"
            f"│  Экономия {saving_against_monthly(3, QUARTERLY_RUB)} ₽ против трёх помесячных оплат.\n"
            f"╰ ⭐ Максимальная выгода · 12 месяцев — <b>{ANNUAL_RUB} ₽</b>\n"
            f"   Экономия {saving_against_monthly(12, ANNUAL_RUB)} ₽ против двенадцати помесячных оплат.\n\n"
            "◈ Эти цены пока нельзя оплатить. Действующие тестовые тарифы и суммы платежей не изменены.")
    keyboard = back_keyboard(back="nav:tariffs")
    keyboard.inline_keyboard[:0] = [
        [InlineKeyboardButton(text=f"1 месяц · {MONTHLY_RUB} ₽ · скоро",
                              callback_data="launchplan:month", style="success")],
        [InlineKeyboardButton(text=f"3 месяца · {QUARTERLY_RUB} ₽ · скоро",
                              callback_data="launchplan:quarter", style="primary")],
        [InlineKeyboardButton(text=f"⭐ 12 месяцев · {ANNUAL_RUB} ₽ · скоро",
                              callback_data="launchplan:year", style="success")],
    ]
    return panel("✦ Стартовые тарифы", body, keyboard)


def launch_tariff_preview(tariff_id: str) -> Screen:
    """Informational preview only: no order, price lookup, or payment callback."""
    proposals = {
        "month": ("1 месяц", MONTHLY_RUB, None),
        "quarter": ("3 месяца", QUARTERLY_RUB, saving_against_monthly(3, QUARTERLY_RUB)),
        "year": ("12 месяцев · максимальная выгода", ANNUAL_RUB,
                 saving_against_monthly(12, ANNUAL_RUB)),
    }
    proposal = proposals.get(tariff_id)
    if proposal is None:
        return launch_tariffs()
    label, price, saving = proposal
    saving_text = f"\n├ Экономия {saving} ₽ относительно помесячной оплаты." if saving else ""
    return panel("✦ Предварительный тариф",
                 f"◆ <b>{label} — {price} ₽</b>{saving_text}\n"
                 "╰ Скоро. Этот тариф пока нельзя оплатить; заказ не создаётся.",
                 back_keyboard(back="nav:launch_tariffs"))


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
