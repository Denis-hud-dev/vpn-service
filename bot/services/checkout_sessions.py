"""Owner-only pilot browser sessions; opaque credentials are stored as hashes."""
from contextlib import closing
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time

TOKEN = re.compile(r'[A-Za-z0-9_-]{43}\Z')


class CheckoutSessions:
    def __init__(self, path: str, owner_id: int, *, clock=time.time):
        self.path, self.owner_id, self.clock = Path(path), owner_id, clock
        if not self.path.is_absolute() or owner_id <= 0:
            raise ValueError('Private absolute session path and pilot owner required')
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.parent.is_symlink() or self.path.parent.stat().st_mode & 0o077:
            raise ValueError('Session directory must be private (0700)')
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        with closing(self._connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS sessions '
                       '(digest TEXT PRIMARY KEY, owner INTEGER NOT NULL, '
                       'kind TEXT NOT NULL, expires INTEGER NOT NULL)')

    def _connect(self):
        return sqlite3.connect(self.path, timeout=3)

    @staticmethod
    def _digest(token):
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise PermissionError('Session unavailable')
        return hashlib.sha256(token.encode()).hexdigest()

    def invite(self) -> str:
        """Trusted local operator only. Never expose this through a public route."""
        token = secrets.token_urlsafe(32)
        with closing(self._connect()) as db, db:
            db.execute('DELETE FROM sessions WHERE expires <= ?', (int(self.clock()),))
            db.execute('INSERT INTO sessions VALUES (?,?,?,?)',
                       (self._digest(token), self.owner_id, 'invite', int(self.clock()) + 600))
        return token

    def exchange(self, token: str) -> str:
        digest = self._digest(token)
        session = secrets.token_urlsafe(32)
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT owner,expires FROM sessions WHERE digest=? AND kind=?',
                             (digest, 'invite')).fetchone()
            if not row or row[0] != self.owner_id or row[1] <= self.clock():
                raise PermissionError('Session unavailable')
            db.execute('DELETE FROM sessions WHERE digest=?', (digest,))
            db.execute('INSERT INTO sessions VALUES (?,?,?,?)',
                       (self._digest(session), self.owner_id, 'browser', int(self.clock()) + 3600))
        return session

    def owner(self, token: str) -> int:
        with closing(self._connect()) as db:
            row = db.execute('SELECT owner,expires FROM sessions WHERE digest=? AND kind=?',
                             (self._digest(token), 'browser')).fetchone()
        if not row or row[0] != self.owner_id or row[1] <= self.clock():
            raise PermissionError('Session unavailable')
        return row[0]

    @staticmethod
    def csrf(token: str) -> str:
        return hmac.new(token.encode(), b'kudavpn-checkout-csrf', hashlib.sha256).hexdigest()

    def logout(self, token: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute('DELETE FROM sessions WHERE digest=?', (self._digest(token),))
