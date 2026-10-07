"""DATABASE_URL is read from environment, never stored in alembic.ini."""
import asyncio
import os
from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from bot.db.models import Base


def configure(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def online(url):
    engine = create_async_engine(url, poolclass=NullPool, hide_parameters=True)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(configure)
    finally:
        await engine.dispose()


connection = context.config.attributes.get("connection")
if connection is not None:
    configure(connection)
else:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise RuntimeError("Укажите DATABASE_URL для миграций")
    if context.is_offline_mode():
        context.configure(url=url, target_metadata=Base.metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
        with context.begin_transaction():
            context.run_migrations()
    else:
        asyncio.run(online(url))
