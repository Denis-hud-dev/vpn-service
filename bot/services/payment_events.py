"""Trusted backend contract; a DTO does not itself verify a provider signature."""
import calendar
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from typing import Protocol


@dataclass(frozen=True)
class ConfirmedPayment:
    payment_id: UUID
    provider: str
    provider_payment_id: str
    amount_kopecks: int
    currency: str
    paid_at: datetime

    def __post_init__(self):
        if self.paid_at.tzinfo is None or self.amount_kopecks <= 0:
            raise ValueError("Некорректное подтверждение оплаты")


def add_months(value: datetime, months: int) -> datetime:
    index = value.year * 12 + value.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    return value.replace(year=year, month=month,
        day=min(value.day, calendar.monthrange(year, month)[1]))


class PaymentVerifier(Protocol):
    async def verify(self, event: ConfirmedPayment) -> ConfirmedPayment:
        """Fetch successful payment from provider; verify identity, amount and ownership.

        Never echo an untrusted webhook as confirmation. Production implementation
        is deliberately absent until authenticated provider verification is added.
        """
        ...


class DisabledPaymentVerifier:
    async def verify(self, event: ConfirmedPayment) -> ConfirmedPayment:
        raise RuntimeError("Проверка платежа у провайдера не подключена")
