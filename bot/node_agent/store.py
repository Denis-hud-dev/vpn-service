"""Durable desired leases. SQLite belongs only to this individual VPN node."""
import os
import sqlite3
from pathlib import Path
from uuid import UUID


class LeaseConflict(ValueError):
    pass


class LeaseStore:
    def __init__(self, path: str):
        file = Path(path)
        file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if file.parent.stat().st_mode & 0o077:
            raise ValueError('Node state directory must be private')
        if file.exists() and file.stat().st_mode & 0o077:
            raise ValueError('Node state file must be private')
        self.db = sqlite3.connect(file)
        os.chmod(file, 0o600)
        self.db.execute('CREATE TABLE IF NOT EXISTS leases '
            '(credential TEXT PRIMARY KEY, revision INTEGER NOT NULL, '
            'enabled INTEGER NOT NULL, expires_at INTEGER, updated_at INTEGER NOT NULL)')
        self.db.commit()

    def apply(self, credential: UUID, revision: int, enabled: bool, expires_at: int | None, now: int):
        key = str(credential)
        previous = self.db.execute('SELECT revision, enabled, expires_at FROM leases '
                                   'WHERE credential=?', (key,)).fetchone()
        if previous and revision < previous[0]:
            raise LeaseConflict('stale revision')
        if previous and revision == previous[0]:
            if (bool(previous[1]), previous[2]) != (enabled, expires_at):
                raise LeaseConflict('conflicting revision')
            return
        with self.db:
            self.db.execute('INSERT INTO leases VALUES (?,?,?,?,?) '
                'ON CONFLICT(credential) DO UPDATE SET revision=excluded.revision, '
                'enabled=excluded.enabled, expires_at=excluded.expires_at, '
                'updated_at=excluded.updated_at',
                (key, revision, int(enabled), expires_at, now))

    def desired(self, now: int):
        rows = self.db.execute('SELECT credential FROM leases WHERE enabled=1 '
                               'AND expires_at>?', (now,)).fetchall()
        return {UUID(row[0]) for row in rows}

    def all_managed(self):
        rows = self.db.execute('SELECT credential FROM leases').fetchall()
        return {UUID(row[0]) for row in rows}

    def close(self):
        self.db.close()
