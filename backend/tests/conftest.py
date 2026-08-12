"""测试夹具：内存 SQLite（async）+ get_session 覆盖 + httpx ASGI 客户端。

让认证/用户接口在无真实 Postgres 时也能端到端测试。
"""

import json

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.core.events as runtime_events
from app.config import settings
from app.core.security import hash_password
from app.db import Base, get_session
from app.main import app
from app.models import Org, User
from app.models.enums import UserRole
from app.schemas.wechat_article import ArticleDocument


def _sqlite_article_document_is_valid(document: str | None) -> int:
    if not isinstance(document, str):
        return 0
    try:
        ArticleDocument.model_validate(json.loads(document))
    except (TypeError, ValueError, ValidationError):
        return 0
    return 1


@pytest_asyncio.fixture(autouse=True)
async def block_unmocked_external_redis(monkeypatch):
    """Keep unit tests deterministic and fail fast on accidental Redis access."""

    class RealtimeRedisStub:
        async def publish(self, *_args, **_kwargs) -> int:
            return 0

    async def reject_external_pool(*_args, **_kwargs):
        raise AssertionError("unit tests must inject a Redis/ARQ test double")

    monkeypatch.setattr(runtime_events, "_pool", None)
    monkeypatch.setattr(runtime_events, "_pool_loop", None)
    monkeypatch.setattr(runtime_events, "create_pool", reject_external_pool)
    monkeypatch.setattr(runtime_events, "get_redis", lambda: RealtimeRedisStub())
    monkeypatch.setattr(settings, "agent_runtime_async_enabled", False)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _register_article_document_check(dbapi_connection, _connection_record) -> None:
        dbapi_connection.run_async(
            lambda raw_connection: raw_connection.create_function(
                "wechat_article_document_is_valid", 1, _sqlite_article_document_is_valid
            )
        )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def client(session):
    async def _override():
        yield session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def admin(session) -> User:
    org = Org(name="测试组织")
    user = User(
        org=org,
        email="admin@test.com",
        hashed_password=hash_password("admin-pw-123"),
        display_name="管理员",
        role=UserRole.ADMIN,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest_asyncio.fixture
async def member(session, admin) -> User:
    user = User(
        org_id=admin.org_id,
        email="user@test.com",
        hashed_password=hash_password("user-pw-123"),
        display_name="员工",
        role=UserRole.USER,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user
