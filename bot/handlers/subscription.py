from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from bot.keyboards.main import BUY, CONNECT, SUBSCRIPTION, back_keyboard
from bot.keyboards.tariffs import tariffs_keyboard
from bot.services import Services, TARIFFS

router = Router(name="subscription")
TARIFF_TEXT = "💎 Выберите тариф KudaVPN\n\n1 месяц — 299 ₽\n3 месяца — 799 ₽\n12 месяцев — 2490 ₽"


@router.message(F.text.in_({BUY, CONNECT}))
async def tariffs(message: Message):
    await message.answer(TARIFF_TEXT, reply_markup=tariffs_keyboard())


@router.callback_query(F.data == "buy")
async def buy(callback: CallbackQuery):
    await callback.answer()
    if isinstance(callback.message, Message):
        await tariffs(callback.message)


@router.callback_query(F.data.startswith("tariff:"))
async def select_tariff(callback: CallbackQuery, services: Services):
    tariff = TARIFFS.get((callback.data or "").partition(":")[2])
    if tariff is None:
        await callback.answer("Тариф не найден. Откройте меню тарифов.", show_alert=True)
        return
    await callback.answer()
    text = await services.payments.create_payment(callback.from_user.id, tariff)
    if isinstance(callback.message, Message):
        await callback.message.answer(text, reply_markup=back_keyboard(("💎 Выбрать тариф", "buy")))


@router.message(F.text == SUBSCRIPTION)
async def subscription(message: Message, services: Services):
    if message.from_user:
        await message.answer(await services.subscriptions.describe(message.from_user.id),
                             reply_markup=back_keyboard(("💎 Купить подписку", "buy")))
