from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import func, select
from bot.db.models import Device, Subscription, SubscriptionStatus, User, Payment, SubscriptionHistory
from bot.services import TARIFFS, BILLABLE_TARIFFS
from bot.services.account import AccountSnapshot, SubscriptionEvent


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class SubscriptionService:
    def __init__(self, sessions, payment_verifier=None):
        from bot.services.payment_events import DisabledPaymentVerifier
        self.sessions = sessions
        self.payment_verifier = payment_verifier or DisabledPaymentVerifier()

    def history(self, session, sub, reason, *, before=None, payment_id=None):
        old = before or (None, None, None)
        session.add(SubscriptionHistory(subscription_id=sub.id, payment_id=payment_id,
            reason=reason, old_status=old[0], old_starts_at=old[1], old_expires_at=old[2],
            new_status=sub.status, new_starts_at=sub.starts_at, new_expires_at=sub.expires_at))

    async def recent_history(self, telegram_id: int, *, limit: int = 6) -> list[SubscriptionEvent]:
        """Read only the caller's real subscription transitions, never payment IDs."""
        async with self.sessions() as session:
            rows = (await session.scalars(
                select(SubscriptionHistory)
                .join(Subscription, SubscriptionHistory.subscription_id == Subscription.id)
                .join(User, Subscription.user_id == User.id)
                .where(User.telegram_id == telegram_id)
                .order_by(SubscriptionHistory.created_at.desc(), SubscriptionHistory.id.desc())
                .limit(max(1, min(limit, 20)))
            )).all()
            return [SubscriptionEvent(
                occurred_at=as_utc(row.created_at), reason=row.reason,
                old_status=row.old_status, new_status=row.new_status,
                old_expires_at=as_utc(row.old_expires_at) if row.old_expires_at else None,
                new_expires_at=as_utc(row.new_expires_at) if row.new_expires_at else None,
                new_starts_at=as_utc(row.new_starts_at) if row.new_starts_at else None,
            ) for row in rows]

    async def apply_confirmed_payment(self, event):
        """Internal boundary: caller must verify the event with the payment provider.

        No Telegram handler or webhook exposes this method. Lock the owner before
        payment/subscription rows, so concurrent purchases serialize in PostgreSQL.
        """
        from bot.services.payment_events import ConfirmedPayment, add_months
        if not isinstance(event, ConfirmedPayment):
            raise ValueError("Требуется подтверждённое платёжное событие")
        verified = await self.payment_verifier.verify(event)
        if not isinstance(verified, ConfirmedPayment) or verified != event:
            raise ValueError("Провайдер не подтвердил параметры оплаты")
        if as_utc(event.paid_at) > utcnow():
            raise ValueError("Дата оплаты находится в будущем")
        async with self.sessions.begin() as session:
            owner = await session.scalar(select(Payment.user_id).where(Payment.id == event.payment_id))
            if owner is None:
                raise ValueError("Платёж не найден")
            await session.scalar(select(User).where(User.id == owner).with_for_update())
            payment = await session.scalar(select(Payment).where(Payment.id == event.payment_id).with_for_update())
            if (payment.provider != event.provider or payment.amount_kopecks != event.amount_kopecks
                    or payment.currency != event.currency or not event.provider_payment_id
                    or (payment.provider_payment_id and payment.provider_payment_id != event.provider_payment_id)):
                raise ValueError("Параметры подтверждения не совпадают")
            applied = await session.scalar(select(SubscriptionHistory).where(SubscriptionHistory.payment_id == payment.id))
            if applied:
                return await session.get(Subscription, applied.subscription_id)
            if payment.status != "pending":
                raise ValueError("Платёж не может активировать подписку")
            tariff = BILLABLE_TARIFFS.get(payment.tariff_id)
            if tariff is None or payment.amount_kopecks != tariff.price_rub * 100:
                raise ValueError("Некорректный тариф или сумма")
            sub = await session.scalar(select(Subscription).where(Subscription.user_id == owner).with_for_update())
            now = utcnow()
            if sub is None:
                sub = Subscription(user_id=owner, tariff_id=tariff.id, status="pending")
                session.add(sub)
                await session.flush()
            before = (sub.status, sub.starts_at, sub.expires_at)
            if sub.status == "blocked":
                raise ValueError("Подписка заблокирована; требуется решение администратора")
            if payment.subscription_id and payment.subscription_id != sub.id:
                raise ValueError("Платёж относится к другой подписке")
            end = as_utc(sub.expires_at) if sub.expires_at else now
            continuing = sub.status == "active" and end > now
            sub.starts_at = as_utc(sub.starts_at) if continuing else now
            sub.expires_at = add_months(end if continuing else now, tariff.months)
            sub.status, sub.tariff_id = "active", tariff.id
            payment.status, payment.paid_at = "succeeded", as_utc(event.paid_at)
            payment.provider_payment_id, payment.subscription_id = event.provider_payment_id, sub.id
            self.history(session, sub, "payment", before=before, payment_id=payment.id)
            await session.flush()
            return sub

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
            self.history(session, current, "created")
            return current

    async def set_status(self, subscription_id: UUID, status: SubscriptionStatus,
                         *, starts_at: datetime | None = None, expires_at: datetime | None = None) -> Subscription:
        status = SubscriptionStatus(status)
        if status == SubscriptionStatus.ACTIVE:
            raise ValueError("Активация доступна только через подтверждённый платёж")
        async with self.sessions.begin() as session:
            sub = await session.scalar(select(Subscription).where(Subscription.id == subscription_id).with_for_update())
            if sub is None:
                raise ValueError("Подписка не найдена")
            transitions = {"pending": {"active", "blocked"}, "active": {"expired", "blocked"},
                           "expired": {"active", "pending", "blocked"}, "blocked": {"active", "expired"}}
            if status != sub.status and status not in transitions[sub.status]:
                raise ValueError("Недопустимый переход статуса")
            before = (sub.status, sub.starts_at, sub.expires_at)
            sub.status = status.value
            if before[0] != sub.status:
                self.history(session, sub, "status", before=before)
            await session.flush()
            return sub

    async def expire_due(self) -> int:
        async with self.sessions.begin() as session:
            rows = (await session.scalars(select(Subscription).where(
                Subscription.status == "active", Subscription.expires_at <= utcnow()).with_for_update())).all()
            for sub in rows:
                before = (sub.status, sub.starts_at, sub.expires_at)
                sub.status = "expired"
                self.history(session, sub, "expired", before=before)
            return len(rows)

    async def snapshot(self, telegram_id: int) -> AccountSnapshot:
        async with self.sessions.begin() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                return AccountSnapshot()
            sub = await session.scalar(select(Subscription).where(Subscription.user_id == user.id).with_for_update())
            count = await session.scalar(select(func.count()).select_from(Device).where(
                Device.user_id == user.id, Device.status != "revoked"))
            if sub is None:
                return AccountSnapshot(device_count=count)
            if sub.status == "active" and sub.expires_at and as_utc(sub.expires_at) <= utcnow():
                before = (sub.status, sub.starts_at, sub.expires_at)
                sub.status = "expired"
                self.history(session, sub, "expired", before=before)
            tariff = BILLABLE_TARIFFS.get(sub.tariff_id)
            label = ('Пробный доступ — 24 часа' if sub.tariff_id == 'pilot_trial_24h'
                     else 'Доступ до возвращённой оплаты' if sub.tariff_id == 'restored_access'
                     else tariff.label if tariff else sub.tariff_id)
            return AccountSnapshot(status=sub.status, tariff_label=label,
                expires_at=as_utc(sub.expires_at) if sub.expires_at else None, device_count=count)

    async def describe(self, telegram_id: int) -> str:
        # Compatibility for backend consumers of the original plain-text summary.
        snapshot = await self.snapshot(telegram_id)
        labels = {"inactive": "не активна", "active": "активна", "expired": "истекла",
                  "pending": "ожидает активации", "blocked": "заблокирована"}
        date = snapshot.expires_at.strftime("%d.%m.%Y %H:%M UTC") if snapshot.expires_at else "—"
        return (f"👤 Ваша подписка\n\nСтатус: {labels[snapshot.status]}\n"
                f"Тариф: {snapshot.tariff_label or '—'}\nДействует до: {date}\n"
                f"Устройства: {snapshot.device_count if snapshot.device_count is not None else 'Нет данных'}")
