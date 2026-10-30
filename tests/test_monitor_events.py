"""Offline regressions from the six observed polling failures. No real services."""
from datetime import datetime, timezone
import json
import re

import pytest

from monitor import collector
from monitor.common import PERIOD, check, validate_snapshot
from monitor.events import MAX_LINES, MASKED, error_events
from monitor.worker import report

END = int(datetime(2026, 10, 30, 4, 0, 40, tzinfo=timezone.utc).timestamp())
START = END-PERIOD


def line(second, level, logger, message):
    return f'2026-10-30T01:19:{second:02d}.000000000Z 2026-10-30 01:19:{second:02d},000 {level} {logger}: {message}'


def six_events():
    timestamps = ('01:19:28.621462583', '01:19:29.643301538', '01:19:30.989256320',
                  '01:19:32.763393107', '01:19:35.131411314', '01:20:18.499191391')
    rows = []
    for index, stamp in enumerate(timestamps):
        kind = 'TelegramServerError' if index < 5 else 'TelegramNetworkError'
        for level, logger, message in (
            ('WARNING', 'bot.observability', 'polling_failure kind='+kind),
            ('ERROR', 'aiogram.dispatcher', MASKED), ('WARNING', 'aiogram.dispatcher', MASKED)):
            rows.append(f'2026-10-30T{stamp}Z 2026-10-30 {stamp[:8]},000 {level} {logger}: {message}')
    return '\n'.join(rows)


def test_six_real_events_not_eighteen_word_matches():
    logs = six_events()
    assert len(re.findall(r'(?im)\bERROR\b|Traceback|TelegramConflictError', logs)) == 18
    stats = error_events(logs, START, END)
    assert stats['complete']
    assert stats['counts'] == dict(telegram_api=5, network=1, application=0, unclassified=0)
    assert abs(stats['last_event']-datetime(2026, 10, 30, 1, 20, 18, 499191, tzinfo=timezone.utc).timestamp()) < .001


def test_multiline_traceback_is_one_application_event():
    logs = '\n'.join([line(1, 'ERROR', 'bot.handler', 'Request failed'),
        '2026-10-30T01:19:01Z Traceback (most recent call last):',
        '2026-10-30T01:19:01Z   File "offline.py", line 1',
        '2026-10-30T01:19:01Z ValueError: error error error'])
    stats = error_events(logs, START, END)
    assert stats['complete'] and stats['counts']['application'] == 1


def structured(identity, **extra):
    return '2026-10-30T01:19:01Z '+json.dumps(dict(
        event_id=identity, level='ERROR', logger='bot.handler', message='offline-secret', **extra))


def test_json_event_identity_deduplicates_fragments_but_not_separate_events():
    stats = error_events('\n'.join([structured('one'), structured('one'), structured('two')]), START, END)
    assert stats['complete'] and stats['counts']['application'] == 2
    assert 'offline-secret' not in json.dumps(stats) and 'event_id' not in json.dumps(stats)


def test_identical_plain_messages_at_different_times_are_not_deduplicated():
    stats = error_events('\n'.join(line(i, 'ERROR', 'bot.handler', 'same error') for i in (1, 2)), START, END)
    assert stats['complete'] and stats['counts']['application'] == 2


def test_unrecognized_dependency_error_is_counted_without_false_classification():
    stats = error_events(line(1, 'CRITICAL', 'unknown.library', 'failure'), START, END)
    assert stats['complete'] and stats['counts']['unclassified'] == 1


def test_only_exact_immediate_dispatcher_wrapper_is_paired():
    logs = '\n'.join([line(1, 'WARNING', 'bot.observability', 'polling_failure kind=TelegramServerError'),
                     line(1, 'ERROR', 'bot.handler', 'independent failure')])
    stats = error_events(logs, START, END)
    assert stats['counts']['telegram_api'] == 1 and stats['counts']['application'] == 1
    logs += '\n'+line(3, 'ERROR', 'aiogram.dispatcher', MASKED)
    assert not error_events(logs, START, END)['complete']


@pytest.mark.parametrize('key', ['bot_api', 'bot_ready', 'postgres', 'tcp:9443'])
def test_current_confirmed_failures_are_still_red_and_critical(key):
    row = check(key, 'error', 'unreachable', critical=True)
    text = report([dict(host='digitalocean', checks=[row])], END)
    assert '🔴 Ошибка' in text and row['critical']


