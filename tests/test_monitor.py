"""No real Telegram traffic, credentials, shell commands or infrastructure changes."""
import json
import subprocess
from unittest.mock import Mock

import pytest

from monitor import collector
from monitor.common import PERIOD, State, check, read_snapshot, validate_snapshot
from monitor.worker import Controller, DeliveryError, Telegram, report, split_text


class Sender:
    def __init__(self):
        self.texts = []
        self.fail = False

    def send(self, text):
        if self.fail:
            raise DeliveryError(60)
        self.texts.append(text)
        return len(self.texts)


def sample(host, now, *, broken=False, full=0, tick=None):
    return dict(host=host, timestamp=now, checks=[check('cpu', 'error' if broken else 'ok',
        'high' if broken else 'measured', 99 if broken else 12, critical=broken),
        check('vpn_data_path', 'unknown', 'not_probed')],
        heartbeat={'tick': now if tick is None else tick, 'full': full})


def pair(now, **kwargs):
    return [sample('justhost', now, **kwargs), sample('digitalocean', now)]


def controller(tmp_path, role='primary'):
    state = State(tmp_path/'private'/'state.sqlite')
    sender = Sender()
    host = 'justhost' if role == 'primary' else 'digitalocean'
    return Controller(state, sender, 501, host, role), sender


def test_four_hour_full_reports_are_sent_even_when_unchanged_and_survive_restart(tmp_path):
    c, sender = controller(tmp_path)
    now = PERIOD*100+300
    c.tick(pair(now), now)
    assert len(sender.texts) == 1
    c.tick(pair(now+60), now+60)
    restarted = Controller(State(c.state.path), sender, 501, 'justhost', 'primary')
    restarted.tick(pair(now+120), now+120)
    assert len(sender.texts) == 1
    restarted.tick(pair(now+PERIOD), now+PERIOD)
    assert len(sender.texts) == 2
    assert c.state.last_receipt() and 'полный отчёт' in sender.texts[-1]


def test_critical_alert_requires_two_samples_is_deduplicated_and_recovers_once(tmp_path):
    c, sender = controller(tmp_path)
    now = PERIOD*100+300
    c.tick(pair(now), now)
    c.tick(pair(now+60, broken=True), now+60)
    assert len(sender.texts) == 1
    c.tick(pair(now+120, broken=True), now+120)
    assert len(sender.texts) == 2
    restarted = Controller(State(c.state.path), sender, 501, 'justhost', 'primary')
    restarted.tick(pair(now+180, broken=True), now+180)
    assert len(sender.texts) == 2
    restarted.tick(pair(now+240), now+240)
    restarted.tick(pair(now+300), now+300)
    assert len(sender.texts) == 3
    restarted.tick(pair(now+360), now+360)
    assert len(sender.texts) == 3
    restarted.tick(pair(now+420, broken=True), now+420)
    restarted.tick(pair(now+480, broken=True), now+480)
    assert len(sender.texts) == 4


def test_failed_delivery_is_not_marked_sent_and_backoff_prevents_flood(tmp_path):
    c, sender = controller(tmp_path)
    now = PERIOD*100+300
    sender.fail = True
    c.tick(pair(now), now)
    assert c.state.get('full_slot') is None and not c.state.last_receipt()
    sender.fail = False
    c.tick(pair(now+30), now+30)
    assert not sender.texts
    c.tick(pair(now+61), now+61)
    assert len(sender.texts) == 1 and c.state.last_receipt()


@pytest.mark.parametrize('chat_type,chat_id,sender_id', [('private', 777, 777), ('private', 501, 777), ('group', 501, 501)])
def test_unauthorized_users_and_groups_never_receive_a_response(tmp_path, chat_type, chat_id, sender_id):
    c, sender = controller(tmp_path)
    now = PERIOD*100+300
    c.tick(pair(now), now)
    update = {'update_id': 15, 'message': {'chat': {'id': chat_id, 'type': chat_type},
        'from': {'id': sender_id}, 'text': '/status'}}
    c.tick(pair(now+60), now+60, [update])
    assert len(sender.texts) == 1 and c.state.get('offset') == 16


