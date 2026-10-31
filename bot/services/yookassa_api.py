"""Fail-closed HTTP adapter for an isolated YooKassa *test* shop only."""
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID
from urllib.parse import urlencode
import json
import re

import aiohttp

from bot.services.payments import PaymentIntent
from bot.services.yookassa_verification import identity_from_lookup

API_ORIGIN = 'https://api.yookassa.ru'
PROVIDER_ID = re.compile(r'[A-Za-z0-9-]{1,128}\Z')
MAX_RESPONSE_BYTES = 256 * 1024


class PaymentProviderUnavailable(RuntimeError):
    """Generic failure: do not expose response bodies, URLs or credentials."""


class YooKassaTestClient:
    def __init__(self, shop_id: str, secret_key: str, *, transport=None,
                 payment_method: str = 'smart'):
        if not re.fullmatch(r'[0-9]{4,20}', shop_id) or not secret_key or '\n' in secret_key:
            raise ValueError('Неверные параметры тестового магазина')
        if payment_method not in {'smart', 'sbp'}:
            raise ValueError('Неподдерживаемый способ тестовой оплаты')
        # YooKassa sandbox supports cards/YooMoney, not SBP. Keep the payload
        # contract available to injected offline fixtures, never real HTTP.
        if payment_method == 'sbp' and transport is None:
            raise ValueError('СБП недоступна в тестовом магазине ЮKassa')
        self.shop_id, self._secret_key = shop_id, secret_key
        self._transport = transport
        self.payment_method = payment_method

    @classmethod
    def from_private_files(cls, shop_id_file: str, secret_key_file: str, *,
                           payment_method: str = 'smart'):
        paths = [Path(shop_id_file), Path(secret_key_file)]
        if any(not path.is_file() or path.stat().st_mode & 0o077 for path in paths):
            raise ValueError('Файлы тестового магазина должны иметь права 0600')
        return cls(*(path.read_text().strip() for path in paths), payment_method=payment_method)

    async def _request(self, method: str, path: str, *, payload=None, key=None,
                       payment_object=True) -> dict[str, Any]:
        if self._transport is not None:
            data = await self._transport(method, path, payload, key)
        else:
            headers = {'Idempotence-Key': str(key)} if key else {}
            timeout = aiohttp.ClientTimeout(total=12, connect=5)
            try:
                async with aiohttp.ClientSession(
                    auth=aiohttp.BasicAuth(self.shop_id, self._secret_key),
                    timeout=timeout, trust_env=False, raise_for_status=False) as session:
                    async with session.request(method, API_ORIGIN + path, json=payload,
                                               headers=headers, allow_redirects=False) as response:
                        if response.status != 200:
                            raise PaymentProviderUnavailable('Тестовый провайдер временно недоступен')
                        if response.content_type != 'application/json':
                            raise PaymentProviderUnavailable('Тестовый провайдер временно недоступен')
                        body = bytearray()
                        async for chunk in response.content.iter_chunked(8192):
                            if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                                raise PaymentProviderUnavailable('Тестовый провайдер временно недоступен')
                            body.extend(chunk)
                        data = json.loads(body.decode('utf-8'))
            except (aiohttp.ClientError, TimeoutError, ValueError, RecursionError):
                raise PaymentProviderUnavailable('Тестовый провайдер временно недоступен') from None
        if not isinstance(data, dict) or (payment_object and data.get('test') is not True):
            raise PaymentProviderUnavailable('Ответ не относится к тестовому магазину')
        return data

    async def fetch_refund(self, refund_id: str) -> dict[str, Any]:
        if not isinstance(refund_id, str) or not PROVIDER_ID.fullmatch(refund_id):
            raise ValueError('Некорректный идентификатор возврата')
        # Refund objects do not carry the payment's test flag. Independently
        # fetch their parent through this authenticated test-only client.
        data = await self._request('GET', '/v3/refunds/' + refund_id, payment_object=False)
        if data.get('id') != refund_id:
            raise PaymentProviderUnavailable('Провайдер вернул другой возврат')
        await self.fetch(data.get('payment_id'))
        return data

    async def list_page(self, kind: str, start: datetime, end: datetime, cursor=None):
        """Authenticated, bounded discovery only; every item needs a fresh GET.

        Fixed UTC creation bounds must be reused on subsequent cursor pages.
        Refunds have no test flag: their parent is checked by fetch_refund.
        """
        if (kind not in {'payments', 'refunds'} or not isinstance(start, datetime)
                or not isinstance(end, datetime) or start.tzinfo is None
                or end.tzinfo is None or start > end
                or cursor is not None and (not isinstance(cursor, str) or not 1 <= len(cursor) <= 512
                    or any(ord(c) < 32 or ord(c) == 127 for c in cursor))):
            raise ValueError('Invalid reconciliation window')
        params = {'created_at.gte': start.astimezone(timezone.utc).isoformat(),
                  'created_at.lte': end.astimezone(timezone.utc).isoformat(), 'limit': 10}
        if cursor is not None:
            params['cursor'] = cursor
        data = await self._request('GET', '/v3/' + kind + '?' + urlencode(params), payment_object=False)
        items, next_cursor = data.get('items'), data.get('next_cursor')
        if (data.get('type') != 'list' or not isinstance(items, list) or len(items) > 10
                or next_cursor is not None and (not isinstance(next_cursor, str)
                    or not 1 <= len(next_cursor) <= 512
                    or any(ord(c) < 32 or ord(c) == 127 for c in next_cursor))):
            raise PaymentProviderUnavailable('Invalid reconciliation page')
        for item in items:
            if (not isinstance(item, dict) or not isinstance(item.get('id'), str)
                    or not PROVIDER_ID.fullmatch(item['id'])
                    or kind == 'payments' and item.get('test') is not True
                    or kind == 'refunds' and (not isinstance(item.get('payment_id'), str)
                        or not PROVIDER_ID.fullmatch(item['payment_id']))):
                raise PaymentProviderUnavailable('Invalid reconciliation page')
        return items, next_cursor

    async def create(self, *, amount_kopecks: int, currency: str,
                     idempotency_key: UUID, order_id: UUID, return_url: str) -> PaymentIntent:
        if (not isinstance(idempotency_key, UUID) or not isinstance(order_id, UUID)
                or type(amount_kopecks) is not int or amount_kopecks <= 0 or currency != 'RUB'):
            raise ValueError('Некорректный тестовый заказ')
        payload = {'amount': {'value': f'{Decimal(amount_kopecks) / 100:.2f}', 'currency': 'RUB'},
                   'capture': True, 'confirmation': {'type': 'redirect', 'return_url': return_url},
                   'description': f'KudaVPN test order {order_id}',
                   'metadata': {'order_id': str(order_id)}}
        if self.payment_method == 'sbp':
            payload['payment_method_data'] = {'type': 'sbp'}
        data = await self._request('POST', '/v3/payments', payload=payload, key=idempotency_key)
        identity = identity_from_lookup(order_id, data)
        if identity.amount_kopecks != amount_kopecks or data.get('status') != 'pending':
            raise PaymentProviderUnavailable('Провайдер не подтвердил тестовый заказ')
        confirmation = data.get('confirmation')
        if not isinstance(confirmation, dict) or confirmation.get('type') != 'redirect':
            raise PaymentProviderUnavailable('Провайдер не вернул страницу оплаты')
        url = confirmation.get('confirmation_url')
        if not isinstance(url, str):
            raise PaymentProviderUnavailable('Провайдер не вернул страницу оплаты')
        return PaymentIntent(identity.provider_id, url)

    async def fetch(self, provider_payment_id: str) -> dict[str, Any]:
        if not isinstance(provider_payment_id, str) or not PROVIDER_ID.fullmatch(provider_payment_id):
            raise ValueError('Некорректный идентификатор платежа')
        data = await self._request('GET', '/v3/payments/' + provider_payment_id)
        if data.get('id') != provider_payment_id:
            raise PaymentProviderUnavailable('Провайдер вернул другой платёж')
        return data
