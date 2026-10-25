"""Offline order boundaries for future YooKassa/SBP integration.

No production gateway, webhook endpoint or credential use is wired here.
"""
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from uuid import UUID, uuid4
from urllib.parse import urlsplit
from sqlalchemy import select
from bot.db.models import Payment, User
from bot.services import Tariff
from bot.services.subscriptions import as_utc, utcnow


@dataclass(frozen=True)
class PaymentIntent:
    provider_id: str
    confirmation_url: str


class PaymentGateway(Protocol):
    async def create(self, *, amount_kopecks: int, currency: str,
                     idempotency_key: UUID, order_id: UUID,
                     return_url: str) -> PaymentIntent: ...
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

    async def create_or_reuse_pending(self, telegram_id: int, tariff: Tariff) -> Payment:
        """Serialize repeated button presses on the user row, reusing a recent order."""
        async with self.sessions.begin() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id).with_for_update())
            if user is None:
                raise ValueError('Пользователь не зарегистрирован')
            rows = (await session.scalars(select(Payment).where(
                Payment.user_id == user.id, Payment.tariff_id == tariff.id,
                Payment.provider == 'yookassa', Payment.status == 'pending')
                .order_by(Payment.created_at.desc()).limit(1))).all()
            recent = rows[0] if rows else None
            if recent and recent.created_at and as_utc(recent.created_at) > utcnow() - timedelta(minutes=30):
                return recent
            payment = Payment(user_id=user.id, tariff_id=tariff.id,
                              amount_kopecks=tariff.price_rub * 100,
                              idempotency_key=uuid4(), currency='RUB',
                              provider='yookassa', status='pending')
            session.add(payment)
            await session.flush()
            return payment

    async def pending_provider_ids(self) -> list[str]:
        async with self.sessions() as session:
            rows = await session.scalars(select(Payment.provider_payment_id).where(
                Payment.provider == 'yookassa', Payment.status == 'pending',
                Payment.provider_payment_id.is_not(None))
                .order_by(Payment.created_at))
            return [value for value in rows if value]

    async def bind_provider_id(self, payment_id: UUID, provider_id: str) -> None:
        if not provider_id or len(provider_id) > 128:
            raise ValueError("Некорректный идентификатор платежа у провайдера")
        async with self.sessions.begin() as session:
            payment = await session.scalar(select(Payment).where(Payment.id == payment_id).with_for_update())
            if payment is None or payment.status != "pending":
                raise ValueError("Заказ не ожидает оплату")
            if payment.provider_payment_id and payment.provider_payment_id != provider_id:
                raise ValueError("Заказ связан с другим платежом")
            payment.provider_payment_id = provider_id


def _safe_confirmation_url(value: str, allowed_hosts: frozenset[str]) -> bool:
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == "https" and parsed.hostname in allowed_hosts
                and parsed.username is None and parsed.password is None
                and parsed.port in (None, 443) and not parsed.fragment)
    except (TypeError, ValueError):
        return False


class PaymentCheckoutService:
    """Prepare an order with a stable idempotency key; no bot wiring by default.

    The injected gateway must use an authenticated provider API and include
    ``order_id`` in provider metadata. Failed creates leave a pending order;
    retrying with the same key must return the same provider payment.
    """

    def __init__(self, sessions, gateway: PaymentGateway, return_url: str,
                 allowed_hosts: frozenset[str] = frozenset({"yoomoney.ru", "yookassa.ru"}),
                 *, tariffs=None):
        from bot.services import TARIFFS
        parsed_return = urlsplit(return_url)
        if (parsed_return.scheme != "https" or not parsed_return.hostname
                or parsed_return.username or parsed_return.password):
            raise ValueError("Для возврата требуется HTTPS")
        self.repository = PaymentRepository(sessions)
        self.gateway = gateway
        self.return_url = return_url
        self.allowed_hosts = allowed_hosts
        self.tariffs = dict(TARIFFS if tariffs is None else tariffs)

    async def create(self, telegram_id: int, tariff_id: str,
                     idempotency_key: UUID) -> PaymentIntent:
        tariff = self.tariffs.get(tariff_id)
        if tariff is None:
            raise ValueError("Неизвестный тариф")
        payment = await self.repository.create_pending(telegram_id, tariff, idempotency_key)
        return await self._start(payment)

    async def create_for_user(self, telegram_id: int, tariff_id: str) -> PaymentIntent:
        """Owner-only bot entry point; callback retries keep the same order/key."""
        tariff = self.tariffs.get(tariff_id)
        if tariff is None:
            raise ValueError('Неизвестный тариф')
        payment = await self.repository.create_or_reuse_pending(telegram_id, tariff)
        return await self._start(payment)

    async def _start(self, payment: Payment) -> PaymentIntent:
        if payment.status != "pending":
            raise ValueError("Заказ больше не ожидает оплату")
        intent = await self.gateway.create(
            amount_kopecks=payment.amount_kopecks, currency=payment.currency,
            idempotency_key=payment.idempotency_key, order_id=payment.id,
            return_url=self.return_url)
        if not isinstance(intent, PaymentIntent) or not intent.provider_id:
            raise ValueError("Провайдер не вернул платёж")
        if not _safe_confirmation_url(intent.confirmation_url, self.allowed_hosts):
            raise ValueError("Провайдер вернул неподтверждённый адрес оплаты")
        await self.repository.bind_provider_id(payment.id, intent.provider_id)
        return intent
