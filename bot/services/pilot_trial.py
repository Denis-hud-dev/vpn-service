"""Operator-only free trial in the isolated pilot database. No payment is created."""
from datetime import datetime, timedelta
from sqlalchemy import select, exists

from bot.db.models import Subscription, SubscriptionHistory, User
from bot.services.subscriptions import SubscriptionService, as_utc, utcnow

TRIAL_DURATION = timedelta(hours=24)


class PilotTrialService:
    def __init__(self, sessions, owner_telegram_id: int):
        if owner_telegram_id <= 0:
            raise ValueError('A positive pilot owner ID is required')
        self.sessions = sessions
        self.owner_telegram_id = owner_telegram_id

    async def grant_once(self, telegram_id: int) -> Subscription:
        """First invocation grants 24 hours; repeats never reset the clock."""
        if telegram_id != self.owner_telegram_id:
            raise PermissionError('Pilot trial is restricted to its owner')
        async with self.sessions.begin() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id).with_for_update())
            if user is None:
                raise ValueError('Pilot owner must register with /start first')
            sub = await session.scalar(select(Subscription).where(Subscription.user_id == user.id).with_for_update())
            if sub is not None:
                already_granted = await session.scalar(select(exists().where(
                    SubscriptionHistory.subscription_id == sub.id,
                    SubscriptionHistory.reason == 'pilot_trial_grant')))
                if already_granted:
                    return sub
                if sub.status not in ('pending', 'expired'):
                    raise ValueError('Existing subscription cannot be replaced by a trial')
                # An expired paid subscription may not be converted to a free trial.
                paid_history = await session.scalar(select(exists().where(
                    SubscriptionHistory.subscription_id == sub.id,
                    SubscriptionHistory.payment_id.is_not(None))))
                if paid_history:
                    raise ValueError('Existing paid subscription cannot be replaced by a trial')
            else:
                sub = Subscription(user_id=user.id, tariff_id='pilot_trial_24h', status='pending')
                session.add(sub)
                await session.flush()
            before = (sub.status, sub.starts_at, sub.expires_at)
            now = utcnow()
            sub.tariff_id = 'pilot_trial_24h'
            sub.status, sub.starts_at, sub.expires_at = 'active', now, now + TRIAL_DURATION
            SubscriptionService(self.sessions).history(session, sub, 'pilot_trial_grant', before=before)
            await session.flush()
            return sub

    async def extend_to(self, telegram_id: int, expires_at: datetime) -> Subscription:
        """Operator-provided absolute expiry makes retries idempotent; UUID stays put."""
        if telegram_id != self.owner_telegram_id:
            raise PermissionError('Pilot trial is restricted to its owner')
        target = as_utc(expires_at)
        if target <= utcnow() or target > utcnow() + timedelta(days=7):
            raise ValueError('Pilot extension must be future-dated and at most seven days')
        async with self.sessions.begin() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id).with_for_update())
            if user is None:
                raise ValueError('Pilot owner is not registered')
            sub = await session.scalar(select(Subscription).where(Subscription.user_id == user.id).with_for_update())
            if sub is None or sub.tariff_id != 'pilot_trial_24h':
                raise ValueError('Pilot trial does not exist')
            granted = await session.scalar(select(exists().where(
                SubscriptionHistory.subscription_id == sub.id,
                SubscriptionHistory.reason == 'pilot_trial_grant')))
            if not granted or sub.status == 'blocked':
                raise ValueError('Pilot trial is not renewable')
            if sub.expires_at and as_utc(sub.expires_at) >= target:
                return sub
            before = (sub.status, sub.starts_at, sub.expires_at)
            sub.status = 'active'
            sub.starts_at = utcnow() if sub.expires_at and as_utc(sub.expires_at) <= utcnow() else sub.starts_at
            sub.expires_at = target
            SubscriptionService(self.sessions).history(session, sub, 'pilot_trial_extend', before=before)
            await session.flush()
            return sub