@pytest.mark.parametrize('logs', [
    'not framed', '2026-10-30T01:19:01Z malformed error record',
    line(1, 'ERROR', 'aiogram.dispatcher', MASKED),
    '2026-10-30T01:19:01Z Traceback (most recent call last):',
    line(1, 'ERROR', 'bot.handler', 'error')+'\nmalformed continuation',
    line(1, 'INFO', 'bot.handler', 'ok')+'\n2026-10-30T01:19:02Z unsupported format',
    '2026-10-30T01:19:01 '+json.dumps(dict(level='ERROR', logger='bot.handler')),
    '2026-10-30T05:19:01Z '+json.dumps(dict(level='ERROR', logger='bot.handler')),
    structured('same')+'\n'+structured('same', exception_type='TelegramNetworkError'),
])
def test_unreliable_format_never_claims_an_exact_count(logs):
    stats = error_events(logs, START, END)
    assert stats['complete'] is False and stats['counts'] is None


@pytest.mark.parametrize('available,logs', [(False, ''), (True, 'x'*1048576),
    (True, '\n'.join([line(1, 'INFO', 'bot.handler', 'ok')]*(MAX_LINES+1)))])
def test_unavailable_or_truncated_logs_are_unknown(available, logs):
    assert not error_events(logs, START, END, available=available)['complete']


def test_no_keyword_counting_in_info_or_warning():
    stats = error_events('\n'.join([line(1, 'INFO', 'bot.handler', 'error handling works'),
        line(2, 'WARNING', 'bot.handler', 'error is a word')]), START, END)
    assert stats['complete'] and sum(stats['counts'].values()) == 0


def test_empty_successful_log_query_is_zero():
    assert error_events('', START, END)['counts'] == dict.fromkeys(
        ('telegram_api', 'network', 'application', 'unclassified'), 0)


def test_journal_counts_cursor_records_not_multiline_message_words(monkeypatch):
    monkeypatch.setattr(collector.time, 'time', lambda: END)
    entry = json.dumps(dict(__CURSOR='offline-one', __REALTIME_TIMESTAMP=str((END-60)*1000000),
                            PRIORITY='3', MESSAGE='error\nerror\nTraceback error'))
    monkeypatch.setattr(collector, 'command', lambda args: (0, entry+'\n'+entry))
    row = collector.journal_errors(['offline.service'])
    assert row['value'] == 1 and row['status'] == 'warning' and not row['critical']
    monkeypatch.setattr(collector, 'command', lambda args: (0, 'unstructured error'))
    assert collector.journal_errors(['offline.service'])['status'] == 'unknown'


def history_row():
    stats = error_events(six_events(), START, END)
    return check('bot_errors', 'warning', 'event_history', 6, stats=stats)


def test_safe_snapshot_carries_validated_counts_and_last_failure():
    data = dict(host='digitalocean', timestamp=END, checks=[history_row()])
    assert validate_snapshot(data, 'digitalocean', END)['checks'][0] == history_row()
    text = report([data], END)
    assert '6 событий' in text and 'Telegram API: 5' in text and 'сеть: 1' in text
    assert '30.10 01:20:18 UTC' in text and '🟡 Предупреждение' in text
    assert '🔴 Ошибка' not in text


@pytest.mark.parametrize('mutation', ['extra', 'total', 'future', 'wrong_window', 'bool_count', 'critical'])
def test_statistics_cannot_smuggle_payloads_or_inconsistent_totals(mutation):
    row = history_row()
    if mutation == 'extra': row['stats']['raw'] = 'offline-secret'
    if mutation == 'total': row['value'] = 18
    if mutation == 'future': row['stats']['last_event'] = END+1
    if mutation == 'wrong_window': row['stats']['window_end'] -= 1
    if mutation == 'bool_count': row['stats']['counts']['network'] = True
    if mutation == 'critical': row['critical'] = True
    data = dict(host='digitalocean', timestamp=END, checks=[row])
    result = validate_snapshot(data, 'digitalocean', END)
    assert result['checks'][0]['id'] == 'collector'
    assert 'offline-secret' not in json.dumps(result)


def test_unknown_and_old_collector_counts_are_not_shown_as_real_counts():
    for row in (check('bot_errors', 'error', 'journal_markers', 18),
                check('bot_errors', 'unknown', 'event_history', stats=error_events('', START, END, available=False))):
        text = report([dict(host='digitalocean', checks=[row])], END)
        assert 'Количество не определено' in text and '18' not in text


