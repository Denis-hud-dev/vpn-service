"""Offline handler for statuses fetched independently from YooKassa.

The provider lookup is an injected, authenticated GET adapter. This module
has no HTTP endpoint, credentials or network access of its own. Never pass a
webhook body as the lookup result: it is merely a hint to re-fetch the payment.
"""
from uuid import UUID

from sqlalchemy import select

from bot.db.models import Payment, SubscriptionHistory
from bot.services.subscriptions import SubscriptionService
from bot.services.yookassa_verification import (
    PaymentLookup, YooKassaPaymentVerifier, confirmed_from_lookup,
    identity_from_lookup,
)


class PaymentNotificationProcessor:
    def __init__(self, sessions, lookup: PaymentLookup, *, refund_reviews=None):
        self.sessions = sessions
        self.lookup = lookup
        self.lifecycle = SubscriptionService(sessions, YooKassaPaymentVerifier(lookup))
        self.refund_reviews = refund_reviews

    async def process(self, provider_payment_id: str) -> str:
        """Return ``succeeded``, ``canceled`` or ``pending`` after provider GET.

        A repeated notification is harmless; a mismatched or unverified
        provider response fails closed. The payment's user is always selected
        from our order row, never from callback data.
        """
        if not isinstance(provider_payment_id, str) or not provider_payment_id:
            raise ValueError("Провайдер не подтвердил параметры оплаты")
        # An untrusted webhook may name any ID. Never query YooKassa or reveal
        # order state for a payment that our checkout has not bound yet.
        async with self.sessions() as session:
            known = await session.scalar(select(Payment.id).where(
                Payment.provider == 'yookassa',
                Payment.provider_payment_id == provider_payment_id))
        if known is None:
            raise ValueError("Платёж не найден")
        if self.refund_reviews is not None and self.refund_reviews.pending(known):
            async with self.sessions() as session:
                applied = await session.scalar(select(SubscriptionHistory.id).where(
                    SubscriptionHistory.payment_id == known, SubscriptionHistory.reason == 'payment'))
            if applied is None:
                raise ValueError("Возврат требует ручного разбора до активации")
        data = await self.lookup.fetch(provider_payment_id)
        try:
            order_id = UUID(data['metadata']['order_id'])
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ValueError("Провайдер не подтвердил параметры оплаты") from None
        if order_id != known:
            # A provider response must describe the exact locally bound order,
            # never another pending order that happens to have the same amount.
            raise ValueError("Провайдер не подтвердил параметры оплаты")
        identity = identity_from_lookup(order_id, data)
        if identity.provider_id != provider_payment_id:
            raise ValueError("Провайдер не подтвердил параметры оплаты")

        status = data.get('status')
        if status == 'succeeded':
            event = confirmed_from_lookup(order_id, data)
            await self.lifecycle.apply_confirmed_payment(event)
            return 'succeeded'
        if status not in {'canceled', 'pending', 'waiting_for_capture'}:
            raise ValueError("Неподдерживаемый статус платежа")
        if status == 'canceled' and data.get('paid') is not False:
            raise ValueError("Провайдер не подтвердил отмену")

        async with self.sessions.begin() as session:
            payment = await session.scalar(select(Payment).where(Payment.id == order_id).with_for_update())
            if (payment is None or payment.provider != 'yookassa'
                    or payment.amount_kopecks != identity.amount_kopecks
                    or payment.currency != 'RUB'
                    or payment.provider_payment_id != provider_payment_id):
                raise ValueError("Параметры платежа не совпадают с заказом")
            if status == 'canceled':
                if payment.status == 'succeeded':
                    raise ValueError("Оплаченный заказ не может стать отменённым")
                payment.status = 'canceled'
                payment.provider_payment_id = provider_payment_id
                return 'canceled'
            if payment.status != 'pending':
                raise ValueError("Заказ уже завершён")
            return 'pending'
