"""Contracts for future YooKassa/SBP integration; no HTTP calls today."""
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID
from sqlalchemy import select
from bot.db.models import Payment, User
from bot.services import Tariff


@dataclass(frozen=True)
class PaymentIntent:
    provider_id: str
    confirmation_url: str


class PaymentGateway(Protocol):
    async def create(self, *, amount_kopecks: int, currency: str,
                     idempotency_key: UUID, return_url: str) -> PaymentIntent: ...
    async def verify(self, provider_id: str) -> str: ...


class YooKassaGateway:
    """Disabled until credentials, HTTP adapter and verified webhook are implemented."""
    async def create(self, **kwargs) -> PaymentIntent:
        raise RuntimeError("YooKassa пока не подключена")

    async def verify(self, provider_id: str) -> str:
        raise RuntimeError("YooKassa пока не подключена")


class PaymentRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    async def create_pending(self, telegram_id: int, tariff: Tariff, idempotency_key: UUID) -> Payment:
        async with self.sessions.begin() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id).with_for_update())
            if user is None:
                raise ValueError("Пользователь не зарегистрирован")
            existing = await session.scalar(select(Payment).where(Payment.idempotency_key == idempotency_key))
            if existing:
                if existing.user_id != user.id or existing.tariff_id != tariff.id or existing.amount_kopecks != tariff.price_rub * 100:
                    raise ValueError("Ключ идемпотентности уже используется")
                return existing
            payment = Payment(user_id=user.id, tariff_id=tariff.id, amount_kopecks=tariff.price_rub * 100,
                              idempotency_key=idempotency_key, currency="RUB", provider="yookassa", status="pending")
            session.add(payment)
            await session.flush()
            return payment
