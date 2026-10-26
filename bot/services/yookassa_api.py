"""Fail-closed HTTP adapter for an isolated YooKassa *test* shop only."""
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID
import re

import aiohttp

from bot.services.payments import PaymentIntent
from bot.services.yookassa_verification import identity_from_lookup

API_ORIGIN = 'https://api.yookassa.ru'
PROVIDER_ID = re.compile(r'[A-Za-z0-9-]{1,128}\Z')


class PaymentProviderUnavailable(RuntimeError):
    """Generic failure: do not expose response bodies, URLs or credentials."""


class YooKassaTestClient:
    def __init__(self, shop_id: str, secret_key: str, *, transport=None,
                 payment_method: str = 'smart'):
        if not re.fullmatch(r'[0-9]{4,20}', shop_id) or not secret_key or '\n' in secret_key:
            raise ValueError('Неверные параметры тестового магазина')
        if payment_method not in {'smart', 'sbp'}:
            raise ValueError('Неподдерживаемый способ тестовой оплаты')
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
                        data = await response.json(content_type='application/json')
            except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
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