def test_collector_requests_fixed_window_timestamps_and_detects_truncation(monkeypatch):
    calls = []
    monkeypatch.setattr(collector.time, 'time', lambda: END)
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return 0, six_events()
    monkeypatch.setattr(collector, 'command', run)
    row = collector.bot_error_history()
    assert row['value'] == 6 and row['status'] == 'warning' and not row['critical']
    args, kwargs = calls[0]
    assert '--timestamps' in args and f'--since={START}' in args and f'--until={END}' in args
    assert kwargs['merge_error'] is True


def retirement_properties(**changes):
    fields = dict(ActiveState='failed', SubState='failed', UnitFileState='disabled',
        Restart='no', MainPID='0', RequiredBy='', WantedBy='', BoundBy='', ConsistsOf='', TriggeredBy='')
    fields.update(changes)
    return '\n'.join(f'{k}={v}' for k, v in fields.items())


def test_retired_controller_requires_current_guards_and_read_only_db_probe(monkeypatch):
    calls = []
    monkeypatch.setattr(collector, 'command', lambda args: (calls.append(args) or (0, retirement_properties())))
    def query(sql):
        assert sql.startswith('SELECT count(*)') and '9443' in sql and 'desired_enabled' in sql
        return 0, '0\n'
    monkeypatch.setattr(collector, 'postgres_query', query)
    row = collector.legacy_controller()
    assert row['status'] == 'retired' and not row['critical']
    assert calls[0][:2] == ['systemctl', 'show']
    assert '⚪ Выведен из эксплуатации' in report([dict(host='justhost', checks=[row])], END)


@pytest.mark.parametrize('changes', [dict(ActiveState='active'), dict(MainPID='1'),
    dict(UnitFileState='enabled'), dict(Restart='always'), dict(RequiredBy='needed.service'),
    dict(TriggeredBy='needed.timer')])
def test_controller_not_retired_if_running_enabled_or_in_use(monkeypatch, changes):
    monkeypatch.setattr(collector, 'command', lambda args: (0, retirement_properties(**changes)))
    row = collector.legacy_controller()
    assert row['status'] == 'error' and row['critical']


@pytest.mark.parametrize('code,output,expected', [(0, '1', 'error'), (1, '', 'unknown'), (0, 'bad', 'unknown')])
def test_retirement_rejects_active_node_or_missing_db_evidence(monkeypatch, code, output, expected):
    monkeypatch.setattr(collector, 'command', lambda args: (0, retirement_properties()))
    monkeypatch.setattr(collector, 'postgres_query', lambda sql: (code, output))
    assert collector.legacy_controller()['status'] == expected


@pytest.mark.parametrize('key', ['postgres', 'tcp:9443', 'unit:kudavpn-pilot-node.service'])
def test_active_infrastructure_cannot_be_marked_retired(key):
    with pytest.raises(ValueError): check(key, 'retired', 'decommissioned')


def test_justhost_still_checks_xray_and_tcp_9443(monkeypatch):
    calls, ports = [], []
    monkeypatch.setattr(collector, 'resources', lambda: [])
    monkeypatch.setattr(collector, 'legacy_controller', lambda: check('unit:kudavpn-pilot-control.service', 'retired', 'decommissioned'))
    def run(args, **kwargs):
        calls.append(args)
        return 0, 'active' if args[:2] == ['systemctl', 'is-active'] else ''
    monkeypatch.setattr(collector, 'command', run)
    monkeypatch.setattr(collector, 'tcp', lambda port: (ports.append(port) or check(f'tcp:{port}', 'error', 'unreachable', critical=True)))
    monkeypatch.setattr(collector, 'ready', lambda key, url: check(key, 'ok'))
    monkeypatch.setattr(collector, 'postgres_query', lambda sql: (0, '1'))
    monkeypatch.setattr(collector, 'http', lambda url: (200, b''))
    monkeypatch.setattr(collector.socket, 'create_connection', lambda *a, **kw: (_ for _ in ()).throw(OSError()))
    rows = collector.collect('justhost')['checks']
    assert ['systemctl', 'is-active', 'kudavpn-pilot-node.service'] in calls
    assert ports == [443, 8443, 9443, 10443, 18443, 18543]
    tcp = next(row for row in rows if row['id'] == 'tcp:9443')
    assert tcp['critical'] and tcp['status'] == 'error'
    assert 'TCP/9443 · соединение: 🔴 Ошибка' in report([dict(host='justhost', checks=[tcp])], END)
