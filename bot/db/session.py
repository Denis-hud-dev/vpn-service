from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from bot.db.models import User, Subscription, Device, VPNServer, Payment, ActiveScreen, SubscriptionHistory, SubscriptionToken, VPNNode, NodeHealth, NodeAccess
from bot.observability import instrument_database


class Database:
    def __init__(self, url: str):
        parsed = make_url(url)
        if parsed.drivername != "postgresql+psycopg":
            raise ValueError("DATABASE_URL должен использовать postgresql+psycopg.")
        self.engine = create_async_engine(parsed, pool_pre_ping=True, hide_parameters=True,
            pool_size=5, max_overflow=5, pool_timeout=3,
            connect_args={"connect_timeout": 5,
                          "options": "-c statement_timeout=5000 -c lock_timeout=2000"})
        instrument_database(self.engine)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def ping(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def check(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
            # Missing migrations must fail startup before polling begins.
            for model in (User, Subscription, Device, VPNServer, Payment, ActiveScreen, SubscriptionHistory, SubscriptionToken, VPNNode, NodeHealth, NodeAccess):
                await connection.execute(select(model).limit(0))
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            if revision != "0007_happ_link_nonce":
                raise RuntimeError("Примените миграции: alembic upgrade head")

    async def close(self) -> None:
        await self.engine.dispose()
