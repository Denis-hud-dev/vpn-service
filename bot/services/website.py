"""Multi-account presentation over the existing payment and Happ lifecycle."""
from sqlalchemy import select, or_, and_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from bot.db.models import NPDReceipt, NPDReceiptEvent, Payment, User
from bot.services.subscriptions import SubscriptionService, as_utc
from bot.services.happ import AccessDenied
from bot.services import BILLABLE_TARIFFS
from bot.services.web_checkout import WebCheckout


class WebsiteCheckout(WebCheckout):
    def _owner(self, user_id):
        # Website HTTP supplies this exclusively from a verified server session.
        # Existing order queries still join Payment -> User and match this owner.
        if type(user_id) is not int or user_id <= 0:
            raise PermissionError('Authentication required')

    async def account(self, owner):
        snapshot = await SubscriptionService(self.sessions).snapshot(owner)
        result = {'status': snapshot.status, 'tariff': snapshot.tariff_label,
            'expires_at': snapshot.expires_at.isoformat() if snapshot.expires_at else None,
            'nodes': [], 'access_pending': False}
        if snapshot.status == 'active':
            try:
                result['nodes'] = list(await self.happ.available_node_names_for_user(owner))
                link = await self.happ.issue_for_user(owner)
                result['connect_url'] = self.happ.connection_page_url(link)
            except (AccessDenied, PermissionError, OSError, RuntimeError, TimeoutError):
                result['access_pending'] = True
        return result

    async def connection(self, owner):
        """The HTTP layer supplies the owner exclusively from its session."""
        self._owner(owner)
        snapshot = await SubscriptionService(self.sessions).snapshot(owner)
        if snapshot.status != 'active' or snapshot.expires_at is None:
            raise PermissionError('Active subscription required')
        # Existing idempotent Happ service; never create another subscription.
        return await self.happ.issue_for_user(owner), int(snapshot.expires_at.timestamp())


