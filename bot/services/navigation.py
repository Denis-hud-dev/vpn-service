"""Only navigation screen IDs are stored; receipts/support history are never tracked."""
from dataclasses import dataclass
from typing import Protocol
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from bot.db.models import ActiveScreen


@dataclass(frozen=True)
class ScreenReference:
    user_id: int
    chat_id: int
    message_id: int


class ScreenStore(Protocol):
    async def load(self, user_id: int) -> ScreenReference | None: ...
    async def save(self, user_id: int, chat_id: int, message_id: int) -> None: ...


class MemoryScreenStore:
    """Demo/test storage; production uses PostgreSQL."""
    def __init__(self):
        self.screens: dict[int, ScreenReference] = {}

    async def load(self, user_id: int) -> ScreenReference | None:
        return self.screens.get(user_id)

    async def save(self, user_id: int, chat_id: int, message_id: int) -> None:
        self.screens[user_id] = ScreenReference(user_id, chat_id, message_id)


class PostgresScreenStore:
    def __init__(self, sessions):
        self.sessions = sessions

    async def load(self, user_id: int) -> ScreenReference | None:
        async with self.sessions() as session:
            screen = await session.get(ActiveScreen, user_id)
            return ScreenReference(screen.user_id, screen.chat_id, screen.message_id) if screen else None

    async def save(self, user_id: int, chat_id: int, message_id: int) -> None:
        async with self.sessions.begin() as session:
            insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
            statement = insert(ActiveScreen).values(user_id=user_id, chat_id=chat_id, message_id=message_id)
            await session.execute(statement.on_conflict_do_update(index_elements=[ActiveScreen.user_id],
                set_={"chat_id": chat_id, "message_id": message_id, "updated_at": func.now()}))
