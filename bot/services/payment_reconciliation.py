"""Opt-in test-shop reconciliation, reusing the canonical payment lifecycle.

No POST, network listener, VPN issuance or startup registration. The private
SQLite inbox is a retry journal, never the financial source of truth. Commit
page+cursor before fulfillment; replay after a crash uses canonical dedupe.
"""
import asyncio
from contextlib import closing
from datetime import timedelta
import os
import fcntl
from pathlib import Path
import sqlite3
import stat
from uuid import UUID

from sqlalchemy import func, select

from bot.db.models import Payment
from bot.services.subscriptions import as_utc, utcnow
from bot.services.yookassa_api import PROVIDER_ID
from bot.services.yookassa_verification import identity_from_lookup


class ReconciliationJournal:
    def __init__(self, path):
        self.path = Path(path)
        parent = self.path.parent
        if (not self.path.is_absolute() or parent.is_symlink() or not parent.is_dir()
                or parent.stat().st_uid != os.geteuid() or parent.stat().st_mode & 0o077):
            raise ValueError('Private reconciliation directory required')
        self.lease = self.path.with_name(self.path.name + '.lock')
        for path in (self.path, self.lease):
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                        or info.st_nlink != 1 or info.st_mode & 0o077):
                    raise ValueError('Private reconciliation journal required')
            finally:
                os.close(fd)
        with closing(self.connect()) as db, db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sync (
                    kind TEXT PRIMARY KEY, watermark REAL NOT NULL, full_at REAL NOT NULL,
                    lower_at REAL, upper_at REAL, cursor TEXT, full_scan INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS inbox (
                    kind TEXT NOT NULL, object_id TEXT NOT NULL, parent_id TEXT,
                    state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
                    due REAL NOT NULL DEFAULT 0, outcome TEXT,
                    PRIMARY KEY(kind,object_id));
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY, kind TEXT NOT NULL, object_id TEXT NOT NULL,
                    outcome TEXT NOT NULL, occurred REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS cursors (
                    kind TEXT NOT NULL, cursor TEXT NOT NULL, PRIMARY KEY(kind,cursor));
            ''')

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    @staticmethod
    def enqueue(db, kind, object_id, parent=None):
        if (kind not in {'payments', 'refunds'} or not isinstance(object_id, str)
                or not PROVIDER_ID.fullmatch(object_id)
                or kind == 'refunds' and (not isinstance(parent, str) or not PROVIDER_ID.fullmatch(parent))):
            raise ValueError('Invalid event hint')
        prior = db.execute('SELECT parent_id FROM inbox WHERE kind=? AND object_id=?', (kind, object_id)).fetchone()
        if prior and prior[0] != parent:
            raise ValueError('Conflicting event identity')
        db.execute('INSERT INTO inbox(kind,object_id,parent_id) VALUES (?,?,?) '
                   'ON CONFLICT(kind,object_id) DO NOTHING', (kind, object_id, parent))


class TestPaymentReconciler:
    """Single opt-in worker; existing pending poller may safely run alongside it.

    Creation-window discovery overlaps 15 minutes and performs a daily full
    sweep from the first local order. Pending objects remain durable jobs even
    when their creation window is old. Page and job budgets bound each tick.
    """
    __test__ = False

    def __init__(self, sessions, lookup, processor, refund_processor, journal, *,
                 clock=utcnow, enabled=False, receipt_ledger=None):
        self.sessions, self.lookup, self.processor = sessions, lookup, processor
        self.refunds, self.journal = refund_processor, journal
        self.clock, self.enabled, self.receipts = clock, enabled, receipt_ledger
        self.lock = asyncio.Lock()

    async def _known(self, provider_id):
        async with self.sessions() as session:
            return await session.scalar(select(Payment.id).where(
                Payment.provider == 'yookassa', Payment.provider_payment_id == provider_id))

    async def accept_hint(self, kind, object_id, parent=None):
        if not self.enabled:
            raise RuntimeError('Reconciliation disabled')
        # A public hint cannot make us query arbitrary provider IDs.
        if await self._known(parent if kind == 'refunds' else object_id) is None:
            return False
        with closing(self.journal.connect()) as db, db:
            self.journal.enqueue(db, kind, object_id, parent)
        return True

    async def _discover(self, kind, beginning, now, pages):
        stamp, first = now.timestamp(), beginning.timestamp()
        with closing(self.journal.connect()) as db, db:
            db.execute('INSERT INTO sync(kind,watermark,full_at) VALUES (?,?,0) '
                       'ON CONFLICT(kind) DO NOTHING', (kind, first))
            row = db.execute('SELECT watermark,full_at,lower_at,upper_at,cursor,full_scan '
                             'FROM sync WHERE kind=?', (kind,)).fetchone()
            if row[2] is None:
                full = row[1] == 0 or stamp - row[1] >= 86400
                lower = first if full else max(first, row[0] - 900)
                # Settlement/discovery lag; never claim this window is realtime.
                upper = max(lower, stamp - 60)
                db.execute('UPDATE sync SET lower_at=?,upper_at=?,cursor=NULL,full_scan=? WHERE kind=?',
                           (lower, upper, int(full), kind))
                db.execute('DELETE FROM cursors WHERE kind=?', (kind,))
                row = (row[0], row[1], lower, upper, None, int(full))
        lower, upper, cursor, full = row[2:]
        for _ in range(pages):
            items, following = await self.lookup.list_page(kind,
                now.fromtimestamp(lower, now.tzinfo), now.fromtimestamp(upper, now.tzinfo), cursor)
            if following is not None and (following == cursor or not items):
                raise ValueError('Reconciliation cursor did not advance')
            accepted = []
            for item in items:
                if kind == 'payments':
                    try:
                        order_id = UUID(item.get('metadata', {}).get('order_id', ''))
                    except (ValueError, TypeError, AttributeError):
                        continue
                    async with self.sessions() as session:
                        known = await session.scalar(select(Payment.id).where(
                            Payment.id == order_id, Payment.provider == 'yookassa'))
                    if known is None:
                        continue
                accepted.append(item)
            with closing(self.journal.connect()) as db, db:
                if following is not None:
                    if db.execute('SELECT 1 FROM cursors WHERE kind=? AND cursor=?', (kind, following)).fetchone():
                        raise ValueError('Reconciliation cursor cycle')
                    db.execute('INSERT INTO cursors VALUES (?,?)', (kind, following))
                for item in accepted:
                    self.journal.enqueue(db, kind, item['id'], item.get('payment_id') if kind == 'refunds' else None)
                if following is not None:
                    db.execute('UPDATE sync SET cursor=? WHERE kind=?', (following, kind))
                else:
                    db.execute('UPDATE sync SET watermark=?,full_at=?,lower_at=NULL,upper_at=NULL,cursor=NULL '
                               'WHERE kind=?', (upper, stamp if full else row[1], kind))
            if following is None:
                break
            cursor = following

    async def _payment(self, provider_id):
        # The provider list is only discovery; a separate authenticated GET
        # proves metadata/amount before repairing a lost local ID binding.
        data = await self.lookup.fetch(provider_id)
        try:
            key = UUID(data.get('metadata', {}).get('order_id', ''))
        except (ValueError, TypeError, AttributeError):
            raise ValueError('Invalid provider order') from None
        identity = identity_from_lookup(key, data)
        async with self.sessions.begin() as session:
            row = await session.scalar(select(Payment).where(Payment.id == key).with_for_update())
            if (row is None or row.provider != 'yookassa' or data.get('test') is not True
                    or identity.provider_id != provider_id or row.currency != 'RUB'
                    or row.amount_kopecks != identity.amount_kopecks
                    or row.provider_payment_id not in (None, provider_id)):
                raise ValueError('Payment requires review')
            if row.status == 'refunded':
                return 'refunded'
            if row.provider_payment_id is None:
                if row.status != 'pending':
                    raise ValueError('Order requires review')
                row.provider_payment_id = provider_id
        return await self.processor.process(provider_id)

    async def tick(self, *, pages=2, jobs=20):
        if not self.enabled:
            return {'enabled': False}
        if not 1 <= pages <= 10 or not 1 <= jobs <= 100:
            raise ValueError('Bounded reconciliation budget required')
        async with self.lock:
            # Different runner instances/processes must not share a cursor.
            # Separate inode: flock on the database file conflicts with SQLite
            # locking on some platforms (including macOS).
            with self.journal.lease.open('rb') as lease:
                try:
                    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return {'enabled': True, 'busy': True}
                return await self._tick(pages, jobs)

    async def _tick(self, pages, jobs):
        now = as_utc(self.clock())
        async with self.sessions() as session:
            first = await session.scalar(select(func.min(Payment.created_at)).where(Payment.provider == 'yookassa'))
        if first is None:
            return {'enabled': True, 'processed': 0, 'discovery_errors': 0}
        errors = 0
        for kind in ('payments', 'refunds'):
            try:
                await self._discover(kind, as_utc(first) - timedelta(seconds=1), now, pages)
            except Exception:
                # Persisted cursor is untouched on a failed page. Existing
                # jobs still run even if either list endpoint is unavailable.
                errors += 1
        with closing(self.journal.connect()) as db:
            pending = db.execute("SELECT kind,object_id,parent_id,attempts FROM inbox WHERE state IN ('queued','retry') "
                                 'AND due<=? ORDER BY due,kind,object_id LIMIT ?', (now.timestamp(), jobs)).fetchall()
        processed, review_count, retries = 0, 0, 0
        for kind, key, parent, attempts in pending:
            state, outcome, delay = 'done', 'review', 0
            try:
                if kind == 'payments':
                    outcome = await self._payment(key)
                elif await self._known(parent) is None:
                    # A payment may be waiting for ID repair; do not lose its refund.
                    state, outcome, delay = 'retry', 'unmatched_parent', 300
                else:
                    # A refund may arrive while its parent is still pending
                    # locally (lost webhook/backoff). Reconcile the parent first
                    # so a later payment retry cannot grant refunded time.
                    await self._payment(parent)
                    outcome = await self.refunds.process(key, parent)
                if outcome in {'pending', 'waiting_for_capture'}:
                    state, delay = 'retry', 30
                if outcome == 'review':
                    state = 'review'
            except ValueError:
                state, outcome = 'review', 'verification_failed'
            except Exception:
                state, outcome = 'retry', 'temporarily_unavailable'
                delay = min(300, 30 * 2 ** min(attempts, 4))
            with closing(self.journal.connect()) as db, db:
                previous = db.execute('SELECT outcome FROM inbox WHERE kind=? AND object_id=?',
                                      (kind, key)).fetchone()[0]
                db.execute('UPDATE inbox SET state=?,outcome=?,attempts=attempts+1,due=? '
                           'WHERE kind=? AND object_id=?', (state, outcome, now.timestamp()+delay, kind, key))
                if outcome != previous:
                    db.execute('INSERT INTO events(kind,object_id,outcome,occurred) VALUES (?,?,?,?)',
                               (kind, key, outcome, now.timestamp()))
            processed += 1
            review_count += state == 'review'
            retries += state == 'retry'
        if self.receipts is not None:
            # Recoverable hook, including when fulfillment committed before
            # an inbox update. Never marks issuance/delivery by itself.
            await self.receipts.tick()
        return {'enabled': True, 'processed': processed, 'discovery_errors': errors,
                'review': review_count, 'retry': retries}
