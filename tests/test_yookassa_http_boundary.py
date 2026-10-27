"""Exercise the real HTTP adapter branch with an offline session, no API calls."""
import asyncio
import json

import pytest

from bot.services import yookassa_api as api


class Response:
    status = 200
    content_type = 'application/json'

    def __init__(self, chunks):
        self.chunks = chunks
        self.content = self
        self.consumed = 0

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            self.consumed += len(chunk)
            yield chunk

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def http_fixture(monkeypatch, response):
    calls = []

    class Session:
        def __init__(self, **kwargs):
            assert kwargs['trust_env'] is False
            assert kwargs['timeout'].total == 12

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def request(self, method, url, **kwargs):
            assert kwargs['allow_redirects'] is False
            calls.append((method, url))
            return response

    monkeypatch.setattr(api.aiohttp, 'ClientSession', Session)
    return calls


def test_sandbox_sbp_rejected_before_http(monkeypatch, tmp_path):
    calls = http_fixture(monkeypatch, Response([]))
    with pytest.raises(ValueError, match='СБП недоступна'):
        api.YooKassaTestClient('123456', 'offline-placeholder', payment_method='sbp')
    shop, secret = tmp_path / 'shop', tmp_path / 'secret'
    shop.write_text('123456')
    secret.write_text('offline-placeholder')
    shop.chmod(0o600)
    secret.chmod(0o600)
    with pytest.raises(ValueError, match='СБП недоступна'):
        api.YooKassaTestClient.from_private_files(str(shop), str(secret), payment_method='sbp')
    assert calls == []


def test_http_json_is_read_to_eof_across_chunks(monkeypatch):
    body = json.dumps({'id': 'test-payment', 'test': True, 'status': 'pending'}).encode()
    response = Response([body[:5], body[5:24], body[24:]])
    calls = http_fixture(monkeypatch, response)
    client = api.YooKassaTestClient('123456', 'offline-placeholder')
    assert asyncio.run(client.fetch('test-payment'))['status'] == 'pending'
    assert response.consumed == len(body)
    assert calls == [('GET', 'https://api.yookassa.ru/v3/payments/test-payment')]


def test_oversized_chunked_response_stops_without_reading_remainder(monkeypatch):
    response = Response([b' ' * 8192] * (api.MAX_RESPONSE_BYTES // 8192 + 5))
    http_fixture(monkeypatch, response)
    client = api.YooKassaTestClient('123456', 'offline-placeholder')
    with pytest.raises(api.PaymentProviderUnavailable, match='временно недоступен'):
        asyncio.run(client.fetch('test-payment'))
    assert response.consumed == api.MAX_RESPONSE_BYTES + 8192


@pytest.mark.parametrize('body,content_type,status', [
    (b'{"test":true}', 'text/html', 200),
    (b'{"test":true}', 'application/json', 302),
    (b'invalid provider payload', 'application/json', 200),
    (b'\xff', 'application/json', 200),
    (b'[' * 2000 + b']' * 2000, 'application/json', 200),
    (b'{"id":"test-payment","test":false}', 'application/json', 200),
])
def test_http_failure_is_sanitized_and_never_accepted(monkeypatch, caplog, body, content_type, status):
    response = Response([body])
    response.content_type, response.status = content_type, status
    http_fixture(monkeypatch, response)
    client = api.YooKassaTestClient('123456', 'offline-placeholder')
    with pytest.raises(api.PaymentProviderUnavailable) as error:
        asyncio.run(client.fetch('test-payment'))
    assert str(error.value) in {
        'Тестовый провайдер временно недоступен', 'Ответ не относится к тестовому магазину'}
    assert caplog.text == ''
