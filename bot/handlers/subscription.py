from aiogram import F, Router
from aiogram.types import CallbackQuery
from bot.navigation import Navigation
from bot import screens
from bot.services import Services


async def buy(callback: CallbackQuery, navigation: Navigation):
    await navigation.callback(callback, screens.tariffs())


async def select_tariff(callback: CallbackQuery, navigation: Navigation):
    # Selection is presentation only. Future payment confirmation stays separate.
    await navigation.callback(callback, screens.tariff_details((callback.data or "").partition(":")[2]))


async def subscription(callback: CallbackQuery, services: Services, navigation: Navigation):
    snapshot = await services.subscriptions.snapshot(callback.from_user.id)
    trial_available = bool(services.trial and
        callback.from_user.id == services.trial.owner_telegram_id and
        snapshot.status == 'inactive')
    await navigation.callback(callback, screens.account(snapshot, trial_available=trial_available))


async def grant_trial(callback: CallbackQuery, services: Services, navigation: Navigation):
    private_chat = getattr(getattr(callback.message, 'chat', None), 'type', None) == 'private'
    if not private_chat or services.trial is None or callback.from_user.id != services.trial.owner_telegram_id:
        await navigation.callback(callback, screens.panel('🎁 Пробный доступ',
            'Пробный доступ недоступен для этого аккаунта.'))
        return
    try:
        await services.trial.grant_once(callback.from_user.id)
    except (ValueError, PermissionError):
        await navigation.callback(callback, screens.panel('🎁 Пробный доступ',
            'Активировать пробный доступ не удалось. Проверьте статус в личном кабинете.'))
        return
    await navigation.callback(callback, screens.panel('🎁 Пробный доступ активирован',
        'Ваши 24 часа начались. Настройка персонального доступа может занять до минуты. '
        'Откройте раздел подключения и получите ссылку для Happ.',
        screens.back_keyboard(("🚀 Подключить VPN", "nav:connect"),
                              ("👤 Личный кабинет", "nav:account"))))


def create_router() -> Router:
    router = Router(name="subscription")
    router.callback_query.register(buy, F.data.in_({"nav:tariffs", "buy"}))
    router.callback_query.register(select_tariff, F.data.startswith("tariff:"))
    router.callback_query.register(subscription, F.data == "nav:account")
    router.callback_query.register(grant_trial, F.data == "pilot:grant")
    return router
