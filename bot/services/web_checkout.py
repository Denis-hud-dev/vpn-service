"""Standalone browser presentation over existing isolated orders and fulfillment."""
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select

from bot.db.models import Payment, User
from bot.services import PILOT_TARIFFS
from bot.services.happ import AccessDenied
from bot.services.payments import _safe_confirmation_url
from bot.services.subscriptions import SubscriptionService, as_utc, utcnow


class OrderUnavailable(LookupError):
    pass


class WebCheckout:
    def __init__(self, sessions, checkout, processor, happ, owner_id: int, *, reviews=None):
        self.sessions, self.checkout, self.processor = sessions, checkout, processor
        self.happ, self.owner_id = happ, owner_id
        self.reviews = reviews

    def plans(self):
        return [{'id': p.id, 'label': p.label, 'amount_kopecks': p.price_rub * 100,
                 'months': p.months, 'style': 'success' if p.months == 1 else 'primary'}
                for p in PILOT_TARIFFS.values()]

    def _owner(self, user_id):
        if user_id != self.owner_id:
            raise OrderUnavailable()

    async def order(self, user_id: int, order_id: str):
        self._owner(user_id)
        try:
            key = UUID(order_id)
        except (ValueError, TypeError, AttributeError):
            raise OrderUnavailable() from None
        async with self.sessions() as session:
            payment = await session.scalar(select(Payment).join(User).where(
                Payment.id == key, User.telegram_id == user_id, Payment.provider == 'yookassa'))
        if payment is None or payment.tariff_id not in PILOT_TARIFFS:
            raise OrderUnavailable()
        if payment.amount_kopecks != PILOT_TARIFFS[payment.tariff_id].price_rub * 100:
            raise OrderUnavailable()
        return payment

    async def latest(self, user_id):
        self._owner(user_id)
        async with self.sessions() as session:
            key = await session.scalar(select(Payment.id).join(User).where(
                User.telegram_id == user_id, Payment.provider == 'yookassa',
                Payment.tariff_id.in_(PILOT_TARIFFS)).order_by(Payment.created_at.desc()).limit(1))
        return await self.state(user_id, str(key)) if key else None

    async def create(self, user_id, tariff_id):
        self._owner(user_id)
        if tariff_id not in PILOT_TARIFFS:
            raise ValueError('Unknown plan')
        intent = await self.checkout.create_for_user(user_id, tariff_id)
        if not _safe_confirmation_url(intent.confirmation_url, frozenset({'yookassa.ru', 'yoomoney.ru'})):
            raise ValueError('Invalid provider URL')
        async with self.sessions() as session:
            key = await session.scalar(select(Payment.id).join(User).where(
                User.telegram_id == user_id, Payment.provider_payment_id == intent.provider_id))
        result = await self.state(user_id, str(key))
        if result['state'] == 'pending':
            result['payment_url'] = intent.confirmation_url
        return result

    async def refresh(self, user_id, order_id):
        row = await self.order(user_id, order_id)
        if row.status == 'pending' and row.provider_payment_id:
            # The client independently GETs the authenticated test-shop status.
            await self.processor.process(row.provider_payment_id)
        return await self.state(user_id, order_id)

    async def state(self, user_id, order_id):
        row = await self.order(user_id, order_id)
        state = row.status
        if self.reviews and self.reviews.pending(row.id):
            state = 'refund_review'
        deadline = as_utc(row.created_at) + timedelta(minutes=30)
        if state == 'pending' and deadline <= utcnow():
            state = 'expired'
        result = {'id': str(row.id), 'state': state, 'label': PILOT_TARIFFS[row.tariff_id].label,
                  'amount_kopecks': row.amount_kopecks, 'currency': row.currency,
                  'expires_at': deadline.isoformat(), 'test': True,
                  'receipt': 'test_not_fiscal', 'seller_regime': 'npd', 'vat': 'not_applicable'}
        if state == 'succeeded':
            snapshot = await SubscriptionService(self.sessions).snapshot(user_id)
            result['subscription_status'] = snapshot.status
            result['subscription_expires_at'] = snapshot.expires_at.isoformat() if snapshot.expires_at else None
            try:
                link = await self.happ.issue_for_user(user_id)
                result['connect_url'] = self.happ.connection_page_url(link)
            except (AccessDenied, PermissionError, OSError, RuntimeError, TimeoutError):
                # Payment success and actual node readiness are distinct facts.
                result['access_pending'] = True
        return result
