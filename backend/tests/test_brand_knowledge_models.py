"""Database invariants for account-scoped brand knowledge."""

import pytest
from sqlalchemy.exc import IntegrityError

from app.db import Base


@pytest.mark.asyncio
async def test_account_has_only_one_active_primary_brand_binding(session):
    """Removing the partial unique index would allow conflicting account scopes."""

    binding_table = Base.metadata.tables.get("account_knowledge_bindings")
    assert binding_table is not None

    with pytest.raises(IntegrityError):
        await session.execute(
            binding_table.insert(),
            [
                {
                    "org_id": 1,
                    "account_id": 4,
                    "knowledge_base_id": 10,
                    "binding_type": "primary_brand",
                    "status": "active",
                },
                {
                    "org_id": 1,
                    "account_id": 4,
                    "knowledge_base_id": 11,
                    "binding_type": "primary_brand",
                    "status": "active",
                },
            ],
        )
        await session.commit()


@pytest.mark.asyncio
async def test_knowledge_base_kind_requires_the_matching_client_scope(session):
    """Removing the kind/client check would permit cross-brand knowledge leakage."""

    knowledge_base_table = Base.metadata.tables.get("knowledge_bases")
    assert knowledge_base_table is not None

    with pytest.raises(IntegrityError):
        await session.execute(
            knowledge_base_table.insert(),
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
                    "client_id": 1,
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
