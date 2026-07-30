import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models import ModelConfig, Org
from app.services.model_infrastructure import resolve_route_targets


@pytest.mark.asyncio
async def test_router_without_own_config_reuses_decision_model_config(session) -> None:
    org = Org(name="Router fallback")
    session.add(org)
    await session.flush()
    session.add(
        ModelConfig(
            org_id=org.id,
            agent_code="00-decision",
            primary_model="deepseek-reasoner",
            fallback_model="deepseek-chat",
        )
    )
    await session.commit()

    primary, fallback, options = await resolve_route_targets(session, org.id, "00-router")

    assert primary.model == "deepseek-reasoner"
    assert fallback is not None
    assert fallback.model == "deepseek-chat"
    assert options == {"temperature": 0.4, "max_tokens": 4096, "timeout_seconds": 90}


def test_router_profile_migration_copies_decision_provider_and_fallback_idempotently(
    monkeypatch,
) -> None:
    migration = importlib.import_module(
        "migrations.versions.20260730_0100_main_agent_router_profile"
    )
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    orgs = sa.Table("orgs", metadata, sa.Column("id", sa.Integer, primary_key=True))
    configs = sa.Table(
        "model_configs",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False),
        sa.Column("agent_code", sa.String(64), nullable=False),
        sa.Column("primary_provider_id", sa.Integer),
        sa.Column("fallback_provider_id", sa.Integer),
        sa.Column("primary_model", sa.String(128), nullable=False),
        sa.Column("fallback_model", sa.String(128)),
        sa.Column("params", sa.JSON),
        sa.UniqueConstraint("org_id", "agent_code", name="uq_model_config_agent"),
    )

    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(orgs.insert(), [{"id": 1}, {"id": 2}])
        connection.execute(
            configs.insert(),
            [
                {
                    "org_id": 1,
                    "agent_code": "00-decision",
                    "primary_provider_id": 11,
                    "fallback_provider_id": 12,
                    "primary_model": "gpt-4.1",
                    "fallback_model": "gpt-4.1-mini",
                    "params": {"routing_config": {"temperature": 0.2}},
                },
                {
                    "org_id": 2,
                    "agent_code": "00-decision",
                    "primary_provider_id": None,
                    "fallback_provider_id": None,
                    "primary_model": "deepseek-chat",
                    "fallback_model": None,
                    "params": None,
                },
            ],
        )
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(migration, "op", operations)

        migration.upgrade()
        migration.upgrade()

        rows = connection.execute(
            sa.select(configs)
            .where(configs.c.agent_code == "00-router")
            .order_by(configs.c.org_id)
        ).mappings().all()

    assert [dict(row) for row in rows] == [
        {
            "id": 3,
            "org_id": 1,
            "agent_code": "00-router",
            "primary_provider_id": 11,
            "fallback_provider_id": 12,
            "primary_model": "deepseek-v4-flash",
            "fallback_model": "gpt-4.1-mini",
            "params": {"routing_config": {"temperature": 0.2}},
        },
        {
            "id": 4,
            "org_id": 2,
            "agent_code": "00-router",
            "primary_provider_id": None,
            "fallback_provider_id": None,
            "primary_model": "deepseek-v4-flash",
            "fallback_model": None,
            "params": None,
        },
    ]
