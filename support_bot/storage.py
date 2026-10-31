"""Durable ticket transactions and delivery outbox, independent of KudaVPN data."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time

from support_bot.migrations.v001 import STATEMENTS, VERSION

APPLICATION_ID = 0x4B535550
OPEN = ('new', 'working', 'waiting')
STATUS = {'new': 'Новое', 'working': 'В работе', 'waiting': 'Ожидает пользователя',
          'resolved': 'Решено', 'closed': 'Закрыто'}


class Rejected(ValueError):
    """Fixed safe reason code, never a user message or SQL error."""


def number(ticket):
    return f'KS-{ticket:06d}'


class Store:
    def __init__(self, path: Path, admin_chat: int, clock=time.time):
        self.path, self.admin_chat, self.clock = Path(path), admin_chat, clock

    @contextmanager
    def connect(self):
        # mode=rw: opening the wrong or missing database must never create it.
        if self.path.is_symlink() or self.path.stat().st_mode & 0o077 or self.path.parent.stat().st_mode & 0o077:
            raise Rejected('support_database_permissions_required')
        db = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            if db.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID:
                raise Rejected('not_support_database')
            if db.execute('PRAGMA user_version').fetchone()[0] != VERSION:
                raise Rejected('support_migration_required')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def migrate(self):
        if not self.path.is_absolute() or self.path.name != 'support.sqlite3' or self.path.is_symlink():
            raise Rejected('support_database_path_required')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.parent.stat().st_mode & 0o077:
            raise Rejected('support_directory_permissions_required')
        if not self.path.exists():
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        if self.path.stat().st_mode & 0o077:
            raise Rejected('support_database_permissions_required')
        db = sqlite3.connect(self.path)
        try:
            db.execute('BEGIN EXCLUSIVE')
            app = db.execute('PRAGMA application_id').fetchone()[0]
            version = db.execute('PRAGMA user_version').fetchone()[0]
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if app not in (0, APPLICATION_ID) or version > VERSION or (app == 0 and tables):
                raise Rejected('not_support_database')
            if version == 0:
                for statement in STATEMENTS:
                    db.execute(statement)
                db.execute(f'PRAGMA application_id={APPLICATION_ID}')
                db.execute(f'PRAGMA user_version={VERSION}')
            db.commit()
            db.execute('PRAGMA journal_mode=WAL')
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def session(self, owner):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO sessions(owner) VALUES(?)', (owner,))
            return dict(db.execute('SELECT * FROM sessions WHERE owner=?', (owner,)).fetchone())

    def set_session(self, owner, **values):
        if not values.keys() <= {'category', 'question', 'target', 'nav_id', 'nav_key'}:
            raise Rejected('invalid_session_field')
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO sessions(owner) VALUES(?)', (owner,))
            if values:
                db.execute('UPDATE sessions SET ' + ','.join(k+'=?' for k in values) + ' WHERE owner=?',
                           (*values.values(), owner))

    def list_tickets(self, owner):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM tickets WHERE owner=? ORDER BY id DESC LIMIT 10', (owner,))]

    @staticmethod
    def ticket(db, ticket, owner=None):
        row = db.execute('SELECT * FROM tickets WHERE id=?', (ticket,)).fetchone()
        if not row or (owner is not None and row['owner'] != owner):
            raise Rejected('ticket_not_found')
        return dict(row)

    def get_ticket(self, ticket, owner):
        with self.connect() as db:
            return self.ticket(db, ticket, owner)

    def transcript(self, ticket, owner):
        with self.connect() as db:
            self.ticket(db, ticket, owner)
            return [dict(r) for r in db.execute('SELECT * FROM history WHERE ticket=? ORDER BY id DESC LIMIT 8', (ticket,))][::-1]

    def _rate(self, db, actor):
        bucket = int(self.clock() // 60)
        db.execute('INSERT INTO rates VALUES(?,?,1) ON CONFLICT(actor,bucket) DO UPDATE SET count=count+1', (actor,bucket))
        if db.execute('SELECT count FROM rates WHERE actor=? AND bucket=?', (actor,bucket)).fetchone()[0] > 10:
            raise Rejected('rate_limited')
        db.execute('DELETE FROM rates WHERE bucket<?', (bucket-60,))

    def _event(self, db, ticket, actor, kind, text, attachment, source):
        return db.execute('INSERT INTO history(ticket,actor,kind,text,attachment,source,created_at) VALUES(?,?,?,?,?,?,?)',
            (ticket, actor, kind, text, json.dumps(attachment) if attachment else None, source, self.clock())).lastrowid

    def _queue(self, db, event, ticket, chat, audience, text, attachment=None):
        payload = json.dumps({'text': text, 'attachment': attachment}, ensure_ascii=False)
        db.execute('INSERT INTO deliveries(event,ticket,chat,audience,payload,due) VALUES(?,?,?,?,?,?)',
                   (event,ticket,chat,audience,payload,self.clock()))

    def user_message(self, owner, category, question, text, attachment, source):
        with self.connect() as db:
            previous = db.execute('SELECT ticket FROM processed_inputs WHERE source=?', (source,)).fetchone()
            if previous:
                return previous['ticket'], False
            self._rate(db, owner)
            session = db.execute('SELECT target FROM sessions WHERE owner=?', (owner,)).fetchone()
            if session and session['target']:
                target = self.ticket(db, session['target'], owner)
                if target['status'] not in OPEN:
                    raise Rejected('ticket_closed')
            else:
                row = db.execute("SELECT * FROM tickets WHERE owner=? AND status IN ('new','working','waiting')", (owner,)).fetchone()
                target = dict(row) if row else None
            is_new = target is None
            if is_new:
                last = db.execute('SELECT created_at FROM tickets WHERE owner=? ORDER BY id DESC LIMIT 1', (owner,)).fetchone()
                if last and self.clock() - last[0] < 60:
                    raise Rejected('rate_limited')
                tid = db.execute("INSERT INTO tickets(owner,category,question,description,status,created_at,updated_at) VALUES(?,?,?,?,'new',?,?)",
                    (owner,category,question,text,self.clock(),self.clock())).lastrowid
                target = self.ticket(db,tid)
            tid = target['id']
            # Suppress identical re-sends within a short window, including photos.
            duplicate = db.execute("SELECT 1 FROM history WHERE ticket=? AND kind='user' AND text=? AND coalesce(attachment,'')=? AND created_at>?",
                (tid,text,json.dumps(attachment) if attachment else '',self.clock()-60)).fetchone()
            db.execute('INSERT INTO processed_inputs VALUES(?,?)', (source,tid))
            if duplicate:
                return tid, False
            event = self._event(db,tid,owner,'user',text,attachment,source)
            state = 'working' if target['assignee'] else 'new'
            db.execute('UPDATE tickets SET status=?,updated_at=? WHERE id=?', (state,self.clock(),tid))
            self._queue(db,event,tid,self.admin_chat,'operator',
                f'{number(tid)} · {"Новое обращение" if is_new else "Дополнение"}\n'
                f'Категория: {target["category"]}\nВопрос: {target["question"] or "Не выбран"}\n\n{text}', attachment)
            self._queue(db,event,tid,owner,'user',
                f'{number(tid)} · {"Обращение принято" if is_new else "Дополнение сохранено"}.\n'
                'Сообщение поставлено в очередь поддержки. Статус — в разделе «Мои обращения».')
            db.execute('UPDATE sessions SET target=?,category=NULL,question=NULL WHERE owner=?', (tid,owner))
            return tid, True

    def operator_action(self, ticket, actor, action, source, text='', attachment=None):
        states = {'take':'working', 'ask':'waiting', 'reply':'waiting', 'resolve':'resolved', 'close':'closed'}
        if action not in states:
            raise Rejected('invalid_action')
        with self.connect() as db:
            if db.execute('SELECT 1 FROM history WHERE source=?', (source,)).fetchone():
                return False
            target = self.ticket(db,ticket)
            if target['status'] not in OPEN or target['assignee'] not in (None,actor):
                raise Rejected('ticket_unavailable')
            self._rate(db,actor)
            status = states[action]
            event = self._event(db,ticket,actor,'operator' if action=='reply' else 'status',
                                text or STATUS[status],attachment,source)
            db.execute('UPDATE tickets SET status=?,assignee=?,updated_at=? WHERE id=?', (status,actor,self.clock(),ticket))
            body = text if action=='reply' else {
                'take':'Оператор принял обращение в работу.',
                'ask':'Нужны дополнительные сведения: модель устройства, версия ОС и Happ, тип сети и точный текст ошибки. Не отправляйте персональную ссылку или ключи.',
                'resolve':'Обращение отмечено как решённое. Если проблема осталась, создайте новое обращение.',
                'close':'Обращение закрыто. История сохранена.'}[action]
            self._queue(db,event,ticket,target['owner'],'user',f'{number(ticket)} · Поддержка KudaVPN\n\n{body}',attachment)
            self._queue(db,event,ticket,self.admin_chat,'operator',f'{number(ticket)} · {STATUS[status]}\n{text or "Статус обновлён"}')
            return True

    def close_user(self, ticket, owner, source):
        with self.connect() as db:
            target = self.ticket(db,ticket,owner)
            if target['status']=='closed' or db.execute('SELECT 1 FROM history WHERE source=?', (source,)).fetchone():
                return
            event = self._event(db,ticket,owner,'status','Закрыто пользователем',None,source)
            db.execute("UPDATE tickets SET status='closed',updated_at=? WHERE id=?", (self.clock(),ticket))
            self._queue(db,event,ticket,self.admin_chat,'operator',f'{number(ticket)} · Закрыто пользователем')
            self._queue(db,event,ticket,owner,'user',f'{number(ticket)} · Обращение закрыто. История сохранена.')
            db.execute('UPDATE sessions SET target=NULL WHERE owner=? AND target=?', (owner,ticket))

    def bound_ticket(self, chat, message):
        with self.connect() as db:
            row = db.execute('SELECT ticket FROM operator_messages WHERE chat=? AND message=?', (chat,message)).fetchone()
            return row[0] if row else None

    def claim(self):
        with self.connect() as db:
            now = self.clock()
            row = db.execute("""SELECT d.* FROM deliveries d WHERE
                ((d.state='pending' AND d.due<=?) OR (d.state='sending' AND d.lease<=?))
                AND NOT EXISTS(SELECT 1 FROM deliveries p WHERE p.id<d.id AND p.chat=d.chat
                    AND p.ticket=d.ticket AND p.state IN ('pending','sending')) ORDER BY d.id LIMIT 1""", (now,now)).fetchone()
            if not row:
                return None
            db.execute("UPDATE deliveries SET state='sending',attempts=attempts+1,lease=? WHERE id=?", (now+60,row['id']))
            return dict(row)

    def sent(self, delivery, message):
        with self.connect() as db:
            db.execute("UPDATE deliveries SET state='sent',telegram_id=?,lease=NULL,error_code=NULL WHERE id=?", (message,delivery['id']))
            if delivery['audience']=='operator':
                db.execute('INSERT OR IGNORE INTO operator_messages VALUES(?,?,?)',
                           (delivery['chat'],message,delivery['ticket']))

    def failed(self, delivery, code, delay=30, permanent=False):
        with self.connect() as db:
            db.execute("UPDATE deliveries SET state=?,due=?,lease=NULL,error_code=? WHERE id=?",
                ('blocked' if permanent else 'pending', self.clock()+min(3600,max(delay,2**min(delivery['attempts']+1,10))),
                 code,delivery['id']))

    def health(self):
        with self.connect() as db:
            row = db.execute("SELECT sum(state IN ('pending','sending')) pending,sum(state='blocked') blocked FROM deliveries").fetchone()
            result = {'pending':row['pending'] or 0, 'blocked':row['blocked'] or 0}
            db.execute('INSERT OR REPLACE INTO worker_health VALUES(1,?,?,?)',
                       (self.clock(),result['pending'],result['blocked']))
            return result


    def offset(self):
        with self.connect() as db:
            row = db.execute('SELECT offset FROM polling_cursor WHERE id=1').fetchone()
            return row[0] if row else 0

    def save_offset(self, offset):
        with self.connect() as db:
            db.execute('INSERT INTO polling_cursor VALUES(1,?) ON CONFLICT(id) DO UPDATE SET offset=max(offset,excluded.offset)', (offset,))

    def delivery_health(self):
        with self.connect() as db:
            row = db.execute("SELECT sum(d.state IN ('pending','sending')) pending, sum(d.state='blocked') blocked, min(CASE WHEN d.state IN ('pending','sending') THEN h.created_at END) oldest FROM deliveries d JOIN history h ON h.id=d.event").fetchone()
            heartbeat = db.execute('SELECT checked_at FROM worker_health WHERE id=1').fetchone()
            return {'pending':row['pending'] or 0, 'blocked':row['blocked'] or 0,
                    'overdue_seconds':max(0,int(self.clock()-row['oldest'])) if row['oldest'] else 0,
                    'worker_checked_at':heartbeat[0] if heartbeat else None}

    def retry_blocked(self, delivery_id):
        with self.connect() as db:
            return db.execute("UPDATE deliveries SET state='pending',due=?,lease=NULL WHERE id=? AND state='blocked'",
                              (self.clock(),delivery_id)).rowcount == 1


    def bind(self, bot_id, admin_chat):
        with self.connect() as db:
            row = db.execute('SELECT bot_id,admin_chat FROM bot_binding WHERE id=1').fetchone()
            if row and (row['bot_id'] != bot_id or row['admin_chat'] != admin_chat):
                raise Rejected('support_database_identity_mismatch')
            db.execute('INSERT OR IGNORE INTO bot_binding VALUES(1,?,?)', (bot_id,admin_chat))
