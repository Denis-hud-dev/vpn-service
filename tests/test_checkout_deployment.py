"""Deployment gates: no network, no production credentials, no VPN listeners."""
import asyncio
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.services.checkout_sessions import CheckoutSessions
from bot.subscription_api.checkout_main import settings, build
from bot.subscription_api.checkout_web import create_checkout_app


def configure(monkeypatch):
    monkeypatch.setenv('KUDAVPN_CHECKOUT_MODE', 'test-owner-only')
    monkeypatch.setenv('DATABASE_URL', 'postgresql+psycopg://kudavpn_pilot:fixture@127.0.0.1:5432/kudavpn_telegram_pilot')
    monkeypatch.setenv('CHECKOUT_PUBLIC_ORIGIN', 'https://checkout.example:18543')
    monkeypatch.delenv('CHECKOUT_BIND_HOST', raising=False)
    monkeypatch.delenv('CHECKOUT_PORT', raising=False)


def test_tls_checkout_is_loopback_unless_explicitly_published(monkeypatch):
    configure(monkeypatch)
    assert settings()[2:] == ('127.0.0.1', 18543)
    monkeypatch.setenv('CHECKOUT_BIND_HOST', '0.0.0.0')
    assert settings()[2:] == ('0.0.0.0', 18543)


@pytest.mark.parametrize('name,value', [
    ('KUDAVPN_CHECKOUT_MODE', 'production'),
    ('DATABASE_URL', 'postgresql+psycopg://kudavpn_pilot:fixture@remote.example/kudavpn_telegram_pilot'),
    ('DATABASE_URL', 'postgresql+psycopg://production:fixture@127.0.0.1/production'),
    ('CHECKOUT_PUBLIC_ORIGIN', 'http://checkout.example:18543'),
    ('CHECKOUT_PUBLIC_ORIGIN', 'https://checkout.example:18443'),
    ('CHECKOUT_BIND_HOST', 'unexpected.example'),
    ('CHECKOUT_PORT', '443'), ('CHECKOUT_PORT', '8443'),
    ('CHECKOUT_PORT', '9443'), ('CHECKOUT_PORT', '10443'), ('CHECKOUT_PORT', '18443'),
])
def test_unsafe_deployment_configuration_fails_closed(monkeypatch, name, value):
    configure(monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError): settings()


def test_live_secret_is_refused_before_any_api_request(monkeypatch, tmp_path):
    configure(monkeypatch)
    shop, secret = tmp_path / 'shop', tmp_path / 'secret'
    shop.write_text('1503208')
    secret.write_text('live-placeholder-not-a-key')
    shop.chmod(0o600)
    secret.chmod(0o600)
    monkeypatch.setenv('PILOT_YOOKASSA_TEST_SHOP_ID_FILE', str(shop))
    monkeypatch.setenv('PILOT_YOOKASSA_TEST_SECRET_FILE', str(secret))
    monkeypatch.setenv('PILOT_TRIAL_OWNER_ID', '501')
    monkeypatch.setenv('CHECKOUT_SESSION_FILE', str(tmp_path / 'sessions.sqlite'))
    monkeypatch.setattr('bot.subscription_api.checkout_main.Database', lambda url: None)
    with pytest.raises(ValueError, match='test API secret'): build()


def test_public_checkout_has_no_webhook_by_default(tmp_path):
    async def run():
        sessions = CheckoutSessions(str(tmp_path / 'sessions.sqlite'), 501)
        app = create_checkout_app(SimpleNamespace(), sessions, 'https://checkout.example:18543')
        async with TestClient(TestServer(app)) as http:
            response = await http.post('/yookassa/test-webhook', json={})
            assert response.status == 404
            assert response.headers['Cache-Control'] == 'no-store, private'
    asyncio.run(run())
