import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import models as app_models
from app.db import Base
from app.models import ModelConfig, Org, User
from app.models.enums import UserRole


def _model_provider_type():
    model = getattr(app_models, "ModelProvider", None)
    assert model is not None
    return model


@pytest.mark.asyncio
async def test_model_provider_persists_defaults(session, admin):
    model_provider = _model_provider_type()
    provider = model_provider(
        org_id=admin.org_id,
        code="deepseek",
        display_name="DeepSeek",
        provider_type="preset",
        protocol="openai_compatible",
        base_url="https://api.deepseek.com",
        credential_source="environment",
        created_by_id=admin.id,
        updated_by_id=admin.id,
    )
    session.add(provider)
    await session.commit()
    provider_id = provider.id
    session.expire_all()

    stored = await session.get(model_provider, provider_id)

    assert stored is not None
    assert stored.enabled is True
    assert stored.sort_order == 0
    assert stored.verification_status == "pending"
    assert stored.template_code is None
    assert stored.models is None


@pytest.mark.asyncio
async def test_model_provider_code_is_unique_within_org(session, admin):
    model_provider = _model_provider_type()

    def provider(display_name: str):
        return model_provider(
            org_id=admin.org_id,
            code="deepseek",
            display_name=display_name,
            provider_type="preset",
            protocol="openai_compatible",
            credential_source="environment",
            created_by_id=admin.id,
            updated_by_id=admin.id,
        )

    session.add(provider("DeepSeek"))
    await session.commit()
    session.add(provider("Duplicate DeepSeek"))

    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_model_provider_code_can_repeat_across_orgs(session, admin):
    model_provider = _model_provider_type()
    other_org = Org(name="Other org")
    other_admin = User(
        org=other_org,
        email="other-admin@test.com",
        hashed_password="x",
        display_name="Other admin",
        role=UserRole.ADMIN,
    )
    session.add(other_admin)
    await session.flush()
    session.add_all(
        [
            model_provider(
                org_id=admin.org_id,
                code="deepseek",
                display_name="DeepSeek",
                provider_type="preset",
                protocol="openai_compatible",
                credential_source="environment",
                created_by_id=admin.id,
                updated_by_id=admin.id,
            ),
            model_provider(
                org_id=other_org.id,
                code="deepseek",
                display_name="DeepSeek",
                provider_type="preset",
                protocol="openai_compatible",
                credential_source="environment",
                created_by_id=other_admin.id,
                updated_by_id=other_admin.id,
            ),
        ]
    )

    await session.commit()

    assert len((await session.scalars(select(model_provider))).all()) == 2


def test_model_routes_have_nullable_restricted_provider_foreign_keys():
    for column_name in ("primary_provider_id", "fallback_provider_id"):
        column = ModelConfig.__table__.c.get(column_name)
        assert column is not None
        assert column.nullable is True
        foreign_key = next(iter(column.foreign_keys))
        assert foreign_key.target_fullname == "model_providers.id"
        assert foreign_key.ondelete == "RESTRICT"


@pytest.mark.asyncio
async def test_database_restricts_deleting_a_routed_provider():
    model_provider = _model_provider_type()
    engine = create_async_engine("sqlite+aiosqlite://")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as restricted_session:
        org = Org(name="Route org")
        admin = User(
            org=org,
            email="route-admin@test.com",
            hashed_password="x",
            display_name="Route admin",
            role=UserRole.ADMIN,
        )
        restricted_session.add(admin)
        await restricted_session.flush()
        provider = model_provider(
            org_id=org.id,
            code="deepseek",
            display_name="DeepSeek",
            provider_type="preset",
            protocol="openai_compatible",
            credential_source="environment",
            created_by_id=admin.id,
            updated_by_id=admin.id,
        )
        restricted_session.add(provider)
        await restricted_session.flush()
        route = ModelConfig(
            org_id=org.id,
            agent_code="positioning",
            primary_provider_id=provider.id,
            primary_model="deepseek-chat",
        )
        restricted_session.add(route)
        await restricted_session.commit()
        provider_id = provider.id

        await restricted_session.delete(provider)
        with pytest.raises(IntegrityError):
            await restricted_session.commit()
        await restricted_session.rollback()

        assert await restricted_session.get(model_provider, provider_id) is not None
    await engine.dispose()


