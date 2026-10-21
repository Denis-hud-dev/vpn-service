"""Owner-only pilot notices, derived from verified DB state without bearer links."""
import asyncio
import logging
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select

from bot.db.models import Payment, PremiumNotice, Subscription, SubscriptionHistory, User
from bot.services.subscriptions import as_utc, utcnow

logger = logging.getLogger(__name__)


class PremiumNoticeService:
    def __init__(self, sessions, owner_telegram_id: int):
        if owner_telegram_id <= 0:
            raise ValueError('Pilot owner is required')
        self.sessions = sessions
        self.owner_telegram_id = owner_telegram_id

    async def enqueue(self) -> None:
        """Never infer a payment from a redirect or pending provider record."""
        async with self.sessions.begin() as session:
            paid_ids = (await session.scalars(select(Payment.id)
                .join(User, Payment.user_id == User.id)
                .join(SubscriptionHistory, SubscriptionHistory.payment_id == Payment.id)
                .where(User.telegram_id == self.owner_telegram_id,
                       Payment.status == 'succeeded',
                       Payment.paid_at >= utcnow() - timedelta(days=7),
                       SubscriptionHistory.reason == 'payment'))).all()
            keys = [f'payment:{payment_id}' for payment_id in paid_ids]
            sub = await session.scalar(select(Subscription).join(User).where(
                User.telegram_id == self.owner_telegram_id))
            if sub and sub.status == 'active' and sub.expires_at:
                end = as_utc(sub.expires_at)
                remaining = end - utcnow()
                threshold = timedelta(hours=1) if sub.tariff_id == 'pilot_trial_24h' else timedelta(hours=24)
                if timedelta(0) < remaining <= threshold:
                    keys.append(f'expiry:{sub.id}:{int(end.timestamp())}')
            existing = set((await session.scalars(select(PremiumNotice.event_key).where(
                PremiumNotice.event_key.in_(keys)))).all()) if keys else set()
            for key in keys:
                if key not in existing:
                    session.add(PremiumNotice(event_key=key, telegram_id=self.owner_telegram_id,
                                              kind=key.partition(':')[0]))

    async def _message(self, key: str) -> str | None:
        async with self.sessions() as session:
            kind, _, value = key.partition(':')
            if kind == 'payment':
                try:
                    payment_id = UUID(value)
                except ValueError:
                    return None
                row = await session.execute(select(Payment, SubscriptionHistory)
                    .join(User, Payment.user_id == User.id)
                    .join(SubscriptionHistory, SubscriptionHistory.payment_id == Payment.id)
                    .where(Payment.id == payment_id, User.telegram_id == self.owner_telegram_id,
                           Payment.status == 'succeeded', Payment.paid_at >= utcnow() - timedelta(days=7),
                           SubscriptionHistory.reason == 'payment'))
                result = row.one_or_none()
                if result is None:
                    return None
                _, history = result
                renewed = (history.old_expires_at is not None and
                           as_utc(history.old_expires_at) > as_utc(history.new_starts_at))
                action = 'продлена' if renewed else 'активирована'
                return (f'✦ KudaVPN Premium\n\nТестовая оплата подтверждена. Подписка {action}. '
                        'Срок доступа указан в личном кабинете. Подключение может обновиться в течение минуты.')
            if kind == 'expiry':
                try:
                    sub_id, expiry = value.split(':', 1)
                    expiry_ts = int(expiry)
                    sub = await session.scalar(select(Subscription).join(User).where(
                        Subscription.id == UUID(sub_id), User.telegram_id == self.owner_telegram_id))
                except (ValueError, TypeError):
                    return None
                if sub is None or sub.status != 'active' or not sub.expires_at:
                    return None
                remaining = as_utc(sub.expires_at) - utcnow()
                threshold = timedelta(hours=1) if sub.tariff_id == 'pilot_trial_24h' else timedelta(hours=24)
                if int(as_utc(sub.expires_at).timestamp()) != expiry_ts or not timedelta(0) < remaining <= threshold:
                    return None
                label = 'менее часа' if sub.tariff_id == 'pilot_trial_24h' else 'менее суток'
                return (f'✦ KudaVPN Premium\n\nДо окончания подписки осталось {label}. '
                        'Продлите её через «Личный кабинет», чтобы сохранить доступ и прежнюю ссылку Happ.')
        return None

    async def deliver(self, bot) -> None:
        now = utcnow()
        async with self.sessions.begin() as session:
            rows = (await session.scalars(select(PremiumNotice).where(
                PremiumNotice.telegram_id == self.owner_telegram_id,
                PremiumNotice.sent_at.is_(None)).order_by(PremiumNotice.event_key)
                .with_for_update(skip_locked=True).limit(10))).all()
            keys = []
            for row in rows:
                if row.claimed_until and as_utc(row.claimed_until) > now:
                    continue
                row.claimed_until = now + timedelta(minutes=5)
                keys.append(row.event_key)
        for key in keys:
            message = await self._message(key)
            if message is not None:
                try:
                    await bot.send_message(self.owner_telegram_id, message)
                except Exception as exc:
                    logger.warning('premium_notice outcome=retry error=%s', type(exc).__name__)
                    # The lease allows retry without leaking provider data in logs.
                    continue
            async with self.sessions.begin() as session:
                row = await session.get(PremiumNotice, key)
                if row and row.sent_at is None:
                    row.sent_at = utcnow()

    async def tick(self, bot) -> None:
        await self.enqueue()
        await self.deliver(bot)


async def run_premium_notices(service: PremiumNoticeService, bot, interval: int = 60) -> None:
    while True:
        try:
            await service.tick(bot)
        except Exception as exc:
            logger.warning('premium_notice outcome=retry error=%s', type(exc).__name__)
        await asyncio.sleep(interval)
