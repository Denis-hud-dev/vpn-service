"""Parse an authenticated provider lookup; this module makes no HTTP calls.

The caller must supply a fetcher that independently reads GET /v3/payments/{id}
from YooKassa with the intended shop credentials. Webhook JSON is untrusted.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from uuid import UUID

from bot.services.payment_events import ConfirmedPayment


class PaymentLookup(Protocol):
    async def fetch(self, provider_payment_id: str) -> dict[str, Any]: ...


def confirmed_from_lookup(payment_id: UUID, data: dict[str, Any]) -> ConfirmedPayment:
    """Reject any incomplete or non-final provider response, without logging it."""
    try:
        if data['status'] != 'succeeded' or data['paid'] is not True:
            raise ValueError
        provider_id = data['id']
        if not isinstance(provider_id, str) or not provider_id:
            raise ValueError
        if data['metadata']['order_id'] != str(payment_id):
            raise ValueError
        amount = data['amount']
        if amount['currency'] != 'RUB' or not isinstance(amount['value'], str):
            raise ValueError
        value = Decimal(amount['value'])
        kopecks = value * 100
        if not value.is_finite() or kopecks != kopecks.to_integral_value() or kopecks <= 0:
            raise ValueError
        captured = data['captured_at']
        if not isinstance(captured, str):
            raise ValueError
        paid_at = datetime.fromisoformat(captured.replace('Z', '+00:00'))
        return ConfirmedPayment(payment_id, 'yookassa', provider_id,
                                int(kopecks), 'RUB', paid_at)
    except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError):
        raise ValueError('Провайдер не подтвердил параметры оплаты') from None


class YooKassaPaymentVerifier:
    """Opt-in only; never inject a webhook body as the PaymentLookup."""

    def __init__(self, lookup: PaymentLookup):
        self.lookup = lookup

    async def verify(self, event: ConfirmedPayment) -> ConfirmedPayment:
        data = await self.lookup.fetch(event.provider_payment_id)
        result = confirmed_from_lookup(event.payment_id, data)
        if result != event:
            raise ValueError('Провайдер не подтвердил параметры оплаты')
        return result
