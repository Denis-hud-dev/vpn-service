"""Owner-only menu PREVIEW, not registered by bot.handlers or any live runner.

Every callback is informational. No invoice, order, external payment URL or grant
is produced. Wire a real Stars handler only after TEST credentials/ledger checks.
"""
from aiogram import F,Router
from aiogram.types import CallbackQuery

from bot import screens
from bot.keyboards.main import back_keyboard
from bot.navigation import Navigation
from bot.services import PILOT_TARIFFS
from bot.services.payment_channels import Channel,ChannelPolicy,Surface
from bot.services.stars_sandbox import TEST_STARS_PRICES

LABELS={Channel.STARS:'⭐ Telegram Stars · подготовка',
        Channel.CARD:'💳 Карта · недоступно в Telegram',
        Channel.SBP:'⚡ СБП · недоступно в Telegram'}


def methods_screen(tariff_id,policy=ChannelPolicy()):
    tariff=PILOT_TARIFFS.get(tariff_id)
    if tariff is None:raise ValueError('Unknown tariff')
    body=(f'◆ Срок: {tariff.months} мес.\n'
          f'├ TEST Stars: {TEST_STARS_PRICES[tariff.months]} XTR\n'
          '╰ Предварительный экран. Покупки не включены.\n\n'
          'Карта и СБП через ЮKassa не используются для покупки цифровой подписки в боте.\n'
          'Stars потребует отдельный TEST-бот и единый учёт оплаченных заказов.')
    return screens.panel('Способ оплаты · макет',body,back_keyboard(
        *((LABELS[c],f'paymentinfo:{tariff_id}:{c.value}') for c in Channel),
        back='nav:tariffs'))


def create_router(owner_id,policy=ChannelPolicy()):
    if type(owner_id) is not int or owner_id<=0:raise ValueError('Trusted preview owner required')
    router=Router(name='payment_methods_preview')

    async def preview(callback: CallbackQuery,navigation: Navigation):
        message=callback.message
        if (callback.from_user.id!=owner_id or message is None or message.chat.type!='private'
                or message.chat.id!=owner_id):
            await callback.answer('Недоступно',show_alert=True)
            return
        fields=(callback.data or '').split(':')
        if len(fields)!=(2 if fields[0]=='paymentpreview' else 3):
            await callback.answer('Способ недоступен',show_alert=True)
            return
        tariff_id=fields[1] if len(fields)>1 else ''
        if tariff_id not in PILOT_TARIFFS:
            await callback.answer('Предложение недоступно',show_alert=True)
            return
        if fields[0]=='paymentpreview':
            await navigation.callback(callback,methods_screen(tariff_id,policy))
            return
        try:channel=Channel(fields[2])
        except (ValueError,IndexError):
            await callback.answer('Способ недоступен',show_alert=True)
            return
        decision=policy.decision(channel,Surface.TELEGRAM)
        body=('◆ Для цифровой подписки в Telegram используется Stars.\n'
              '╰ Внешний платёж и заказ не создаются.' if channel!=Channel.STARS else
              '◆ Утверждены только тестовые цены 1 / 2 / 3 XTR.\n'
              '╰ Счёт пока не отправляется: TEST-бот и канонический XTR-учёт ещё не подключены.')
        # Even an eligible policy is not a real payment implementation.
        if decision.allowed:body+='\nЭтот обработчик остаётся только макетом.'
        await navigation.callback(callback,screens.panel('Оплата · информация',body,
            back_keyboard(('Способы оплаты',f'paymentpreview:{tariff_id}'),back='nav:tariffs')))

    router.callback_query.register(preview,F.data.startswith('paymentpreview:')|F.data.startswith('paymentinfo:'))
    return router
