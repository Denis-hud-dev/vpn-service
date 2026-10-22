from aiogram import F, Router
from aiogram.types import CallbackQuery
from bot.navigation import Navigation
from bot import screens
from bot.services import Services


def pilot_payment_allowed(callback: CallbackQuery, services: Services) -> bool:
    return bool(services.test_checkout is not None and services.test_payment_owner_id
                and callback.from_user.id == services.test_payment_owner_id
                and getattr(getattr(callback.message, 'chat', None), 'type', None) == 'private')


async def buy(callback: CallbackQuery, services: Services, navigation: Navigation):
    await navigation.callback(callback, screens.tariffs(test_payment=pilot_payment_allowed(callback, services)))


async def select_tariff(callback: CallbackQuery, services: Services, navigation: Navigation):
    # Selecting a tariff does not create an order; a second explicit tap does.
    await navigation.callback(callback, screens.tariff_details(
        (callback.data or "").partition(":")[2],
        test_payment=pilot_payment_allowed(callback, services)))


async def create_test_order(callback: CallbackQuery, services: Services, navigation: Navigation):
    if not pilot_payment_allowed(callback, services):
        await navigation.callback(callback, screens.panel('🧪 Тестовая оплата', '◆ Доступно только владельцу тестового аккаунта.'))
        return
    tariff_id = (callback.data or '').partition(':')[2]
    try:
        intent = await services.test_checkout.create_for_user(callback.from_user.id, tariff_id)
    except (ValueError, RuntimeError, OSError, TimeoutError):
        await navigation.callback(callback, screens.panel('🧪 Тестовая оплата',
            '◆ Создать тестовый заказ пока не удалось.\n╰ Повторите попытку позже.'))
        return
    await navigation.callback(callback, screens.test_checkout(tariff_id, intent.confirmation_url))


async def subscription(callback: CallbackQuery, services: Services, navigation: Navigation):
    snapshot = await services.subscriptions.snapshot(callback.from_user.id)
    trial_available = bool(services.trial and
        callback.from_user.id == services.trial.owner_telegram_id and
        snapshot.status == 'inactive')
    await navigation.callback(callback, screens.account(snapshot, trial_available=trial_available,
        premium_account=services.premium_account_enabled))


async def grant_trial(callback: CallbackQuery, services: Services, navigation: Navigation):
    private_chat = getattr(getattr(callback.message, 'chat', None), 'type', None) == 'private'
    if not private_chat or services.trial is None or callback.from_user.id != services.trial.owner_telegram_id:
        await navigation.callback(callback, screens.panel('🎁 Пробный доступ',
            '◆ Пробный доступ недоступен для этого аккаунта.'))
        return
    try:
        await services.trial.grant_once(callback.from_user.id)
    except (ValueError, PermissionError):
        await navigation.callback(callback, screens.panel('🎁 Пробный доступ',
            '◆ Активировать пробный доступ не удалось.\n╰ Проверьте статус в личном кабинете.'))
        return
    await navigation.callback(callback, screens.panel('🎁 Пробный доступ активирован',
        '◆ Ваши 24 часа начались.\n├ Настройка личного доступа может занять до минуты.\n'
        '╰ Откройте раздел подключения и получите ссылку для Happ.',
        screens.back_keyboard(("🚀 Подключить VPN", "nav:connect"),
                              ("👤 Личный кабинет", "nav:account"))))


def create_router() -> Router:
    router = Router(name="subscription")
    router.callback_query.register(buy, F.data.in_({"nav:tariffs", "nav:renew", "buy"}))
    router.callback_query.register(select_tariff, F.data.startswith("tariff:"))
    router.callback_query.register(create_test_order, F.data.startswith("paytest:"))
    router.callback_query.register(subscription, F.data == "nav:account")
    router.callback_query.register(grant_trial, F.data == "pilot:grant")
    return router
