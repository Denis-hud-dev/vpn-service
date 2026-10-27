"""Unwired Telegram TEST-server contract. No transport, storage or fulfillment.

Shape checks are not proof that an update came from Telegram. A future adapter
must authenticate the update source and use the canonical payment ledger before
any access grant. No bot handler or live settings import this prototype.
"""
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aiogram.client.telegram import TEST, TelegramAPIServer
from aiogram.methods import AnswerPreCheckoutQuery, SendInvoice
from aiogram.types import LabeledPrice, Message, PreCheckoutQuery


@dataclass(frozen=True)
class StarsTestOrder:
    # A reference to the future canonical order, not another persistent ledger.
    order_id: UUID
    test_user_id: int
    months: int
    stars: int
    expires_at: datetime
    price_approved: bool = False
    pending: bool = True

    @property
    def payload(self):
        return 'kuda-stars-test:'+self.order_id.hex


@dataclass(frozen=True)
class StarsObservation:
    order_id: UUID
    telegram_charge_id: str
    currency: str = 'XTR'
    environment: str = 'telegram_test'


class StarsSandbox:
    def __init__(self, *, enabled=False, api: TelegramAPIServer=TEST):
        if api != TEST:
            raise ValueError('Separate Telegram TEST environment required')
        self.enabled=enabled

    def _validate(self, order):
        if self.enabled is not True or order.price_approved is not True:
            raise PermissionError('Stars prototype and prices are not enabled')
        if (not isinstance(order.order_id,UUID) or type(order.test_user_id) is not int
                or order.test_user_id<=0 or type(order.months) is not int or order.months not in {1,3,12}
                or type(order.stars) is not int or order.stars<=0
                or not isinstance(order.expires_at,datetime) or order.expires_at.tzinfo is None):
            raise ValueError('Explicit test order in whole Stars required')

    def invoice(self, order, *, now):
        self._validate(order)
        if order.pending is not True or now>=order.expires_at:
            raise ValueError('Order unavailable')
        return SendInvoice(chat_id=order.test_user_id,title='KudaVPN Premium · тест',
            description=f'Тестовая подписка · {order.months} мес. · без реальных списаний',
            payload=order.payload,provider_token='',currency='XTR',
            prices=[LabeledPrice(label=f'{order.months} мес.',amount=order.stars)],
            start_parameter='stars_test_'+order.order_id.hex,protect_content=True)

    def precheckout(self, order, query: PreCheckoutQuery, *, now):
        self._validate(order)
        ok=(order.pending is True and now<order.expires_at and query.from_user.id==order.test_user_id
            and query.currency=='XTR' and query.total_amount==order.stars
            and query.invoice_payload==order.payload)
        return AnswerPreCheckoutQuery(pre_checkout_query_id=query.id,ok=ok,
            error_message=None if ok else 'Заказ недоступен. Откройте новый тестовый счёт.')

    def inspect_successful_payment(self, order, message: Message):
        self._validate(order)
        paid=message.successful_payment
        if (message.chat.type!='private' or message.chat.id!=order.test_user_id
                or message.from_user is None or message.from_user.id!=order.test_user_id
                or paid is None or paid.currency!='XTR' or paid.total_amount!=order.stars
                or paid.invoice_payload!=order.payload or not paid.telegram_payment_charge_id
                or paid.is_recurring or paid.is_first_recurring or paid.subscription_expiration_date):
            raise ValueError('Payment observation does not match the test order')
        # A late successful update must not disappear merely because a UI
        # deadline passed. Duplicate observations carry the same charge/order
        # key for future transactional deduplication; no grant happens here.
        return StarsObservation(order.order_id,paid.telegram_payment_charge_id)
