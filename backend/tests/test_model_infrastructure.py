import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models import ModelConfig, ModelProvider, Org
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


@pytest.mark.asyncio
async def test_sparse_router_profile_overlays_provider_fallback_and_route_options(session) -> None:
    org = Org(name="Router overlay")
    session.add(org)
    await session.flush()
    primary_provider = ModelProvider(
        org_id=org.id,
        code="primary",
        display_name="Primary",
        provider_type="custom_openai",
        template_code=None,
        protocol="openai_compatible",
        base_url="https://primary.example.com/v1",
        enabled=True,
        credential_source="encrypted",
        verification_status="verified",
        models=["deepseek-v4-flash"],
    )
    fallback_provider = ModelProvider(
        org_id=org.id,
        code="fallback",
        display_name="Fallback",
        provider_type="custom_openai",
        template_code=None,
        protocol="openai_compatible",
        base_url="https://fallback.example.com/v1",
        enabled=True,
        credential_source="encrypted",
        verification_status="verified",
        models=["gpt-4.1-mini"],
    )
    session.add_all([primary_provider, fallback_provider])
    await session.flush()
    session.add_all(
        [
            ModelConfig(
                org_id=org.id,
                agent_code="00-decision",
                primary_provider_id=primary_provider.id,
                fallback_provider_id=fallback_provider.id,
                primary_model="gpt-4.1",
                fallback_model="gpt-4.1-mini",
                params={
                    "routing_config": {
                        "temperature": 0.1,
                        "max_tokens": 2048,
                        "timeout_seconds": 45,
                    }
                },
            ),
            ModelConfig(
                org_id=org.id,
                agent_code="00-router",
                primary_model="deepseek-v4-flash",
                params={"routing_config": {"temperature": 0.3}},
            ),
        ]
    )
    await session.commit()

    primary, fallback, options = await resolve_route_targets(session, org.id, "00-router")

    assert primary.provider_id == primary_provider.id
    assert primary.provider_code == "primary"
    assert primary.model == "deepseek-v4-flash"
    assert fallback is not None
    assert fallback.provider_id == fallback_provider.id
    assert fallback.provider_code == "fallback"
    assert fallback.model == "gpt-4.1-mini"
    assert options == {"temperature": 0.3, "max_tokens": 2048, "timeout_seconds": 45}


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
                {
                    "org_id": 2,
                    "agent_code": "00-router",
                    "primary_provider_id": 22,
                    "fallback_provider_id": 23,
                    "primary_model": "manual-router",
                    "fallback_model": "manual-fallback",
                    "params": {"routing_config": {"max_tokens": 512}},
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
        migration.downgrade()
        rows_after_downgrade = connection.execute(
            sa.select(configs)
            .where(configs.c.agent_code == "00-router")
            .order_by(configs.c.org_id)
        ).mappings().all()

    assert [dict(row) for row in rows] == [
        {
            "id": 4,
            "org_id": 1,
            "agent_code": "00-router",
            "primary_provider_id": 11,
            "fallback_provider_id": 12,
            "primary_model": "deepseek-v4-flash",
            "fallback_model": "gpt-4.1-mini",
            "params": {"routing_config": {"temperature": 0.2}},
        },
        {
            "id": 3,
            "org_id": 2,
            "agent_code": "00-router",
            "primary_provider_id": 22,
            "fallback_provider_id": 23,
            "primary_model": "manual-router",
            "fallback_model": "manual-fallback",
            "params": {"routing_config": {"max_tokens": 512}},
        },
    ]
    assert [dict(row) for row in rows_after_downgrade] == [dict(row) for row in rows]
