"""数据库与 Redis 接入层。

- SQLAlchemy 2.x async 引擎 / 会话工厂 / `get_session` 依赖
- `Base`：所有 ORM 模型的声明基类（T3 起在 `app/models/` 中继承填充）
- `check_db` / `check_redis`：就绪探针用的轻量连通性检查
"""

from collections.abc import AsyncIterator

import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    """ORM 声明基类。T3 起各模型继承此类，Alembic 以 `Base.metadata` 为目标。"""


engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
)

async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：提供一个请求级 async 会话。"""
    async with async_session() as session:
        yield session


_redis_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    """惰性单例 Redis 客户端。"""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_client


async def check_db() -> bool:
    """DB 连通性检查（就绪探针用，不抛异常）。"""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def check_redis() -> bool:
    """Redis 连通性检查（就绪探针用，不抛异常）。"""
    try:
        return bool(await get_redis().ping())
    except Exception:
        return False