class NPDLedger:
    """Recoverable obligations, not a provider receipt or a My Tax integration.

    Reconciliation can repeat after payment confirmation/crash. No network or
    money movement occurs here. A live ledger is testable offline, but the site
    entrypoint accepts test=True only and has no operator HTTP mutation route.
    """
    def __init__(self, sessions, *, test=True):
        self.sessions, self.test = sessions, test

    async def reconcile(self, owner):
        async with self.sessions.begin() as session:
            # Shared owner lock serializes receipt work and existing fulfillment.
            user = await session.scalar(select(User).where(User.telegram_id == owner).with_for_update())
            if user is None:
                raise PermissionError('Authentication required')
            payments = (await session.scalars(select(Payment).outerjoin(NPDReceipt,
                NPDReceipt.payment_id == Payment.id).where(Payment.user_id == user.id,
                Payment.status.in_(['succeeded', 'refunded']), or_(NPDReceipt.payment_id.is_(None),
                    and_(Payment.status == 'refunded', NPDReceipt.state.not_in(
                        ['test_not_required', 'void_pending', 'voided']))))
                .order_by(Payment.created_at).limit(100))).all()
            insert = pg_insert if session.bind.dialect.name == 'postgresql' else sqlite_insert
            for payment in payments:
                initial = 'test_not_required' if self.test else ('voided' if payment.status == 'refunded' else 'pending')
                created = (await session.execute(insert(NPDReceipt).values(payment_id=payment.id,
                    environment='test' if self.test else 'live', state=initial, amount_kopecks=payment.amount_kopecks)
                    .on_conflict_do_nothing().returning(NPDReceipt.payment_id))).scalar_one_or_none()
                if created:
                    session.add(NPDReceiptEvent(operation='initial:'+str(payment.id), payment_id=payment.id, state=initial))
                row = await session.get(NPDReceipt, payment.id)
                if row.environment != ('test' if self.test else 'live'):
                    raise ValueError('Receipt environment mismatch')
                if not self.test and payment.status == 'refunded' and row.state not in {'voided', 'void_pending'}:
                    row.state = 'void_pending' if row.document_ref else 'voided'
                    await session.execute(insert(NPDReceiptEvent).values(operation='refund:'+str(payment.id),
                        payment_id=payment.id, state=row.state).on_conflict_do_nothing())

    async def tick(self):
        async with self.sessions() as session:
            owners = (await session.scalars(select(User.telegram_id).join(Payment, Payment.user_id == User.id)
                .outerjoin(NPDReceipt, NPDReceipt.payment_id == Payment.id)
                .where(Payment.status.in_(['succeeded', 'refunded']), or_(NPDReceipt.payment_id.is_(None),
                    and_(Payment.status == 'refunded', NPDReceipt.state.not_in(
                        ['test_not_required', 'void_pending', 'voided'])))).distinct().limit(100))).all()
        for owner in owners:
            await self.reconcile(owner)

    async def history(self, owner):
        await self.reconcile(owner)
        async with self.sessions() as session:
            rows = (await session.execute(select(Payment, NPDReceipt).join(User, Payment.user_id == User.id)
                .outerjoin(NPDReceipt, NPDReceipt.payment_id == Payment.id)
                .where(User.telegram_id == owner).order_by(Payment.created_at.desc()).limit(30))).all()
            if any(r and r.environment != ('test' if self.test else 'live') for _, r in rows):
                raise ValueError('Receipt environment mismatch')
            return [{'order': str(p.id), 'amount_kopecks': p.amount_kopecks, 'currency': p.currency,
                'status': p.status, 'tariff': BILLABLE_TARIFFS[p.tariff_id].label if p.tariff_id in BILLABLE_TARIFFS else 'Исторический тариф', 'created_at': as_utc(p.created_at).isoformat(),
                'receipt': r.state if r else 'not_due', 'receipt_error': r.error_code if r else None,
                'test': self.test} for p, r in rows]

    async def record(self, payment_id, state, *, operation, document_ref=None,
                     error_code=None, operator_confirmed=False,
                     registration_confirmed=False, delivery_confirmed=False):
        """Internal future operator boundary; no public website route calls this."""
        import re
        if (operator_confirmed is not True or self.test
                or state == 'issued' and registration_confirmed is not True
                or state == 'delivered' and delivery_confirmed is not True
                or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', operation)
                or error_code not in {None, 'my_tax_unavailable', 'delivery_failed', 'operator_review'}
                or document_ref is not None and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', document_ref)):
            raise PermissionError('Receipt operation unavailable')
        async with self.sessions.begin() as session:
            row = await session.scalar(select(NPDReceipt).where(NPDReceipt.payment_id == payment_id).with_for_update())
            prior = await session.get(NPDReceiptEvent, operation)
            if prior:
                if (prior.payment_id != payment_id or prior.state != state or prior.error_code != error_code
                        or state == 'issued' and prior.document_ref != document_ref):
                    raise ValueError('Receipt operation conflict')
                return
            transitions = {'pending': {'issued', 'error'}, 'error': {'issued', 'delivered', 'error', 'void_pending', 'voided'},
                           'issued': {'delivered', 'void_pending', 'error'}, 'delivered': {'void_pending', 'error'},
                           'void_pending': {'voided', 'error'}}
            payment = await session.get(Payment, payment_id)
            if (row is None or row.environment != 'live' or state not in transitions.get(row.state, set())
                    or state == 'issued' and (not document_ref or payment.status != 'succeeded')
                    or state == 'delivered' and (not row.document_ref or payment.status != 'succeeded')
                    or state == 'error' and not error_code
                    or state in {'void_pending', 'voided'} and payment.status != 'refunded'):
                raise ValueError('Invalid receipt transition')
            row.state, row.error_code = state, error_code
            if document_ref:
                row.document_ref = document_ref
            session.add(NPDReceiptEvent(operation=operation, payment_id=payment_id, state=state,
                error_code=error_code, document_ref=row.document_ref))
