"""Root-only read-only probes. Never emits logs, environment values or URLs."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from monitor.common import atomic_json, check

DOMAIN = 'r1276248.s2578.vm.mydomain.zone'
UNITS = ('xray.service', 'kudavpn-xray-alt8443.service', 'kudavpn-pilot-node.service',
         'kudavpn-pilot-two-node.service', 'kudavpn-telegram-pilot-feed.service',
         'kudavpn-telegram-pilot-bot.service', 'kudavpn-test-checkout.service',
         'postgresql@16-main.service', 'kudavpn-pilot-control.service')
CONTAINERS = ('kudavpn-premium-bot-1', 'kudavpn-premium-postgres-1', 'kudavpn-xray-xray-1')


def command(args, timeout=10, *, merge_error=False):
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT if merge_error else subprocess.DEVNULL,
                                timeout=timeout, text=True, check=False)
        return result.returncode, result.stdout[:1048576]
    except (OSError, subprocess.TimeoutExpired):
        return None, ''


def cpu_percent(before, after):
    if len(before) < 8 or len(after) < 8:
        return None
    total = sum(after[:8])-sum(before[:8])
    idle = sum(after[3:5])-sum(before[3:5])
    if total <= 0 or idle < 0 or idle > total:
        return None
    return round(100*(1-idle/total), 1)


def resources():
    rows = []
    try:
        before = [int(v) for v in Path('/proc/stat').read_text().splitlines()[0].split()[1:]]
        time.sleep(0.3)
        after = [int(v) for v in Path('/proc/stat').read_text().splitlines()[0].split()[1:]]
        cpu = cpu_percent(before, after)
        rows.append(check('cpu', 'unknown' if cpu is None else 'error' if cpu >= 95 else 'ok',
                          'not_available' if cpu is None else 'high' if cpu >= 95 else 'measured',
                          cpu, critical=cpu is not None and cpu >= 95))
    except (OSError, ValueError, IndexError):
        rows.append(check('cpu', 'unknown', 'not_available'))
    try:
        values = {line.partition(':')[0]: int(line.partition(':')[2].split()[0])
                  for line in Path('/proc/meminfo').read_text().splitlines()}
        used = round(100*(1-values['MemAvailable']/values['MemTotal']), 1)
        rows.append(check('memory', 'error' if used >= 90 else 'ok',
                          'high' if used >= 90 else 'measured', used, critical=used >= 90))
    except (OSError, KeyError, ValueError, ZeroDivisionError):
        rows.append(check('memory', 'unknown', 'not_available'))
    try:
        disk = shutil.disk_usage('/')
        used = round(disk.used/disk.total*100, 1)
        rows.append(check('disk', 'error' if used >= 90 or disk.free < 1024**3 else 'ok',
                          'high' if used >= 90 or disk.free < 1024**3 else 'measured', used,
                          critical=used >= 90 or disk.free < 1024**3))
    except (OSError, ZeroDivisionError):
        rows.append(check('disk', 'unknown', 'not_available'))
    return rows


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def http(url):
    # No environment proxy or redirects; neither tokens nor error strings leave
    # this function. URLs with a personal feed credential never enter logs.
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        with opener.open(url, timeout=5) as response:
            return response.status, response.read(262145)
    except urllib.error.HTTPError as error:
        return error.code, b''
    except Exception:
        return None, b''


def ready(key, url):
    status, body = http(url)
    try:
        valid = status == 200 and json.loads(body).get('ready') is True
    except (ValueError, AttributeError):
        valid = False
    return check(key, 'ok' if valid else 'error', 'reachable' if valid else 'invalid_response', critical=True)


def tcp(port, host='127.0.0.1'):
    try:
        with socket.create_connection((host, port), timeout=3):
            return check(f'tcp:{port}', 'ok', 'reachable', critical=True)
    except OSError:
        return check(f'tcp:{port}', 'error', 'unreachable', critical=True)


def journal_errors(units):
    args = ['journalctl', '--since=-4hours', '--priority=err', '--lines=2000', '--quiet', '--output=json']
    for unit in units:
        args.extend(['--unit', unit])
    code, data = command(args)
    if code != 0:
        return check('service_errors', 'unknown', 'not_available')
    count = len(data.splitlines())
    return check('service_errors', 'error' if count else 'ok', 'journal_markers', count)


def collect(host, probe_file=None):
    if host not in ('justhost', 'digitalocean'):
        raise ValueError('Unknown monitor host')
    rows = [check('collector', 'ok', 'measured')] + resources()
    if host == 'justhost':
        for unit in UNITS:
            code, output = command(['systemctl', 'is-active', unit])
            legacy = unit == 'kudavpn-pilot-control.service'
            status = 'unknown' if code is None else 'ok' if output.strip() == 'active' else 'error'
            rows.append(check('unit:'+unit, status,
                'not_available' if code is None else 'active' if status == 'ok' else 'legacy_failed' if legacy else 'inactive',
                critical=not legacy))
        rows.extend(tcp(port) for port in (443, 8443, 9443, 10443, 18443, 18543))
        rows.append(ready('test_bot_ready', 'http://127.0.0.1:18081/ready'))
        rows.append(ready('checkout_ready', f'https://{DOMAIN}:18543/ready'))
        code, output = command(['runuser', '-u', 'postgres', '--', 'psql', '-XAtq',
                               '-d', 'kudavpn_telegram_pilot', '-c', 'BEGIN READ ONLY; SELECT 1; COMMIT;'])
        rows.append(check('postgres', 'ok' if code == 0 and output.strip() == '1' else 'unknown' if code is None else 'error',
                          'reachable' if code == 0 and output.strip() == '1' else 'not_available', critical=True))
        code, output = command(['runuser', '-u', 'postgres', '--', 'psql', '-XAtq',
            '-d', 'kudavpn_telegram_pilot', '-c',
            "BEGIN READ ONLY; SELECT CASE WHEN COUNT(*)=0 THEN 'unknown' "
            "WHEN bool_and(coalesce(h.available,false) AND h.checked_at>now()-interval '90 seconds') "
            "THEN 'ok' ELSE 'error' END FROM vpn_nodes v LEFT JOIN node_health h ON h.node_id=v.id "
            "WHERE v.enabled AND v.port=10443; COMMIT;"])
        status = output.strip() if code == 0 and output.strip() in ('ok', 'error') else 'unknown'
        rows.append(check('node_health', status, 'healthy' if status == 'ok' else 'not_available', critical=True))
    else:
        for container in CONTAINERS:
            code, output = command(['docker', 'inspect', '--format', '{{json .State}}', container])
            try:
                state = json.loads(output)
                healthy = state.get('Running') is True and state.get('Health', {}).get('Status') == 'healthy'
                rows.append(check('container:'+container, 'ok' if healthy else 'error',
                                  'healthy' if healthy else 'unhealthy', critical=True))
            except (ValueError, AttributeError):
                rows.append(check('container:'+container, 'unknown', 'not_available', critical=True))
        rows.append(tcp(443, '209.38.196.186'))
        rows.append(ready('bot_ready', 'http://127.0.0.1:18080/ready'))
        # Credentials stay inside the existing PostgreSQL container; SELECT only.
        code, output = command(['docker', 'exec', 'kudavpn-premium-postgres-1', 'sh', '-c',
            'PGPASSWORD="$POSTGRES_PASSWORD" PGOPTIONS="-c default_transaction_read_only=on" '
            'psql -h127.0.0.1 -p5433 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -XAtqc "SELECT 1"'])
        rows.append(check('postgres', 'ok' if code == 0 and output.strip() == '1' else 'unknown' if code is None else 'error',
                          'reachable' if code == 0 and output.strip() == '1' else 'not_available', critical=True))
        code, output = command(['docker', 'logs', '--since=4h', '--tail=1000', 'kudavpn-premium-bot-1'], merge_error=True)
        count = len(re.findall(r'(?im)\bERROR\b|Traceback|TelegramConflictError', output))
        rows.append(check('bot_errors', 'unknown' if code != 0 else 'error' if count else 'ok',
                          'not_available' if code != 0 else 'journal_markers', count if code == 0 else None))
        # No production bot token is copied to Monitor; readiness covers its DB.
        rows.append(check('bot_api', 'unknown', 'not_probed'))
    status, _ = http(f'https://{DOMAIN}:18443/assets/connect.js')
    rows.append(check('https_feed', 'ok' if status == 200 else 'error',
                      'http_status' if status is not None else 'unreachable', status, critical=True))
    try:
        with socket.create_connection((DOMAIN, 18443), timeout=3) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname=DOMAIN) as tls:
                days = int((ssl.cert_time_to_seconds(tls.getpeercert()['notAfter'])-time.time())/86400)
        rows.append(check('tls', 'error' if days < 14 else 'ok', 'high' if days < 14 else 'measured', days, critical=days < 3))
    except Exception:
        rows.append(check('tls', 'unknown', 'not_available', critical=True))
    if host == 'justhost' and probe_file:
        try:
            url = Path(probe_file).read_text().strip()
            parsed = urlsplit(url)
            if (parsed.scheme != 'https' or parsed.hostname != DOMAIN or parsed.port != 18443
                    or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or not re.fullmatch(r'/s/[A-Za-z0-9_-]{43}', parsed.path)):
                raise ValueError()
            status, body = http(url)
            entries = base64.b64decode(body.strip(), validate=True).decode().splitlines() if status == 200 else []
            valid = bool(entries) and all(urlsplit(line).scheme == 'vless' and urlsplit(line).port == 10443 for line in entries)
            rows.append(check('feed_content', 'ok' if valid else 'error',
                              'measured' if valid else 'http_status', len(entries) if valid else status))
        except Exception:
            rows.append(check('feed_content', 'unknown', 'not_available'))
    elif host == 'justhost':
        rows.append(check('feed_content', 'unknown', 'not_configured'))
    rows.append(check('vpn_data_path', 'unknown', 'not_probed'))
    rows.append(journal_errors(UNITS if host == 'justhost' else ['docker.service']))
    return dict(host=host, timestamp=int(time.time()), checks=rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True, choices=('justhost', 'digitalocean'))
    parser.add_argument('--output', default='/var/lib/kudavpn-monitor-collector/export.json')
    parser.add_argument('--heartbeat', default='/var/lib/kudavpn-monitor/heartbeat.json')
    parser.add_argument('--probe-file')
    args = parser.parse_args()
    snapshot = collect(args.host, args.probe_file)
    try:
        heartbeat = json.loads(Path(args.heartbeat).read_text())
        snapshot['heartbeat'] = {k: int(heartbeat.get(k, 0)) for k in ('tick', 'full')}
    except (OSError, ValueError, TypeError):
        snapshot['heartbeat'] = {}
    atomic_json(args.output, snapshot, 0o640)
    print('monitor_collect outcome=written')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('monitor_collect outcome=failed category='+type(error).__name__)
        raise SystemExit(1) from None
