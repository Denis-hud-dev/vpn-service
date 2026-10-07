from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import func, select, update
from bot.db.models import Device, Subscription, SubscriptionStatus, User
from bot.services import TARIFFS


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class SubscriptionService:
    def __init__(self, sessions):
        self.sessions = sessions

    async def create_pending(self, telegram_id: int, tariff_id: str) -> Subscription:
        if tariff_id not in TARIFFS:
            raise ValueError("Неизвестный тариф")
        async with self.sessions.begin() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id).with_for_update())
            if user is None:
                raise ValueError("Пользователь не зарегистрирован")
            current = await session.scalar(select(Subscription).where(Subscription.user_id == user.id))
            if current is not None:
                # Renewal must not overwrite an active/blocked subscription.
                return current
            current = Subscription(user_id=user.id, tariff_id=tariff_id, status="pending")
            session.add(current)
            await session.flush()
            return current

    async def set_status(self, subscription_id: UUID, status: SubscriptionStatus,
                         *, starts_at: datetime | None = None, expires_at: datetime | None = None) -> Subscription:
        status = SubscriptionStatus(status)
        async with self.sessions.begin() as session:
            sub = await session.scalar(select(Subscription).where(Subscription.id == subscription_id).with_for_update())
            if sub is None:
                raise ValueError("Подписка не найдена")
            transitions = {"pending": {"active", "blocked"}, "active": {"expired", "blocked"},
                           "expired": {"active", "pending", "blocked"}, "blocked": {"active", "expired"}}
            if status != sub.status and status not in transitions[sub.status]:
                raise ValueError("Недопустимый переход статуса")
            if status == SubscriptionStatus.ACTIVE:
                start = as_utc(starts_at or sub.starts_at) if (starts_at or sub.starts_at) else None
                end = as_utc(expires_at or sub.expires_at) if (expires_at or sub.expires_at) else None
                if start is None or end is None or start > utcnow() or end <= max(start, utcnow()):
                    raise ValueError("Для активной подписки нужны корректные даты в UTC")
                sub.starts_at, sub.expires_at = start, end
            sub.status = status.value
            await session.flush()
            return sub

    async def expire_due(self) -> int:
        async with self.sessions.begin() as session:
            result = await session.execute(update(Subscription).where(
                Subscription.status == "active", Subscription.expires_at <= utcnow()).values(status="expired"))
            return result.rowcount

    async def describe(self, telegram_id: int) -> str:
        async with self.sessions.begin() as session:
            sub = await session.scalar(select(Subscription).join(User).where(User.telegram_id == telegram_id).with_for_update())
            if sub is None:
                return "👤 Ваша подписка\n\nСтатус: не активна\nТариф: —\nДействует до: —\nУстройства: 0"
            if sub.status == "active" and sub.expires_at and as_utc(sub.expires_at) <= utcnow():
                sub.status = "expired"
            count = await session.scalar(select(func.count()).select_from(Device).where(
                Device.subscription_id == sub.id, Device.status != "revoked"))
            labels = {"active": "активна", "expired": "истекла", "pending": "ожидает активации", "blocked": "заблокирована"}
            tariff = TARIFFS.get(sub.tariff_id)
            date = as_utc(sub.expires_at).strftime("%d.%m.%Y %H:%M UTC") if sub.expires_at else "—"
            return f"👤 Ваша подписка\n\nСтатус: {labels[sub.status]}\nТариф: {tariff.label if tariff else sub.tariff_id}\nДействует до: {date}\nУстройства: {count}"
