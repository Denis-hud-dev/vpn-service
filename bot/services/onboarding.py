"""Pilot-only greeting ledger, isolated from the existing subscription database."""
import asyncio
import os
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from sqlalchemy import select
from bot.db.models import User


class PilotOnboardingService:
    def __init__(self, sessions, state_file: str):
        self.sessions = sessions
        self.path = Path(state_file)
        if not self.path.is_absolute():
            raise ValueError("Pilot onboarding state must use an absolute private path")

    def _connect(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        connection = sqlite3.connect(self.path, timeout=3)
        connection.execute("CREATE TABLE IF NOT EXISTS onboarding ("
            "telegram_id INTEGER PRIMARY KEY, welcome_claimed INTEGER NOT NULL, "
            "welcome_message_id INTEGER, keyboard_installed INTEGER NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS keyboard_hints ("
            "telegram_id INTEGER PRIMARY KEY, message_id INTEGER NOT NULL)")
        return connection

    @contextmanager
    def _transaction(self):
        with closing(self._connect()) as connection:
            with connection:
                yield connection

    async def user_exists(self, telegram_id: int) -> bool:
        async with self.sessions() as session:
            return await session.scalar(select(User.telegram_id).where(User.telegram_id == telegram_id)) is not None

    async def first_start(self, telegram_id: int, *, new_user: bool) -> bool:
        def claim():
            with self._transaction() as connection:
                result = connection.execute("INSERT OR IGNORE INTO onboarding VALUES (?, ?, NULL, 0)",
                                            (telegram_id, int(new_user)))
                return bool(new_user and result.rowcount == 1)
        return await asyncio.to_thread(claim)

    async def record_welcome(self, telegram_id: int, message_id: int) -> None:
        def record():
            with self._transaction() as connection:
                connection.execute("UPDATE onboarding SET welcome_message_id=? "
                    "WHERE telegram_id=? AND welcome_message_id IS NULL", (message_id, telegram_id))
        await asyncio.to_thread(record)

    async def valid_welcome(self, telegram_id: int, message_id: int) -> bool:
        def check():
            with self._transaction() as connection:
                row = connection.execute("SELECT welcome_claimed, welcome_message_id FROM onboarding "
                    "WHERE telegram_id=?", (telegram_id,)).fetchone()
                return bool(row and row[0] and row[1] == message_id)
        return await asyncio.to_thread(check)

    async def record_keyboard_message(self, telegram_id: int, message_id: int) -> int | None:
        def record():
            with self._transaction() as connection:
                previous = connection.execute("SELECT message_id FROM keyboard_hints "
                    "WHERE telegram_id=?", (telegram_id,)).fetchone()
                connection.execute("INSERT INTO keyboard_hints (telegram_id, message_id) "
                    "VALUES (?, ?) ON CONFLICT(telegram_id) DO UPDATE SET message_id=excluded.message_id",
                    (telegram_id, message_id))
                return previous[0] if previous else None
        return await asyncio.to_thread(record)


class MemoryPilotOnboarding:
    def __init__(self):
        self.known = set()
        self.rows = {}

    async def user_exists(self, telegram_id: int) -> bool:
        return telegram_id in self.known

    async def first_start(self, telegram_id: int, *, new_user: bool) -> bool:
        if telegram_id in self.rows:
            return False
        self.rows[telegram_id] = {'welcome': new_user, 'message_id': None, 'keyboard_message_id': None}
        return new_user

    async def record_welcome(self, telegram_id: int, message_id: int) -> None:
        self.rows[telegram_id]['message_id'] = message_id

    async def valid_welcome(self, telegram_id: int, message_id: int) -> bool:
        row = self.rows.get(telegram_id)
        return bool(row and row['welcome'] and row['message_id'] == message_id)

    async def record_keyboard_message(self, telegram_id: int, message_id: int) -> int | None:
        row = self.rows[telegram_id]
        previous = row['keyboard_message_id']
        row['keyboard_message_id'] = message_id
        return previous
