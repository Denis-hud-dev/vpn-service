"""Owner-only notifier, persistent four-hour reports and peer watchdog."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

from monitor.common import (HOST_NAMES, LABELS, PERIOD, REASONS, STALE, State,
                            atomic_json, check, read_snapshot, unavailable, validate_snapshot)


class DeliveryError(RuntimeError):
    def __init__(self, delay=300):
        self.delay = max(30, min(900, int(delay)))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Telegram:
    def __init__(self, token, owner):
        self.token, self.owner = token, owner

    def call(self, method, data):
        if method not in ('getMe', 'getChat', 'getUpdates', 'sendMessage'):
            raise ValueError('Unsupported monitor operation')
        try:
            request = urllib.request.Request('https://api.telegram.org/bot'+self.token+'/'+method,
                data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                urllib.request.HTTPSHandler(context=ssl.create_default_context()))
            with opener.open(request, timeout=10) as response:
                result = json.loads(response.read(1048576))
            if result.get('ok') is not True:
                raise DeliveryError(result.get('parameters', {}).get('retry_after', 300))
            return result['result']
        except urllib.error.HTTPError as error:
            delay = 900 if error.code in (401, 403) else 300
            if error.code == 429:
                try:
                    delay = json.loads(error.read(4096)).get('parameters', {}).get('retry_after', 300)
                except Exception:
                    pass
            raise DeliveryError(delay) from None
        except DeliveryError:
            raise
        except Exception:
            # Bot API has no sendMessage idempotency key. An ambiguous timeout
            # is backed off; no immediate duplicate or raw URL is logged.
            raise DeliveryError(300) from None

    def validate(self, identity):
        me = self.call('getMe', {})
        name = (me.get('username', '')+' '+me.get('first_name', '')).casefold()
        if me.get('id') != identity['bot_id'] or 'kuda' not in name or 'monitor' not in name:
            raise ValueError('Wrong monitor identity')
        chat = self.call('getChat', {'chat_id': self.owner})
        if chat.get('id') != self.owner or chat.get('type') != 'private':
            raise ValueError('Owner private chat is not available')

    def send(self, text):
        result = self.call('sendMessage', {'chat_id': self.owner, 'text': text,
            'protect_content': True, 'link_preview_options': {'is_disabled': True}})
        if result.get('chat', {}).get('id') != self.owner or type(result.get('message_id')) is not int:
            raise DeliveryError()
        return result['message_id']


def report(samples, now, title='◆ KudaVPN Monitor · полный отчёт'):
    stamp = datetime.fromtimestamp(now, ZoneInfo('America/Los_Angeles')).strftime('%d.%m.%Y %H:%M %Z')
    lines = [title, stamp]
    markers = {'ok': '🟢 Работает', 'warning': '🟡 Предупреждение',
               'error': '🔴 Ошибка', 'unknown': '🟡 Нет данных',
               'retired': '⚪ Выведен из эксплуатации'}
    for sample in samples:
        lines.extend(['', '┃ '+HOST_NAMES[sample['host']]])
        for row in sample['checks']:
            detail = REASONS[row['code']]
            value = row.get('value')
            if row['id'] == 'bot_errors':
                stats = row.get('stats', {})
                if stats.get('complete') is True:
                    counts = stats['counts']
                    detail = (f"{int(value)} событий · Telegram API: {counts['telegram_api']}"
                              f" · сеть: {counts['network']} · приложение: {counts['application']}"
                              f" · без классификации: {counts['unclassified']}")
                else:
                    detail = 'Количество не определено'
                if stats.get('last_event') is not None:
                    detail += (' · последний сбой: ' if stats.get('complete') else
                               ' · последний распознанный сбой: ')+datetime.fromtimestamp(
                        stats['last_event'], ZoneInfo('UTC')).strftime('%d.%m %H:%M:%S UTC')
                detail += ' · история за 4 ч; текущая доступность проверяется отдельно'
                value = None
            if value is not None:
                value = str(value)+('%' if row['id'] in ('cpu', 'memory', 'disk') else '')
                detail = (detail+' '+value).strip()
            lines.append('├ '+LABELS[row['id']]+': '+markers[row['status']]+(' · '+detail if detail else ''))
    next_at = (int(now)//PERIOD+1)*PERIOD
    lines.extend(['', 'Следующий полный отчёт: '+datetime.fromtimestamp(next_at,
        ZoneInfo('America/Los_Angeles')).strftime('%d.%m %H:%M %Z'),
        'Проверки ≈ каждую минуту. TCP/API не доказывают работу VPN с телефона.'])
    return '\n'.join(lines)


def split_text(text, limit=3500):
    chunks, current = [], ''
    for line in text.splitlines():
        if len(current)+len(line)+1 > limit:
            chunks.append(current)
            current = ''
        current += line[:limit]+'\n'
    if current:
        chunks.append(current)
    return chunks


class Controller:
    def __init__(self, state, sender, owner, host, role, *, initial_offset=0):
        self.state, self.sender, self.owner = state, sender, owner
        self.host, self.role = host, role
        self.started_at = None
        if self.state.get('offset') is None:
            self.state.put('offset', initial_offset)

    def deliver(self, event, text, now):
        if self.state.delivered(event):
            return True
        if now < self.state.get('retry_after', 0):
            return False
        ids = self.state.get('parts:'+event, [])
        try:
            for chunk in split_text(text)[len(ids):]:
                ids.append(self.sender.send(chunk))
                # A confirmed chunk is not resent after a restart/failure of
                # the next chunk. No exactly-once promise for lost responses.
                self.state.put('parts:'+event, ids)
            self.state.record(event, ids, now)
            self.state.put('retry_after', 0)
            print('monitor_send outcome=accepted category='+event.partition(':')[0], flush=True)
            return True
        except DeliveryError as error:
            self.state.put('retry_after', now+error.delay)
            print('monitor_send outcome=retry', flush=True)
            return False

    def incidents(self, samples):
        records = self.state.get('incidents', {})
        changes = []
        for sample in samples:
            for row in sample['checks']:
                key = sample['host']+'/'+row['id']
                entry = records.setdefault(key, {'bad': 0, 'good': 0, 'alerted': False, 'generation': 0})
                if row['status'] == 'retired':
                    # Retirement is not a recovery. Clear old incident counters
                    # without emitting a misleading "service restored" message.
                    entry.update(bad=0, good=0, alerted=False,
                                 generation=entry['generation']+int(entry['alerted']))
                    continue
                bad = row['critical'] and row['status'] in ('error', 'unknown')
                if bad:
                    entry['bad'] += 1
                    entry['good'] = 0
                    if entry['bad'] >= 2 and not entry['alerted']:
                        changes.append((key, 'failure', entry['generation']))
                else:
                    entry['bad'] = 0
                    entry['good'] += 1
                    if entry['good'] >= 2 and entry['alerted']:
                        changes.append((key, 'recovery', entry['generation']))
        self.state.put('incidents', records)
        return changes

    def acknowledge(self, changes):
        records = self.state.get('incidents', {})
        for key, kind, generation in changes:
            records[key]['alerted'] = kind == 'failure'
            if kind == 'recovery':
                records[key]['generation'] = generation+1
        self.state.put('incidents', records)

    def tick(self, samples, now, updates=()):
        if self.started_at is None:
            self.started_at = now
        if self.state.get('started_at') is None:
            self.state.put('started_at', now)
        peer = next(s for s in samples if s['host'] != self.host)
        slot, slot_start = int(now)//PERIOD, int(now)//PERIOD*PERIOD
        peer_full = peer.get('heartbeat', {}).get('full', 0)
        if self.role == 'primary':
            visible = []
            for sample in samples:
                tick = sample.get('heartbeat', {}).get('tick', 0)
                alive = sample['host'] == self.host or 0 <= now-tick <= STALE
                visible.append(dict(sample, checks=sample['checks']+[check('observer',
                    'ok' if alive else 'unknown', 'active' if alive else 'primary_unavailable', critical=True)]))
            samples = visible
            changes = self.incidents(samples)
            due = slot > self.state.get('full_slot', -1)
            # If the watchdog delivered this slot during a primary outage,
            # recovery does not produce a duplicate full report.
            if due and peer_full >= slot_start:
                self.state.put('full_slot', slot)
                due = False
        else:
            peer_tick = peer.get('heartbeat', {}).get('tick', 0)
            alive = 0 <= now-peer_tick <= STALE
            observer = dict(host=peer['host'], checks=[check('observer', 'ok' if alive else 'unknown',
                'active' if alive else 'primary_unavailable', critical=True)])
            samples = [dict(s, checks=s['checks']+observer['checks']) if s['host'] == peer['host'] else s for s in samples]
            changes = self.incidents([observer])
            due = (slot > self.state.get('full_slot', -1) and now >= slot_start+600
                   and now-self.started_at >= STALE
                   and peer_full < slot_start)
            if peer_full >= slot_start:
                self.state.put('full_slot', slot)
        sent_full = False
        if due:
            title = '◆ KudaVPN Monitor · полный отчёт' if self.role == 'primary' else '◆ KudaVPN Monitor · резервный полный отчёт'
            if self.deliver('full:'+str(slot), report(samples, now, title), now):
                self.state.put('full_slot', slot)
                self.state.put('last_full_at', int(now))
                self.acknowledge(changes)
                sent_full = True
        if changes and not sent_full:
            event = 'incident:'+json.dumps(changes, sort_keys=True)
            title = '🚨 KudaVPN Monitor · авария / восстановление'
            if self.deliver(event, report(samples, now, title), now):
                self.acknowledge(changes)
        if self.role == 'primary':
            for update in updates:
                number = update.get('update_id')
                if type(number) is not int or number < self.state.get('offset', 0):
                    continue
                message = update.get('message', {})
                allowed = (message.get('chat', {}).get('type') == 'private'
                    and message['chat'].get('id') == self.owner
                    and message.get('from', {}).get('id') == self.owner)
                command = message.get('text', '').split(' ', 1)[0].split('@', 1)[0].lower()
                # There are no mutation/exec commands, even for the owner.
                if allowed and command in ('/start', '/status', '/report', '/help'):
                    if now-self.state.get('last_command', 0) >= 30:
                        if not self.deliver('command:'+str(number), report(samples, now), now):
                            break
                        self.state.put('last_command', now)
                self.state.put('offset', number+1)
        self.state.put('last_tick', int(now))


def remote_snapshot(config, now):
    host, peer = config['peer_host'], config['peer_ip']
    try:
        args = ['ssh', '-F', '/dev/null', '-T', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=5', '-o', 'ConnectionAttempts=1',
            '-o', 'UserKnownHostsFile=/etc/kudavpn-monitor/known_hosts',
            '-i', '/var/lib/kudavpn-monitor/peer-key', 'root@'+peer, 'metrics']
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=8, check=False)
        if result.returncode or len(result.stdout) > 65536:
            return unavailable(host, now)
        return validate_snapshot(json.loads(result.stdout), host, now)
    except Exception:
        return unavailable(host, now)


def notify_systemd(message):
    address = os.getenv('NOTIFY_SOCKET')
    if not address:
        return
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(message.encode(), '\0'+address[1:] if address.startswith('@') else address)
    except OSError:
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='/etc/kudavpn-monitor/config.json')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    peers = {'justhost': ('digitalocean', '209.38.196.186', 'primary'),
             'digitalocean': ('justhost', '194.58.39.247', 'watchdog')}
    if (config.get('host') not in peers or
            (config.get('peer_host'), config.get('peer_ip'), config.get('role')) != peers[config['host']]):
        raise ValueError('Explicit monitor host configuration required')
    folder = Path('/var/lib/kudavpn-monitor')
    for name in ('bot-token', 'identity.json', 'peer-key'):
        if (folder/name).stat().st_mode & 0o077:
            raise ValueError('Private monitor credential required')
    identity = json.loads((folder/'identity.json').read_text())
    owner = identity['owner_id']
    if type(owner) is not int or owner <= 0 or not (identity.get('owner_start_verified') or identity.get('owner_contact_verified')):
        raise ValueError('Verified owner required')
    sender = Telegram((folder/'bot-token').read_text().strip(), owner)
    sender.validate(identity)
    state = State(folder/'state.sqlite')
    controller = Controller(state, sender, owner, config['host'], config['role'],
                            initial_offset=identity.get('initial_update_offset', 0))
    notify_systemd('READY=1')
    while True:
        started = time.monotonic()
        now = time.time()
        samples = [read_snapshot('/var/lib/kudavpn-monitor-collector/export.json', config['host'], now),
                   remote_snapshot(config, now)]
        updates = []
        if config['role'] == 'primary':
            try:
                updates = sender.call('getUpdates', {'offset': state.get('offset', 0), 'timeout': 0,
                                                     'limit': 20, 'allowed_updates': ['message']})
            except DeliveryError:
                print('monitor_poll outcome=retry', flush=True)
        controller.tick(samples, now, updates)
        atomic_json(folder/'heartbeat.json', dict(tick=int(time.time()), full=state.get('last_full_at', 0)))
        notify_systemd('WATCHDOG=1')
        if args.once:
            break
        time.sleep(max(1, 60-(time.monotonic()-started)))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('monitor_worker outcome=failed category='+type(error).__name__, flush=True)
        raise SystemExit(1) from None
