import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.db import Base
from app.models import (
    Account,
    AccountMembership,
    AdminSecurityCredential,
    BrainTask,
    ContentItem,
    KnowledgeEntry,
    LLMCall,
    MatrixDistributionPlan,
    Org,
    User,
)
from app.models.enums import Platform, UserRole


@pytest.fixture
async def account(session, admin) -> Account:
    item = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="Governed account",
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


@pytest.mark.asyncio
async def test_identity_governance_models_persist(session, admin, member, account):
    member.account_scope_mode = "selected"
    membership = AccountMembership(user_id=member.id, account_id=account.id)
    credential = AdminSecurityCredential(user_id=admin.id, password_hash="hash")
    session.add_all([membership, credential])
    await session.commit()

    assert member.account_scope_mode == "selected"
    assert membership.user_id == member.id
    assert membership.account_id == account.id
    assert credential.failed_attempts == 0
    assert credential.changed_at is not None
    assert credential.delete_available_at is not None


def test_creator_foreign_keys_restrict_direct_user_deletion():
    for model in (
        BrainTask,
        ContentItem,
        MatrixDistributionPlan,
        KnowledgeEntry,
        LLMCall,
    ):
        column = model.__table__.c.created_by_id
        foreign_key = next(iter(column.foreign_keys))

        assert column.nullable is True
        assert foreign_key.target_fullname == "users.id"
        assert foreign_key.ondelete == "RESTRICT"


@pytest.mark.asyncio
async def test_account_scope_default_is_actually_persisted(session, admin):
    user = User(
        org_id=admin.org_id,
        email="default-scope@test.com",
        hashed_password=hash_password("default-scope-pw"),
        display_name="Default scope",
        role=UserRole.USER,
    )
    session.add(user)
    await session.commit()
    user_id = user.id
    session.expire_all()

    stored = await session.scalar(select(User).where(User.id == user_id))

    assert stored is not None
    assert stored.account_scope_mode == "all_accessible"


@pytest.mark.asyncio
async def test_real_database_restricts_direct_user_deletion():
    engine = create_async_engine("sqlite+aiosqlite://")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as restricted_session:
        org = Org(name="Restrict org")
        owner = User(
            org=org,
            email="restrict-owner@test.com",
            hashed_password=hash_password("restrict-owner-pw"),
            display_name="Restrict owner",
            role=UserRole.USER,
        )
        restricted_session.add(owner)
        await restricted_session.flush()
        task = BrainTask(org_id=org.id, created_by_id=owner.id, title="Protected root")
        restricted_session.add(task)
        await restricted_session.commit()
        owner_id = owner.id
        task_id = task.id

        await restricted_session.delete(owner)
        with pytest.raises(IntegrityError):
            await restricted_session.commit()
        await restricted_session.rollback()

        assert await restricted_session.get(User, owner_id) is not None
        assert await restricted_session.get(BrainTask, task_id) is not None
    await engine.dispose()
