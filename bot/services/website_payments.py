"""Owner review payment bridge. Website keeps no provider or canonical DB secret."""
import json
import os
from pathlib import Path
import stat
from uuid import UUID

import aiohttp
from sqlalchemy import text

from bot.services import PILOT_TARIFFS
from bot.services.web_checkout import WebCheckout, OrderUnavailable
from bot.services.payments import _safe_confirmation_url
from bot.services.yookassa_verification import identity_from_lookup
from bot.services.website_review import ReadOnlyReview
from bot.services.yookassa_api import PaymentProviderUnavailable

SOCKET = '/run/kudavpn-site-payments/bridge.sock'


class NoProfileIssuance:
    """Payment handling never issues/rotates VPN credentials; existing bot owns it."""
    async def issue_for_user(self, owner):
        raise PermissionError('Existing canonical exporter supplies access')


class CanonicalPaymentCheckout(WebCheckout):
    def __init__(self, sessions, checkout, processor, owner):
        super().__init__(sessions, checkout, processor, NoProfileIssuance(), owner)

    async def create(self, user_id, tariff_id):
        self._owner(user_id)
        if tariff_id not in PILOT_TARIFFS:
            raise ValueError('Unknown plan')
        row = await self.checkout.repository.create_or_reuse_pending(user_id, PILOT_TARIFFS[tariff_id])
        if row.provider_payment_id:
            # A pending order may originate in the previous checkout, whose
            # return_url differs. Reuse its authenticated GET; never POST the
            # same key with changed payload or create a second provider object.
            return await self.refresh(user_id, str(row.id))
        await self.checkout._start(row)
        return await self.state(user_id, str(row.id))

    async def ready(self):
        async with self.sessions() as session:
            await session.execute(text("SELECT 1"))
        return {"ready": True, "test": True}

    async def state(self, user_id, order_id):
        result = await super().state(user_id, order_id)
        row = await self.order(user_id, order_id)
        if result['state'] == 'pending' and row.provider_payment_id:
            # Restore an unfinished checkout after browser/server restart using
            # GET only, without creating another payment or trusting return URL.
            try:
                data = await self.processor.lookup.fetch(row.provider_payment_id)
                identity = identity_from_lookup(row.id, data)
                if (identity.provider_id != row.provider_payment_id
                        or identity.amount_kopecks != row.amount_kopecks):
                    raise ValueError('Provider order mismatch')
                confirmation = data.get('confirmation') or {}
                url = confirmation.get('confirmation_url')
                if (data.get('status') == 'pending' and confirmation.get('type') == 'redirect'
                        and _safe_confirmation_url(url, frozenset({'yookassa.ru','yoomoney.ru'}))):
                    result['payment_url'] = url
            except PaymentProviderUnavailable:
                pass  # Local pending state remains true; polling retries later.
        return result


class LocalPaymentClient:
    def __init__(self, path=SOCKET, *, validate_socket=True):
        self.path, self.validate_socket = Path(path), validate_socket

    def check(self):
        info, parent = self.path.lstat(), self.path.parent.lstat()
        if (str(self.path) != SOCKET or not stat.S_ISSOCK(info.st_mode)
                or not stat.S_ISDIR(parent.st_mode) or info.st_uid != parent.st_uid
                or info.st_mode & 0o007 or parent.st_mode & 0o027
                or info.st_uid == os.geteuid() or info.st_gid != os.getegid()):
            raise PaymentProviderUnavailable('Local payment service unavailable')

    async def request(self, action, **arguments):
        try:
            if self.validate_socket:
                self.check()
            connector = aiohttp.UnixConnector(path=str(self.path), limit=2)
            async with aiohttp.ClientSession(connector=connector, trust_env=False,
                    timeout=aiohttp.ClientTimeout(total=20, connect=3)) as session:
                async with session.post('http://localhost/rpc', json={'action': action, **arguments},
                                        allow_redirects=False) as response:
                    body = await response.content.read(16385)
                    if len(body)>16384:
                        raise ValueError()
                    if response.status == 404:
                        raise OrderUnavailable()
                    if response.status != 200:
                        raise PaymentProviderUnavailable('Local payment service unavailable')
                    result = json.loads(body)
                    if not isinstance(result, dict):
                        raise ValueError()
                    return result
        except OrderUnavailable:
            raise
        except Exception:
            raise PaymentProviderUnavailable('Local payment service unavailable') from None


class PaymentReview(ReadOnlyReview):
    read_only = False

    def __init__(self, snapshot, origin, payments):
        super().__init__(snapshot, origin)
        self.payments = payments

    async def account(self, owner):
        result = await super().account(owner)
        result["read_only"] = False
        return result

    async def _call(self, owner, action, **arguments):
        # Fresh canonical snapshot and authenticated owner required before any RPC.
        self.snapshot.read(owner)
        result = await self.payments.request(action, **arguments)
        order = result.get('order') if action == 'latest' else result
        if order and order.get('state') == 'succeeded':
            current = self.snapshot.read(owner)['account']
            # Export may lag confirmation by 30s; do not advertise stale access.
            if (current.get('feed_url') and current.get('expires_at') == order.get('subscription_expires_at')):
                order['connect_url'] = self.origin+'/connect'
                order['access_pending'] = False
        return result

    async def latest(self, owner):
        return (await self._call(owner, 'latest'))['order']

    async def create(self, owner, tariff):
        if tariff not in {p['id'] for p in self.plans()}:
            raise ValueError('Unknown tariff')
        return await self._call(owner, 'create', tariff_id=tariff)

    async def state(self, owner, order):
        UUID(order)
        return await self._call(owner, 'state', order_id=order)

    async def refresh(self, owner, order):
        UUID(order)
        return await self._call(owner, 'refresh', order_id=order)
