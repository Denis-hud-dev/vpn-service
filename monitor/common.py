"""Small, sanitized snapshot schema and private durable monitor state."""
from __future__ import annotations

from contextlib import closing
import json
import math
import os
from pathlib import Path
from monitor.events import CATEGORIES
import sqlite3
import tempfile
import time

PERIOD = 4 * 3600
STALE = 180
HOST_NAMES = {'justhost': 'JustHost', 'digitalocean': 'DigitalOcean'}
LABELS = {
    'collector': 'Свежесть метрик', 'cpu': 'CPU', 'memory': 'RAM', 'disk': 'Диск /',
    'postgres': 'PostgreSQL · SELECT 1', 'bot_ready': 'Основной бот · readiness',
    'bot_api': 'Основной бот · Telegram API', 'test_bot_ready': 'Тестовый бот · readiness',
    'checkout_ready': 'Тестовый HTTPS-checkout', 'https_feed': 'HTTPS-сервис подписок',
    'tls': 'TLS подписок · дней до окончания', 'feed_content': 'Личная тестовая подписка · узлов',
    'service_errors': 'Ошибки systemd за 4 ч', 'bot_errors': 'События ошибок бота за 4 ч',
    'node_health': 'Агент 10443 · свежая проверка control plane',
    'vpn_data_path': 'VPN с устройства пользователя', 'observer': 'Monitor · heartbeat',
    'unit:xray.service': 'Xray · 443',
    'unit:kudavpn-xray-alt8443.service': 'Xray · 8443',
    'unit:kudavpn-pilot-node.service': 'Xray · 9443',
    'unit:kudavpn-pilot-two-node.service': 'Агент пилота · 10443',
    'unit:kudavpn-pilot-control.service': 'Старый контроллер 9443',
    'unit:kudavpn-telegram-pilot-feed.service': 'Сервис HTTPS-подписок',
    'unit:kudavpn-telegram-pilot-bot.service': 'Тестовый Telegram-бот',
    'unit:kudavpn-test-checkout.service': 'Тестовый checkout',
    'unit:postgresql@16-main.service': 'PostgreSQL · процесс',
    'container:kudavpn-premium-bot-1': 'Основной бот · контейнер',
    'container:kudavpn-premium-postgres-1': 'PostgreSQL · контейнер',
    'container:kudavpn-xray-xray-1': 'Xray · контейнер',
}
for port in (443, 8443, 9443, 10443, 18443, 18543):
    LABELS[f'tcp:{port}'] = f'TCP/{port} · соединение'

REASONS = {
    'measured': '', 'active': 'активен', 'healthy': 'healthy', 'reachable': 'доступен',
    'inactive': 'не активен', 'unhealthy': 'не healthy', 'unreachable': 'недоступен',
    'not_available': 'проверка недоступна', 'stale': 'метрики устарели/не получены',
    'high': 'порог превышен', 'http_status': 'HTTP', 'invalid_response': 'ответ не прошёл проверку',
    'not_probed': 'нужен тест с реального клиента', 'not_configured': 'нет приватного probe-профиля',
    'legacy_failed': 'failed; ранее остановлен, не перезапускался',
    'primary_unavailable': 'нет свежего heartbeat', 'journal_markers': 'маркеров',
    'event_history': 'история событий, не текущая авария',
    'decommissioned': '', 'unexpected_active': 'контроллер неожиданно активен',
    'controller_in_use': 'вывод из эксплуатации не подтверждён',
}


def check(key, status, code='measured', value=None, *, critical=False, stats=None):
    if key not in LABELS or status not in ('ok', 'warning', 'retired', 'error', 'unknown') or code not in REASONS:
        raise ValueError('Invalid metric schema')
    if status == 'retired' and (key != 'unit:kudavpn-pilot-control.service'
                               or code != 'decommissioned' or critical):
        raise ValueError('Only confirmed legacy controller may be retired')
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
        raise ValueError('Metric value must be a finite number')
    result = dict(id=key, status=status, code=code, value=value, critical=bool(critical))
    if stats is not None:
        if key != 'bot_errors' or set(stats) != {'complete', 'counts', 'last_event', 'window_start', 'window_end'}:
            raise ValueError('Invalid event statistics')
        start, end, last = (stats[k] for k in ('window_start', 'window_end', 'last_event'))
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end)) or end-start != PERIOD:
            raise ValueError('Invalid event window')
        if last is not None and (type(last) not in (int, float) or not math.isfinite(last) or not start <= last <= end):
            raise ValueError('Invalid last event')
        counts = stats['counts']
        if stats['complete'] is True:
            if (not isinstance(counts, dict) or set(counts) != set(CATEGORIES)
                    or any(type(v) is not int or not 0 <= v <= 5000 for v in counts.values())
                    or value != sum(counts.values()) or (value == 0) != (last is None)
                    or status != ('warning' if value else 'ok')):
                raise ValueError('Invalid event totals')
        elif stats['complete'] is not False or counts is not None or value is not None or status != 'unknown':
            raise ValueError('Unknown events must not have an exact count')
        if critical:
            raise ValueError('Historical events are not a current incident')
        result['stats'] = dict(stats, counts=dict(counts) if counts is not None else None)
    return result


def unavailable(host, now):
    return dict(host=host, timestamp=now, checks=[check('collector', 'unknown', 'stale', critical=True)],
                heartbeat={})


def validate_snapshot(data, host, now):
    try:
        stamp = data['timestamp']
        if (data['host'] != host or type(stamp) not in (int, float)
                or not math.isfinite(stamp) or not -30 <= now-stamp <= STALE):
            return unavailable(host, now)
        rows = data['checks']
        if not isinstance(rows, list) or not 1 <= len(rows) <= 60:
            return unavailable(host, now)
        cleaned = [check(r['id'], r['status'], r['code'], r.get('value'),
                         critical=r.get('critical') is True, stats=r.get('stats')) for r in rows]
        if any('stats' in r and not 0 <= stamp-r['stats']['window_end'] <= 120 for r in cleaned):
            return unavailable(host, now)
        if len({r['id'] for r in cleaned}) != len(cleaned):
            return unavailable(host, now)
        heartbeat = {k: int(data.get('heartbeat', {}).get(k, 0)) for k in ('tick', 'full')}
        return dict(host=host, timestamp=stamp, checks=cleaned, heartbeat=heartbeat)
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return unavailable(host, now)


def atomic_json(path, data, mode=0o600):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.monitor-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as file:
            os.fchmod(file.fileno(), mode)
            json.dump(data, file, ensure_ascii=True)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class State:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.parent.is_symlink() or self.path.parent.stat().st_mode & 0o077:
            raise ValueError('Monitor state directory must be0700')
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS receipts (event TEXT PRIMARY KEY, sent INTEGER NOT NULL, message_ids TEXT NOT NULL)')
            db.commit()

    def get(self, key, default=None):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT OR REPLACE INTO kv VALUES (?,?)', (key, json.dumps(value)))

    def delivered(self, event):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute('SELECT 1 FROM receipts WHERE event=?', (event,)).fetchone() is not None

    def record(self, event, ids, now):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT OR IGNORE INTO receipts VALUES (?,?,?)', (event, int(now), json.dumps(ids)))

    def last_receipt(self):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute('SELECT event,sent,message_ids FROM receipts ORDER BY sent DESC LIMIT 1').fetchone()


def read_snapshot(path, host, now=None):
    now = time.time() if now is None else now
    try:
        if Path(path).stat().st_size > 65536:
            return unavailable(host, now)
        return validate_snapshot(json.loads(Path(path).read_text()), host, now)
    except (OSError, ValueError):
        return unavailable(host, now)
