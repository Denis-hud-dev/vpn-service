"""The opt-in Telegram contour must not inherit a production DB or bot token."""
import base64
from pathlib import Path

import pytest
import yaml

from bot.pilot_bootstrap import PILOT_MARKER, prepare_environment


def source(tmp_path):
    password = tmp_path / 'db-password'
    password.write_text('pilot-only-32-character-password-1234\n')
    token = tmp_path / 'bot-token'
    token.write_text('123456:' + 'a' * 32 + '\n')
    return {
        'KUDAVPN_PILOT_MARKER': PILOT_MARKER,
        'PILOT_DB_PASSWORD_FILE': str(password),
        'PILOT_BOT_TOKEN_FILE': str(token),
        'PILOT_EXPECTED_BOT_ID': '123456',
    }


def test_pilot_database_identity_is_fixed_and_credentials_not_printed(tmp_path):
    env = source(tmp_path)
    result = prepare_environment('migrate', env)
    assert result['DATABASE_URL'].startswith('postgresql+psycopg://kudavpn_pilot:')
    assert result['DATABASE_URL'].endswith('@postgres:5432/kudavpn_telegram_pilot')
    assert 'BOT_TOKEN' not in result
    for key in ('DATABASE_URL', 'DATABASE_URL_DOCKER'):
        with pytest.raises(ValueError, match='pre-existing database'):
            prepare_environment('migrate', {**env, key: 'postgresql+psycopg://production'})
    with pytest.raises(ValueError, match='marker'):
        prepare_environment('bot', {**env, 'KUDAVPN_PILOT_MARKER': 'production'})


def test_pilot_bot_requires_distinct_expected_identity_and_https_link(tmp_path):
    env = source(tmp_path)
    result = prepare_environment('bot', env)
    assert result['BOT_TOKEN'].startswith('123456:')
    assert result['HEALTH_PORT'] == '18081'
    assert 'SUBSCRIPTION_PUBLIC_URL' not in result
    with pytest.raises(ValueError, match='expected test bot'):
        prepare_environment('bot', {**env, 'PILOT_EXPECTED_BOT_ID': '999999'})
    with pytest.raises(ValueError, match='trusted HTTPS'):
        prepare_environment('bot', {**env, 'PILOT_SUBSCRIPTION_PUBLIC_URL': 'http://127.0.0.1:18443'})
    with pytest.raises(ValueError, match='trusted HTTPS'):
        prepare_environment('bot', {**env, 'PILOT_SUBSCRIPTION_PUBLIC_URL': 'https://127.0.0.1'})
    with pytest.raises(ValueError, match='secret file'):
        prepare_environment('bot', {**env, 'PILOT_SUBSCRIPTION_PUBLIC_URL': 'https://pilot.example.org'})

    key = tmp_path / 'happ-key'
    key.write_text(base64.urlsafe_b64encode(bytes(range(32))).rstrip(b'=').decode())
    result = prepare_environment('bot', {**env,
        'PILOT_SUBSCRIPTION_PUBLIC_URL': 'https://pilot.example.org',
        'PILOT_HAPP_LINK_SECRET_FILE': str(key)})
    assert result['SUBSCRIPTION_PUBLIC_URL'] == 'https://pilot.example.org'
    assert result['HAPP_LINK_SECRET'] == key.read_text()
    ip_result = prepare_environment('bot', {**env,
        'PILOT_SUBSCRIPTION_PUBLIC_URL': 'https://1.1.1.1:18443',
        'PILOT_ALLOW_IP_CERT': '1', 'PILOT_HAPP_LINK_SECRET_FILE': str(key)})
    assert ip_result['SUBSCRIPTION_PUBLIC_URL'] == 'https://1.1.1.1:18443'


def test_pilot_compose_uses_own_volume_no_production_env_and_loopback_db():
    compose = yaml.safe_load(Path('deploy/telegram-pilot/compose.yml').read_text())
    assert compose['name'] == 'kudavpn-telegram-pilot'
    assert compose['services']['postgres']['ports'] == ['127.0.0.1:55433:5432']
    assert compose['services']['postgres']['volumes'] == ['pilot_pg_data:/var/lib/postgresql/data']
    assert compose['services']['postgres']['environment']['POSTGRES_DB'] == 'kudavpn_telegram_pilot'
    assert compose['services']['bot']['profiles'] == ['telegram']
    assert compose['services']['bot']['depends_on']['migrate']['condition'] == 'service_completed_successfully'
    assert not {'DATABASE_URL', 'DATABASE_URL_DOCKER', 'BOT_TOKEN'} & set(compose['services']['bot']['environment'])
    assert 'ports' not in compose['services']['bot']
    assert 'NODE_AGENT_CONFIG' not in str(compose)
