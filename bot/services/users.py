from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from bot.db.models import User


class UserService:
    def __init__(self, sessions):
        self.sessions = sessions

    async def register(self, telegram_id: int, first_name: str, username: str | None = None,
                       last_name: str | None = None, referrer_id: int | None = None) -> User:
        if telegram_id <= 0:
            raise ValueError("Некорректный Telegram ID")
        if referrer_id == telegram_id or (referrer_id is not None and referrer_id <= 0):
            referrer_id = None
        async with self.sessions.begin() as session:
            # Atomic UPSERT handles concurrent /start; attribution is first-touch only.
            insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
            statement = insert(User).values(telegram_id=telegram_id, first_name=first_name,
                username=username, last_name=last_name, referrer_id=referrer_id)
            statement = statement.on_conflict_do_update(index_elements=[User.telegram_id], set_={
                "username": statement.excluded.username, "first_name": statement.excluded.first_name,
                "last_name": statement.excluded.last_name, "updated_at": func.now(),
            }).returning(User)
            return (await session.scalars(statement)).one()