def test_owner_commands_have_no_exec_operation_are_throttled_and_cursor_is_durable(tmp_path):
    c, sender = controller(tmp_path)
    now = PERIOD*100+300
    c.tick(pair(now), now)
    def update(number, text):
        return {'update_id': number, 'message': {'chat': {'id': 501, 'type': 'private'},
            'from': {'id': 501}, 'text': text}}
    c.tick(pair(now+60), now+60, [update(1, '/restart')])
    assert len(sender.texts) == 1
    c.tick(pair(now+120), now+120, [update(2, '/status'), update(3, '/report')])
    assert len(sender.texts) == 2
    restarted = Controller(State(c.state.path), sender, 501, 'justhost', 'primary')
    restarted.tick(pair(now+180), now+180, [update(2, '/status'), update(3, '/report')])
    assert len(sender.texts) == 2


@pytest.mark.parametrize('change', ['old', 'future', 'wrong_host', 'missing', 'nonfinite', 'injected_label'])
def test_invalid_or_stale_metrics_never_become_success(change):
    now = PERIOD*100+300
    data = sample('digitalocean', now)
    if change == 'old': data['timestamp'] = now-181
    if change == 'future': data['timestamp'] = now+31
    if change == 'wrong_host': data['host'] = 'justhost'
    if change == 'missing': data.pop('checks')
    if change == 'nonfinite': data['checks'][0]['value'] = float('nan')
    if change == 'injected_label': data['checks'][0]['id'] = 'private-token:do-not-render'
    result = validate_snapshot(data, 'digitalocean', now)
    assert result['checks'][0]['status'] == 'unknown'
    assert 'private-token' not in json.dumps(result)


def test_missing_snapshot_is_unknown_and_report_keeps_unknown_labels(tmp_path):
    now = PERIOD*100+300
    missing = read_snapshot(tmp_path/'missing', 'digitalocean', now)
    text = report([sample('justhost', now), missing], now)
    assert 'нет данных' in text and 'VPN с устройства пользователя' in text
    assert 'private-token' not in text and 'работает' in text


def test_watchdog_has_boot_grace_and_sends_fallback_when_primary_disappears(tmp_path):
    c, sender = controller(tmp_path, 'watchdog')
    now = PERIOD*100+900
    samples = [sample('digitalocean', now), sample('justhost', now, tick=now-300)]
    c.tick(samples, now)
    assert not sender.texts
    c.tick(samples, now+60)
    assert len(sender.texts) == 1 and 'нет свежего heartbeat' in sender.texts[0]
    c.tick(samples, now+180)
    assert len(sender.texts) == 2 and 'резервный полный отчёт' in sender.texts[-1]
    c.tick(samples, now+240)
    assert len(sender.texts) == 2


def test_watchdog_does_not_duplicate_primary_full_report(tmp_path):
    c, sender = controller(tmp_path, 'watchdog')
    now = PERIOD*100+900
    c.tick([sample('digitalocean', now), sample('justhost', now, full=now-60)], now)
    c.tick([sample('digitalocean', now+200), sample('justhost', now+200, full=now-60)], now+200)
    assert not sender.texts


def test_primary_recovery_does_not_repeat_watchdog_report_for_same_slot(tmp_path):
    c, sender = controller(tmp_path)
    now = PERIOD*100+900
    c.tick([sample('justhost', now), sample('digitalocean', now, full=now-60)], now)
    assert not sender.texts


def test_partial_message_delivery_is_not_repeated_after_restart(tmp_path):
    c, sender = controller(tmp_path)
    original = sender.send
    count = [0]
    def partly(text):
        count[0] += 1
        if count[0] == 2: raise DeliveryError(60)
        return original(text)
    sender.send = partly
    assert not c.deliver('test:split', 'a'*3000+'\n'+'b'*3000, 10000)
    assert len(sender.texts) == 1
    sender.send = original
    restarted = Controller(State(c.state.path), sender, 501, 'justhost', 'primary')
    assert restarted.deliver('test:split', 'a'*3000+'\n'+'b'*3000, 10061)
    assert len(sender.texts) == 2


