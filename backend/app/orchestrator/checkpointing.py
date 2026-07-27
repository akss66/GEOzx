"""LangGraph checkpoint storage used by production workers."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


def postgres_checkpoint_dsn(database_url: str) -> str:
    """Convert SQLAlchemy's asyncpg URL into a psycopg connection string."""

    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


@asynccontextmanager
async def open_postgres_checkpointer(database_url: str) -> AsyncIterator[Any]:
    """Open and initialize the official asynchronous PostgreSQL saver."""

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    dsn = postgres_checkpoint_dsn(database_url)
    async with AsyncPostgresSaver.from_conn_string(dsn) as checkpointer:
        await checkpointer.setup()
        yield checkpointer
