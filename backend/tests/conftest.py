"""测试夹具：内存 SQLite（async）+ get_session 覆盖 + httpx ASGI 客户端。

让认证/用户接口在无真实 Postgres 时也能端到端测试。
"""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db import Base, get_session
from app.main import app
from app.models import Org, User
from app.models.enums import UserRole


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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
