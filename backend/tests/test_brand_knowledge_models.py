"""Database invariants for account-scoped brand knowledge."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db import Base


async def _seed_binding_scope(session):
    """Create real rows so binding checks exercise relational database behavior."""

    await session.execute(text("PRAGMA foreign_keys = ON"))

    tables = Base.metadata.tables
    await session.execute(tables["orgs"].insert(), {"id": 1, "name": "Knowledge org"})
    await session.execute(
        tables["clients"].insert(),
        [
            {"id": 10, "org_id": 1, "name": "Client A", "status": "active"},
            {"id": 11, "org_id": 1, "name": "Client B", "status": "active"},
        ],
    )
    await session.execute(
        tables["accounts"].insert(),
        {
            "id": 100,
            "org_id": 1,
            "client_id": 10,
            "platform": "wechat_official_account",
            "nickname": "Client A account",
            "status": "active",
        },
    )
    await session.execute(
        tables["knowledge_bases"].insert(),
        [
            {
                "id": 1000,
                "org_id": 1,
                "client_id": 10,
                "kind": "brand",
                "name": "Client A brand",
                "status": "active",
                "version": 1,
            },
            {
                "id": 1001,
                "org_id": 1,
                "client_id": 11,
                "kind": "brand",
                "name": "Client B brand",
                "status": "active",
                "version": 1,
            },
            {
                "id": 1002,
                "org_id": 1,
                "client_id": None,
                "kind": "organization_shared",
                "name": "Shared policy",
                "status": "active",
                "version": 1,
            },
            {
                "id": 1003,
                "org_id": 1,
                "client_id": 10,
                "kind": "brand",
                "name": "Client A second brand",
                "status": "active",
                "version": 1,
            },
        ],
    )
    await session.commit()

    binding_table = tables["account_knowledge_bindings"]
    assert {"knowledge_base_kind", "client_id"} <= set(binding_table.c.keys())
    return binding_table


@pytest.mark.asyncio
async def test_account_has_only_one_active_primary_brand_binding(session):
    """Removing the partial unique index would allow conflicting account scopes."""

    binding_table = await _seed_binding_scope(session)

    with pytest.raises(IntegrityError):
        await session.execute(
            binding_table.insert(),
            [
                {
                    "org_id": 1,
                    "account_id": 100,
                    "knowledge_base_id": 1000,
                    "knowledge_base_kind": "brand",
                    "client_id": 10,
                    "binding_type": "primary_brand",
                    "status": "active",
                },
                {
                    "org_id": 1,
                    "account_id": 100,
                    "knowledge_base_id": 1003,
                    "knowledge_base_kind": "brand",
                    "client_id": 10,
                    "binding_type": "primary_brand",
                    "status": "active",
                },
            ],
        )
        await session.commit()


@pytest.mark.asyncio
async def test_shared_binding_rejects_a_brand_knowledge_base(session):
    """A shared label must not make a brand base readable by another account scope."""

    binding_table = await _seed_binding_scope(session)

    with pytest.raises(IntegrityError):
        await session.execute(
            binding_table.insert(),
            {
                "org_id": 1,
                "account_id": 100,
                "knowledge_base_id": 1000,
                "knowledge_base_kind": "brand",
                "client_id": None,
                "binding_type": "shared",
                "status": "active",
            },
        )
        await session.commit()


@pytest.mark.asyncio
async def test_primary_brand_binding_rejects_an_organization_shared_base(session):
    """The primary brand slot must not point at the organization shared library."""

    binding_table = await _seed_binding_scope(session)

    with pytest.raises(IntegrityError):
        await session.execute(
            binding_table.insert(),
            {
                "org_id": 1,
                "account_id": 100,
                "knowledge_base_id": 1002,
                "knowledge_base_kind": "organization_shared",
                "client_id": None,
                "binding_type": "primary_brand",
                "status": "active",
            },
        )
        await session.commit()


@pytest.mark.asyncio
async def test_primary_brand_binding_rejects_a_different_client_brand_base(session):
    """A raw insert must not attach Client B's brand to Client A's account."""

    binding_table = await _seed_binding_scope(session)

    with pytest.raises(IntegrityError):
        await session.execute(
            binding_table.insert(),
            {
                "org_id": 1,
                "account_id": 100,
                "knowledge_base_id": 1001,
                "knowledge_base_kind": "brand",
                "client_id": 11,
                "binding_type": "primary_brand",
                "status": "active",
            },
        )
        await session.commit()


@pytest.mark.asyncio
async def test_knowledge_base_kind_requires_the_matching_client_scope(session):
    """Removing the kind/client check would permit cross-brand knowledge leakage."""

    await session.execute(text("PRAGMA foreign_keys = ON"))
    tables = Base.metadata.tables
    await session.execute(tables["orgs"].insert(), {"id": 1, "name": "Knowledge org"})
    await session.execute(
        tables["clients"].insert(),
        {"id": 10, "org_id": 1, "name": "Client A", "status": "active"},
    )
    await session.commit()

    with pytest.raises(IntegrityError):
        await session.execute(
            tables["knowledge_bases"].insert(),
            [
                {
                    "org_id": 1,
                    "client_id": None,
                    "kind": "brand",
                    "name": "Invalid brand base",
                    "status": "active",
                    "version": 1,
                },
                {
                    "org_id": 1,
                    "client_id": 10,
                    "kind": "organization_shared",
                    "name": "Invalid shared base",
                    "status": "active",
                    "version": 1,
                },
            ],
        )
        await session.commit()


def test_knowledge_entries_accept_legacy_rows_without_a_knowledge_base():
    """Removing nullable scope fields would make existing knowledge entries unreadable."""

    entry_columns = Base.metadata.tables["knowledge_entries"].c
    assert "knowledge_base_id" in entry_columns
    assert entry_columns.knowledge_base_id.nullable is True
    assert "entry_kind" in entry_columns
    assert "verification_status" in entry_columns
    assert "source_attachment_id" in entry_columns
    assert "effective_at" in entry_columns
    assert "expires_at" in entry_columns
    assert "allowed_for_external_claim" in entry_columns


@pytest.mark.asyncio
async def test_knowledge_citations_allow_unknown_legacy_snapshots_and_index_exact_versions(session):
    """Removing snapshots would make later fact gates read mutable source state."""

    citation_table = Base.metadata.tables["knowledge_citations"]
    expected_columns = {
        "entry_version",
        "source_type",
        "source_label",
        "source_url",
        "verification_status",
        "allowed_for_external_claim",
    }
    assert expected_columns <= set(citation_table.c.keys())
    assert all(citation_table.c[name].nullable for name in expected_columns)
    assert any(
        tuple(column.name for column in index.columns) == ("entry_id", "entry_version")
        and not index.unique
        for index in citation_table.indexes
    )
    await session.execute(
        citation_table.insert(),
        {
            "org_id": 1,
            "client_id": 1,
            "entry_id": 1,
            "agent_code": "legacy-agent",
            "context": "legacy citation without snapshots",
        },
    )
    await session.commit()
    legacy = (await session.execute(citation_table.select())).mappings().one()
    assert {name: legacy[name] for name in expected_columns} == {
        "entry_version": None,
        "source_type": None,
        "source_label": None,
        "source_url": None,
        "verification_status": None,
        "allowed_for_external_claim": None,
    }
