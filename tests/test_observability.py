import asyncio
import logging
from unittest.mock import AsyncMock
from pathlib import Path
import pytest
import yaml
from aiogram.methods import SendMessage
from aiogram.exceptions import TelegramNetworkError
from bot.observability import TelegramTimingMiddleware, SafeDependencyLogs, instrument_database
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from tests.fake_telegram import TelegramHarness


def test_update_timings_do_not_log_payload_or_identity(caplog):
    async def run():
        app = TelegramHarness()
        try:
            await app.text('/start secret_argument')
            await app.callback('unknown_private_payload')
        finally:
            await app.close()
    with caplog.at_level(logging.INFO, logger='bot.observability'):
        asyncio.run(run())
    assert 'operation=start' in caplog.text and 'operation=callback' in caplog.text
    assert 'secret_argument' not in caplog.text and 'unknown_private_payload' not in caplog.text
    assert 'chat_id' not in caplog.text and 'user_id' not in caplog.text


def test_telegram_timing_preserves_errors_without_logging_details(caplog):
    async def run():
        method = SendMessage(chat_id=123, text='private_payload')
        request = AsyncMock(side_effect=TelegramNetworkError(method=method, message='private_token'))
        with pytest.raises(TelegramNetworkError):
            await TelegramTimingMiddleware()(request, object(), method)
    with caplog.at_level(logging.INFO, logger='bot.observability'):
        asyncio.run(run())
    assert 'operation=telegram.sendMessage' in caplog.text and 'outcome=interrupted' in caplog.text
    assert 'private_' not in caplog.text


def test_database_timing_excludes_query_parameters(caplog):
    async def run():
        engine = create_async_engine('sqlite+aiosqlite://')
        instrument_database(engine)
        try:
            async with engine.connect() as connection:
                assert await connection.scalar(text('SELECT :value'), {'value': 'private_database_value'}) == 'private_database_value'
        finally:
            await engine.dispose()
    with caplog.at_level(logging.INFO, logger='bot.observability'):
        asyncio.run(run())
    assert 'operation=database.query' in caplog.text
    assert 'private_database_value' not in caplog.text and 'SELECT' not in caplog.text


def test_dependency_log_filter_removes_payload_and_traceback():
    record = logging.LogRecord('aiogram.event', logging.ERROR, '', 0, 'private %s', ('token',), (ValueError, ValueError('secret'), None))
    assert SafeDependencyLogs().filter(record)
    assert 'private' not in record.getMessage() and 'token' not in record.getMessage()
    assert record.exc_info is None
    record.levelno = logging.INFO
    assert not SafeDependencyLogs().filter(record)


def test_production_compose_has_no_vpn_or_public_ports():
    services = yaml.safe_load(Path('docker-compose.production.yml').read_text())['services']
    assert set(services) == {'bot', 'migrate', 'postgres'}
    for service in services.values():
        assert service['network_mode'] == 'host'
        assert 'ports' not in service and 'privileged' not in service
        assert service['logging']['options']['max-file'] == '3'
    assert services['bot']['restart'] == 'unless-stopped'
    assert services['bot']['read_only']
    assert 'listen_addresses=127.0.0.1' in services['postgres']['command']
    assert services['postgres']['environment']['POSTGRES_INITDB_ARGS'] == '--auth-host=scram-sha-256'


def test_polling_conflict_is_reported_without_token_or_idle_timing(caplog):
    from aiogram.methods import GetUpdates
    from aiogram.exceptions import TelegramConflictError
    async def run():
        method = GetUpdates()
        middleware = TelegramTimingMiddleware()
        assert await middleware(AsyncMock(return_value=[]), object(), method) == []
        request = AsyncMock(side_effect=TelegramConflictError(method=method, message='private_token'))
        with pytest.raises(TelegramConflictError):
            await middleware(request, object(), method)
    with caplog.at_level(logging.INFO, logger='bot.observability'):
        asyncio.run(run())
    assert 'polling_failure kind=TelegramConflictError' in caplog.text
    assert 'private_token' not in caplog.text and 'duration_ms' not in caplog.text


def test_release_dependencies_are_hashed_and_target_linux_amd64():
    import re
    services = yaml.safe_load(Path('docker-compose.production.yml').read_text())['services']
    assert all(service['platform'] == 'linux/amd64' for service in services.values())
    assert '@sha256:' in services['postgres']['image']
    dockerfile = Path('bot/Dockerfile.production').read_text()
    assert '@sha256:' in dockerfile and '--require-hashes' in dockerfile
    packages = [line for line in Path('bot/requirements-linux.lock').read_text().splitlines() if line and not line.startswith('#')]
    assert packages and all(re.fullmatch(r'[A-Za-z0-9_.-]+==[0-9][A-Za-z0-9_.+-]* --hash=sha256:[0-9a-f]{64}', line) for line in packages)


def test_production_ports_are_distinct_and_health_is_loopback():
    services = yaml.safe_load(Path('docker-compose.production.yml').read_text())['services']
    assert 'port=5433' in services['postgres']['command']
    assert services['bot']['environment']['HEALTH_PORT'] == '18080'
    assert '127.0.0.1:18080/health' in services['bot']['healthcheck']['test'][-1]
    assert '"127.0.0.1", config.health_port' in Path('bot/main.py').read_text()