@pytest.mark.asyncio
async def test_database_rejects_cross_organization_provider_route():
    model_provider = _model_provider_type()
    engine = create_async_engine("sqlite+aiosqlite://")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as isolated_session:
        first_org = Org(name="First org")
        second_org = Org(name="Second org")
        first_admin = User(
            org=first_org,
            email="first-admin@test.com",
            hashed_password="x",
            display_name="First admin",
            role=UserRole.ADMIN,
        )
        second_admin = User(
            org=second_org,
            email="second-admin@test.com",
            hashed_password="x",
            display_name="Second admin",
            role=UserRole.ADMIN,
        )
        isolated_session.add_all([first_admin, second_admin])
        await isolated_session.flush()
        provider = model_provider(
            org_id=second_org.id,
            code="deepseek",
            display_name="DeepSeek",
            provider_type="preset",
            protocol="openai_compatible",
            credential_source="environment",
            created_by_id=second_admin.id,
            updated_by_id=second_admin.id,
        )
        isolated_session.add(provider)
        await isolated_session.flush()
        isolated_session.add(
            ModelConfig(
                org_id=first_org.id,
                agent_code="positioning",
                primary_provider_id=provider.id,
                primary_model="deepseek-chat",
            )
        )

        with pytest.raises(IntegrityError):
            await isolated_session.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_deleting_provider_actor_preserves_provider_and_clears_attribution():
    model_provider = _model_provider_type()
    engine = create_async_engine("sqlite+aiosqlite://")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as isolated_session:
        org = Org(name="Attribution org")
        actor = User(
            org=org,
            email="provider-actor@test.com",
            hashed_password="x",
            display_name="Provider actor",
            role=UserRole.USER,
        )
        isolated_session.add(actor)
        await isolated_session.flush()
        provider = model_provider(
            org_id=org.id,
            code="deepseek",
            display_name="DeepSeek",
            provider_type="preset",
            protocol="openai_compatible",
            credential_source="environment",
            created_by_id=actor.id,
            updated_by_id=actor.id,
        )
        isolated_session.add(provider)
        await isolated_session.commit()
        provider_id = provider.id

        await isolated_session.delete(actor)
        await isolated_session.commit()
        isolated_session.expire_all()

        stored = await isolated_session.get(model_provider, provider_id)
        assert stored is not None
        assert stored.created_by_id is None
        assert stored.updated_by_id is None

    await engine.dispose()


def test_migration_backfills_compatibility_providers_without_rewriting_models(monkeypatch):
    migration = importlib.import_module("migrations.versions.20260720_0400_model_provider_registry")
    engine = sa.create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    metadata = sa.MetaData()
    orgs = sa.Table(
        "orgs",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
    )
    users = sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, sa.ForeignKey("orgs.id"), nullable=False),
    )
    model_configs = sa.Table(
        "model_configs",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("agent_code", sa.String(64), nullable=False),
        sa.Column("primary_model", sa.String(128), nullable=False),
        sa.Column("fallback_model", sa.String(128)),
    )

    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(
            orgs.insert(),
            [
                {"id": 1, "name": "DeepSeek route"},
                {"id": 2, "name": "LiteLLM route"},
                {"id": 3, "name": "No routes"},
            ],
        )
        connection.execute(
            users.insert(),
            [
                {"id": 11, "org_id": 1},
                {"id": 22, "org_id": 2},
            ],
        )
        connection.execute(
            model_configs.insert(),
            [
                {
                    "id": 101,
                    "org_id": 1,
                    "agent_code": "writer",
                    "primary_model": "deepseek-chat",
                    "fallback_model": "litellm:openai/gpt-4.1",
                },
                {
                    "id": 202,
                    "org_id": 2,
                    "agent_code": "reviewer",
                    "primary_model": "litellm:anthropic/claude-sonnet-4",
                    "fallback_model": "deepseek-reasoner",
                },
            ],
        )
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(migration, "op", operations)

        migration.upgrade()

        providers = (
            connection.execute(
                sa.text(
                    "SELECT id, org_id, code, enabled, credential_source, "
                    "created_by_id, updated_by_id "
                    "FROM model_providers ORDER BY org_id, code"
                )
            )
            .mappings()
            .all()
        )
        routes = (
            connection.execute(
                sa.text(
                    "SELECT mc.id, mc.primary_model, mc.fallback_model, "
                    "primary_provider.code AS primary_provider, "
                    "fallback_provider.code AS fallback_provider "
                    "FROM model_configs AS mc "
                    "LEFT JOIN model_providers AS primary_provider "
                    "ON primary_provider.id = mc.primary_provider_id "
                    "LEFT JOIN model_providers AS fallback_provider "
                    "ON fallback_provider.id = mc.fallback_provider_id "
                    "ORDER BY mc.id"
                )
            )
            .mappings()
            .all()
        )
        migration.downgrade()
        inspector = sa.inspect(connection)
        downgraded_columns = {column["name"] for column in inspector.get_columns("model_configs")}
        provider_table_removed = not inspector.has_table("model_providers")

    assert [(row["org_id"], row["code"]) for row in providers] == [
        (1, "deepseek"),
        (1, "legacy-litellm"),
        (2, "deepseek"),
        (2, "legacy-litellm"),
        (3, "deepseek"),
    ]
    legacy_rows = [row for row in providers if row["code"] == "legacy-litellm"]
    assert all(row["enabled"] == 0 for row in legacy_rows)
    assert all(row["credential_source"] == "none" for row in legacy_rows)
    userless_provider = next(row for row in providers if row["org_id"] == 3)
    assert userless_provider["created_by_id"] is None
    assert userless_provider["updated_by_id"] is None
    assert [dict(row) for row in routes] == [
        {
            "id": 101,
            "primary_model": "deepseek-chat",
            "fallback_model": "litellm:openai/gpt-4.1",
            "primary_provider": "deepseek",
            "fallback_provider": "legacy-litellm",
        },
        {
            "id": 202,
            "primary_model": "litellm:anthropic/claude-sonnet-4",
            "fallback_model": "deepseek-reasoner",
            "primary_provider": "legacy-litellm",
            "fallback_provider": "deepseek",
        },
    ]
    assert "primary_provider_id" not in downgraded_columns
    assert "fallback_provider_id" not in downgraded_columns
    assert provider_table_removed is True