def test_cpu_reset_is_unknown_and_normal_measurement_is_real():
    assert collector.cpu_percent([10]*8, [9]*8) is None
    assert collector.cpu_percent([0]*8, [10, 0, 10, 80, 0, 0, 0, 0]) == 20


def test_http_200_with_wrong_readiness_payload_is_error(monkeypatch):
    monkeypatch.setattr(collector, 'http', lambda url: (200, b'{"ready":false}'))
    assert collector.ready('bot_ready', 'unused')['status'] == 'error'
    monkeypatch.setattr(collector, 'http', lambda url: (200, b'not-json'))
    assert collector.ready('bot_ready', 'unused')['status'] == 'error'


def test_probe_failures_do_not_expose_credentials(monkeypatch):
    secret = 'offline-secret-never-render'
    def failure(*args, **kwargs): raise OSError(secret)
    monkeypatch.setattr(collector.subprocess, 'run', failure)
    assert collector.command(['unused']) == (None, '')
    assert secret not in json.dumps(collector.journal_errors(['unused']))


def test_telegram_wrong_recipient_is_not_delivery_and_never_exposes_token(monkeypatch):
    sender = Telegram('offline-secret', 501)
    monkeypatch.setattr(sender, 'call', lambda *a: {'chat': {'id': 777}, 'message_id': 1})
    with pytest.raises(DeliveryError): sender.send('status')


def test_split_messages_obey_telegram_size_limit():
    chunks = split_text(('one line\n'*1000))
    assert all(len(part) <= 3501 for part in chunks) and len(chunks) > 1


@pytest.mark.parametrize('encoded', [False, True])
def test_plain_and_base64_happ_feed_are_both_real_formats(encoded):
    import base64
    from uuid import UUID
    body=('vless://'+str(UUID(int=1))+'@203.0.113.1:10443?security=reality\n').encode()
    if encoded: body=base64.b64encode(body)
    assert collector.feed_entries(body)==1


@pytest.mark.parametrize('body',[b'not a subscription',b'',b'<html>failure</html>',b'vless://offline@203.0.113.1:8443'])
def test_invalid_feed_or_working_port_is_not_a_success(body):
    with pytest.raises(ValueError): collector.feed_entries(body)


def test_postgres_probe_uses_private_env_and_read_only_without_uid_change(monkeypatch, tmp_path):
    calls=[]
    password = tmp_path/'db-probe-password'
    password.write_text('offline-db-fixture')
    password.chmod(0o600)
    monkeypatch.setattr(collector, 'PG_PASSWORD_PATH', password)
    def runner(args,**kwargs):
        calls.append((args,kwargs))
        return 0,'1\n'
    monkeypatch.setattr(collector,'command',runner)
    assert collector.postgres_query('SELECT 1')==(0,'1\n')
    args,kwargs=calls[0]
    assert args[0]=='psql' and args[-1]=='BEGIN READ ONLY; SELECT 1; COMMIT;'
    assert 'offline-db-fixture' not in ' '.join(args)
    assert kwargs['env']['PGPASSWORD']=='offline-db-fixture'
    assert kwargs['env']['PGOPTIONS']=='-c default_transaction_read_only=on'
    password.chmod(0o644)
    assert collector.postgres_query('SELECT 1')==(None,'')


@pytest.mark.parametrize('code,output,expected',[(0,'ok\n','ok'),(0,'unknown\n','unknown'),(1,'','unknown'),(0,'unexpected-output','unknown')])
def test_main_bot_api_probe_keeps_token_inside_existing_container(monkeypatch,code,output,expected):
    calls=[]
    def run(args,**kwargs):
        calls.append(args)
        return code,output
    monkeypatch.setattr(collector,'command',run)
    row=collector.production_bot_api()
    assert row['status']==expected
    assert calls[0][:4]==['docker','exec','kudavpn-premium-bot-1','python']
    source=calls[0][-1]
    assert "os.environ.get('BOT_TOKEN'" in source and '/getMe' in source
    assert 'sendMessage' not in source and 'print(token)' not in source
    compile(source,'container-read-only-probe','exec')
