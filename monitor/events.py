"""Bounded event accounting. Raw log content never leaves this module."""
from datetime import datetime
import json
import re

CATEGORIES = ('telegram_api', 'network', 'application', 'unclassified')
MAX_LINES = 5000
MAX_BYTES = 1048576
RECORD = re.compile(r'^\d{4}-\d\d-\d\d [\d:,.-]+ (DEBUG|INFO|WARNING|ERROR|CRITICAL) ([\w.]+): (.*)$')
POLL = re.compile(r'^polling_failure kind=(\w+)$')
MASKED = 'Dependency warning/error; inspect timing and service readiness'
API = {'TelegramServerError', 'TelegramConflictError', 'TelegramUnauthorizedError',
       'TelegramRetryAfter', 'TelegramBadRequest'}


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Missing timestamp')
    date = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if date.tzinfo is None:
        raise ValueError('Ambiguous timezone')
    return date.timestamp()


def category(kind, logger):
    if kind in API:
        return 'telegram_api'
    if kind in ('TelegramNetworkError', 'TimeoutError'):
        return 'network'
    return 'application' if logger.startswith('bot.') and not kind else 'unclassified'


def error_events(output, start, end, *, available=True):
    """Count framed records, not words. Uncertain boundaries mean unknown total.

    Current aiogram emits one sanitized ERROR after each typed polling warning.
    Only that exact immediate wrapper is paired, never arbitrary similar errors.
    JSON producers may supply event_id to identify repeated fragments explicitly.
    """
    counts = dict.fromkeys(CATEGORIES, 0)
    complete = available and len(output.encode()) < MAX_BYTES
    lines = output.splitlines()
    complete = complete and len(lines) <= MAX_LINES
    last = None
    pending = None
    framed = False
    seen = {}
    previous = None
    for line in lines[:MAX_LINES]:
        try:
            prefix, body = line.split(' ', 1)
            stamp = timestamp(prefix)
            if not start <= stamp <= end:
                raise ValueError('Outside requested window')
            if previous is not None and stamp < previous:
                # Docker merges stdout/stderr: ordering is not guaranteed.
                pending = None
            previous = stamp
            identity = None
            if body.startswith('{'):
                data = json.loads(body)
                level, logger = data['level'], data['logger']
                message = data.get('message', '')
                kind = data.get('exception_type', '')
                identity = data.get('event_id')
                if not all(isinstance(v, str) for v in (level, logger, message, kind)):
                    raise ValueError('Invalid record')
                if identity is not None and (not isinstance(identity, str) or not 1 <= len(identity) <= 128):
                    raise ValueError('Invalid identity')
            else:
                match = RECORD.fullmatch(body)
                if not match:
                    # Continuations are not new events. An unframed prefix is
                    # incomplete, including when tail cut into a traceback.
                    if not framed or not (body.startswith((' ', '\t', 'Traceback ', 'During handling ',
                            'The above exception ')) or re.match(r'^[\w.]+(?:Error|Exception):', body)):
                        complete = False
                    pending = None
                    continue
                level, logger, message = match.groups()
                kind = ''
            if level not in ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'):
                raise ValueError('Unknown level')
            framed = True
            poll = POLL.fullmatch(message) if logger == 'bot.observability' else None
            if poll:
                kind = poll.group(1)
            wrapper = (pending is not None and 0 <= stamp-pending <= 1
                       and logger == 'aiogram.dispatcher' and level == 'ERROR' and message == MASKED)
            pending = None
            if wrapper:
                continue
            if logger == 'aiogram.dispatcher' and level == 'ERROR' and message == MASKED:
                # This could be a wrapper whose typed anchor is missing or
                # interleaved. Do not claim an exact number either way.
                complete = False
            if level not in ('ERROR', 'CRITICAL') and not poll:
                continue
            group = category(kind, logger)
            if identity is not None:
                signature = (stamp, group)
                if identity in seen:
                    if seen[identity] != signature:
                        complete = False
                    continue
                seen[identity] = signature
            counts[group] += 1
            last = stamp if last is None else max(last, stamp)
            if poll:
                pending = stamp
        except (ValueError, KeyError, TypeError, OverflowError):
            complete = False
            pending = None
            framed = False
    return dict(complete=bool(complete), counts=counts if complete else None,
                last_event=last, window_start=start, window_end=end)
