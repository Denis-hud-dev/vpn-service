"""Verified full/latest test refunds; ambiguous adjustments require review."""
from contextlib import closing
from decimal import Decimal, InvalidOperation
import logging
import sqlite3

from sqlalchemy import select

from bot.db.models import Payment, Subscription, SubscriptionHistory, User
from bot.services.subscriptions import SubscriptionService, as_utc, utcnow
from bot.services.yookassa_verification import confirmed_from_lookup

logger = logging.getLogger(__name__)


class RefundReviews:
    """Private operational ledger; no payloads, card data or customer contacts."""
    def __init__(self, browser_sessions):
        self.path = browser_sessions.path
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS refund_reviews '
                       '(refund_id TEXT PRIMARY KEY, payment_id TEXT NOT NULL, state TEXT NOT NULL)')

    def record(self, refund_id, payment_id, state):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT INTO refund_reviews VALUES (?,?,?) ON CONFLICT(refund_id) '
                       'DO UPDATE SET state=excluded.state', (refund_id, str(payment_id), state))

    def pending(self, payment_id):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute('SELECT 1 FROM refund_reviews WHERE payment_id=? AND state=?',
                              (str(payment_id), 'review')).fetchone() is not None


class TestRefundProcessor:
    def __init__(self, sessions, lookup, reviews):
        self.sessions, self.lookup, self.reviews = sessions, lookup, reviews

    async def process(self, refund_id, provider_payment_id):
        async with self.sessions() as session:
            known = await session.scalar(select(Payment).where(
                Payment.provider == 'yookassa', Payment.provider_payment_id == provider_payment_id))
        if known is None:
            raise ValueError('Unknown refund')
        data = await self.lookup.fetch_refund(refund_id)
        if data.get('payment_id') != provider_payment_id or data.get('id') != refund_id:
            raise ValueError('Refund mismatch')
        parent_data = await self.lookup.fetch(provider_payment_id)
        if parent_data.get('test') is not True:
            raise ValueError('Test payment required')
        parent = confirmed_from_lookup(known.id, parent_data)
        if parent.amount_kopecks != known.amount_kopecks or parent.provider_payment_id != provider_payment_id:
            raise ValueError('Payment mismatch')
        try:
            amount = data['amount']
            if not isinstance(amount['value'], str):
                raise ValueError
            value = Decimal(amount['value']) * 100
            if amount['currency'] != 'RUB' or not value.is_finite() or value <= 0 or value != value.to_integral_value():
                raise ValueError
        except (KeyError, TypeError, InvalidOperation, ValueError):
            raise ValueError('Invalid refund amount') from None
        state = data.get('status')
        if state in {'pending', 'canceled'}:
            return state
        if state != 'succeeded' or value > known.amount_kopecks:
            raise ValueError('Invalid refund state')
        # Persist review before adjusting access, so a crash cannot hide a
        # verified refund. Replaying completes an interrupted adjustment safely.
        self.reviews.record(refund_id, known.id, 'review')
        if value != known.amount_kopecks:
            logger.warning('test_refund outcome=review_required kind=partial')
            return 'review'
        async with self.sessions.begin() as session:
            await session.scalar(select(User).where(User.id == known.user_id).with_for_update())
            payment = await session.scalar(select(Payment).where(Payment.id == known.id).with_for_update())
            history = await session.scalar(select(SubscriptionHistory).where(
                SubscriptionHistory.payment_id == payment.id, SubscriptionHistory.reason == 'payment'))
            sub = await session.scalar(select(Subscription).where(
                Subscription.id == payment.subscription_id).with_for_update())
            if payment.status == 'refunded':
                pass
            elif (payment.status != 'succeeded' or history is None or sub is None
                  or sub.expires_at is None or as_utc(sub.expires_at) != as_utc(history.new_expires_at)):
                logger.warning('test_refund outcome=review_required kind=later_changes')
                return 'review'
            else:
                before = (sub.status, sub.starts_at, sub.expires_at)
                restore = history.old_expires_at
                continuing = history.old_status == 'active' and restore and as_utc(restore) > utcnow()
                sub.expires_at = as_utc(restore) if continuing else utcnow()
                if sub.status != 'blocked':
                    sub.status = 'active' if continuing else 'expired'
                if continuing:
                    previous = await session.scalar(select(Payment).where(
                        Payment.subscription_id == sub.id, Payment.id != payment.id,
                        Payment.status == 'succeeded').order_by(Payment.paid_at.desc()).limit(1))
                    sub.tariff_id = previous.tariff_id if previous else 'restored_access'
                    if history.old_starts_at:
                        sub.starts_at = history.old_starts_at
                payment.status = 'refunded'
                SubscriptionService(self.sessions).history(session, sub, 'payment_refund', before=before)
        self.reviews.record(refund_id, known.id, 'applied')
        logger.info('test_refund outcome=applied')
        return 'refunded'
