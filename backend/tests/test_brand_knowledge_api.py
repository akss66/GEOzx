"""Brand knowledge APIs keep bases, entries, and account bindings in one scope."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

import app.api.knowledge as knowledge_api
from app.core.security import hash_password
from app.models import (
    Account,
    AccountKnowledgeBinding,
    Client,
    ClientMembership,
    KnowledgeBase,
    KnowledgeCitation,
    KnowledgeEntry,
    Org,
    Project,
    User,
)
from app.models.enums import KnowledgeCategory, Platform, UserRole, WorkspaceRole
from app.services.knowledge_workspace import list_agent_knowledge_for_account


async def _token(client, email: str, password: str) -> dict[str, str]:
    response = await client.post("/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _member(
    session, admin, *, email: str, role: WorkspaceRole
) -> tuple[User, Client, Account]:
    user = User(
        org_id=admin.org_id,
        email=email,
        hashed_password=hash_password("member-pw-123"),
        display_name=email,
        role=UserRole.USER,
    )
    workspace = Client(org_id=admin.org_id, name=f"{email} client")
    account = Account(
        org_id=admin.org_id,
        client=workspace,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname=f"{email} account",
    )
    session.add_all([user, workspace, account])
    await session.flush()
    session.add(ClientMembership(client_id=workspace.id, user_id=user.id, role=role))
    await session.commit()
    return user, workspace, account


async def _brand_base(
    session, *, org_id: int, client_id: int, name: str, creator_id: int
) -> KnowledgeBase:
    base = KnowledgeBase(
        org_id=org_id,
        client_id=client_id,
        kind="brand",
        name=name,
        status="active",
        version=1,
        created_by_id=creator_id,
    )
    session.add(base)
    await session.commit()
    return base


@pytest.mark.asyncio
async def test_knowledge_base_crud_and_entry_lists_are_paginated(client, session, admin):
    """Removing the new base routes would make brand knowledge inaccessible."""

    lead, workspace, _account = await _member(
        session, admin, email="lead@test.com", role=WorkspaceRole.LEAD
    )
    headers = await _token(client, lead.email, "member-pw-123")

    created = await client.post(
        "/knowledge-bases",
        headers=headers,
        json={"kind": "brand", "client_id": workspace.id, "name": "Launch brand"},
    )
    assert created.status_code == 201
    base = created.json()
    assert base["kind"] == "brand"
    assert base["client_id"] == workspace.id

    listed = await client.get("/knowledge-bases?limit=1&offset=0", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["data"] == [base]
    assert listed.json()["pagination"] == {"limit": 1, "offset": 0, "total": 1}

    updated = await client.patch(
        f"/knowledge-bases/{base['id']}", headers=headers, json={"name": "Refined brand"}
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Refined brand"
    assert updated.json()["version"] == 2

    entry = await client.post(
        f"/knowledge-bases/{base['id']}/entries",
        headers=headers,
        json={
            "title": "Warranty",
            "content": "Warranty duration is ten years.",
            "category": "prompt_library",
            "source_label": "Warranty policy",
            "entry_kind": "product_fact",
            "payload": {
                "schema_version": 1,
                "kind": "product_fact",
                "product_code": "YH-001",
                "fact_key": "warranty_years",
                "value": 10,
                "unit": "year",
                "claim_text": "10 year warranty",
                "allowed_for_external_claim": True,
            },
        },
    )
    assert entry.status_code == 201

    entries = await client.get(
        f"/knowledge-bases/{base['id']}/entries?limit=1&offset=0", headers=headers
    )
    assert entries.status_code == 200
    assert entries.json()["data"][0]["id"] == entry.json()["id"]
    assert entries.json()["pagination"] == {"limit": 1, "offset": 0, "total": 1}

    archived_entry = await client.delete(
        f"/knowledge-bases/{base['id']}/entries/{entry.json()['id']}", headers=headers
    )
    assert archived_entry.status_code == 204
    empty_entries = await client.get(
        f"/knowledge-bases/{base['id']}/entries?limit=1&offset=0", headers=headers
    )
    assert empty_entries.json() == {
        "data": [],
        "pagination": {"limit": 1, "offset": 0, "total": 0},
    }

    archived = await client.delete(f"/knowledge-bases/{base['id']}", headers=headers)
    assert archived.status_code == 204
    assert (await session.get(KnowledgeBase, base["id"])).status == "archived"


@pytest.mark.asyncio
async def test_admin_creates_shared_entry_with_null_client_and_agents_retrieve_it(
    client, session, admin
):
    """Shared knowledge must use its real null-client base shape without bypassing bindings."""

    lead, workspace, account = await _member(
        session, admin, email="shared-entry-lead@test.com", role=WorkspaceRole.LEAD
    )
    project = Project(org_id=admin.org_id, client_id=workspace.id, name="Shared entry project")
    shared = KnowledgeBase(
        org_id=admin.org_id,
        client_id=None,
        kind="organization_shared",
        name="Shared entry base",
        status="active",
        version=1,
        created_by_id=admin.id,
    )
    session.add_all([project, shared])
    await session.flush()
    session.add(
        AccountKnowledgeBinding(
            org_id=admin.org_id,
            account_id=account.id,
            knowledge_base_id=shared.id,
            knowledge_base_kind="organization_shared",
            client_id=None,
            binding_type="shared",
            status="active",
            bound_by_id=admin.id,
        )
    )
    await session.commit()
    headers = await _token(client, admin.email, "admin-pw-123")

    created = await client.post(
        f"/knowledge-bases/{shared.id}/entries",
        headers=headers,
        json={
            "title": "Shared verified policy",
            "content": "Use the organization disclosure language.",
            "category": "prompt_library",
            "source_label": "Organization policy",
            "entry_kind": "policy",
            "payload": {"schema_version": 1, "kind": "policy"},
        },
    )

    assert created.status_code == 201
    entry_id = created.json()["id"]
    entry = await session.get(KnowledgeEntry, entry_id)
    assert entry is not None
    assert entry.client_id is None
    assert entry.project_id is None
    assert entry.knowledge_base_kind == "organization_shared"
    assert (
        await client.patch(
            f"/knowledge/{entry_id}",
            headers=headers,
            json={"title": "Wrong local route"},
        )
    ).status_code == 404
    verified = await client.patch(
        f"/knowledge-bases/{shared.id}/entries/{entry_id}",
        headers=headers,
        json={"verification_status": "verified"},
    )
    assert verified.status_code == 200

    rows = await list_agent_knowledge_for_account(
        session,
        org_id=admin.org_id,
        account_id=account.id,
        project_id=project.id,
        limit=24,
    )
    assert [row.id for row in rows] == [entry_id]


@pytest.mark.asyncio
async def test_product_fact_payload_rejects_malformed_values_at_the_boundary(
    client, session, admin
):
    """Untyped facts could otherwise turn malformed claims into publishable knowledge."""

    lead, workspace, _account = await _member(
        session, admin, email="facts-lead@test.com", role=WorkspaceRole.LEAD
    )
    base = await _brand_base(
        session, org_id=admin.org_id, client_id=workspace.id, name="Facts", creator_id=lead.id
    )
    headers = await _token(client, lead.email, "member-pw-123")

    response = await client.post(
        f"/knowledge-bases/{base.id}/entries",
        headers=headers,
        json={
            "title": "Invalid warranty",
            "content": "No structured evidence.",
            "category": "prompt_library",
            "source_label": "Unverified note",
            "entry_kind": "product_fact",
            "payload": {
                "schema_version": 1,
                "kind": "product_fact",
                "product_code": "YH-001",
                "fact_key": "warranty_years",
                "value": "ten",
                "claim_text": "Ten year warranty",
            },
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_legacy_entry_update_cannot_bypass_product_fact_validation(client, session, admin):
    """The older generic route must not turn a typed fact back into untyped JSON."""

    lead, workspace, _account = await _member(
        session, admin, email="legacy-facts-lead@test.com", role=WorkspaceRole.LEAD
    )
    entry = KnowledgeEntry(
        org_id=admin.org_id,
        client_id=workspace.id,
        category=KnowledgeCategory.PROMPT_LIBRARY,
        title="Warranty",
        content="Ten year warranty.",
        payload={
            "schema_version": 1,
            "kind": "product_fact",
            "product_code": "YH-001",
            "fact_key": "warranty_years",
            "value": 10,
            "claim_text": "10 year warranty",
        },
        source_type="manual",
        source_label="Policy",
        entry_kind="product_fact",
        verification_status="draft",
        version=1,
        created_by_id=lead.id,
    )
    session.add(entry)
    await session.commit()

    response = await client.patch(
        f"/knowledge/{entry.id}",
        headers=await _token(client, lead.email, "member-pw-123"),
        json={"payload": {"kind": "product_fact", "value": "ten"}},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_entry_role_matrix_separates_write_and_fact_review(client, session, admin):
    """Changing role guards would let reviewers write or operators verify facts."""

    lead, workspace, _account = await _member(
        session, admin, email="matrix-lead@test.com", role=WorkspaceRole.LEAD
    )
    operator, _operator_workspace, _operator_account = await _member(
        session, admin, email="matrix-operator@test.com", role=WorkspaceRole.OPERATOR
    )
    editor, _editor_workspace, _editor_account = await _member(
        session, admin, email="matrix-editor@test.com", role=WorkspaceRole.EDITOR
    )
    reviewer, _reviewer_workspace, _reviewer_account = await _member(
        session, admin, email="matrix-reviewer@test.com", role=WorkspaceRole.REVIEWER
    )
    for user in (operator, editor, reviewer):
        session.add(ClientMembership(client_id=workspace.id, user_id=user.id, role={
            operator.id: WorkspaceRole.OPERATOR,
            editor.id: WorkspaceRole.EDITOR,
            reviewer.id: WorkspaceRole.REVIEWER,
        }[user.id]))
    base = await _brand_base(
        session, org_id=admin.org_id, client_id=workspace.id, name="Matrix", creator_id=lead.id
    )
    await session.commit()

    entry_body = {
        "title": "Policy",
        "content": "Always disclose limitations.",
        "category": "prompt_library",
        "source_label": "Policy owner",
        "entry_kind": "policy",
        "payload": {"schema_version": 1, "kind": "policy"},
    }
    created_ids: list[int] = []
    for user in (lead, operator, editor):
        response = await client.post(
            f"/knowledge-bases/{base.id}/entries",
            headers=await _token(client, user.email, "member-pw-123"),
            json=entry_body,
        )
        assert response.status_code == 201
        created_ids.append(response.json()["id"])
    denied = await client.post(
        f"/knowledge-bases/{base.id}/entries",
        headers=await _token(client, reviewer.email, "member-pw-123"),
        json=entry_body,
    )
    assert denied.status_code == 403

    mixed_review = await client.patch(
        f"/knowledge-bases/{base.id}/entries/{created_ids[0]}",
        headers=await _token(client, reviewer.email, "member-pw-123"),
        json={"verification_status": "verified", "content": "Reviewer tampering"},
    )
    assert mixed_review.status_code == 422
    unchanged = await session.get(KnowledgeEntry, created_ids[0])
    assert unchanged is not None
    assert unchanged.content == entry_body["content"]
    assert unchanged.payload == entry_body["payload"]

    operator_review = await client.patch(
        f"/knowledge-bases/{base.id}/entries/{created_ids[0]}",
        headers=await _token(client, operator.email, "member-pw-123"),
        json={"verification_status": "verified"},
    )
    assert operator_review.status_code == 403
    reviewer_review = await client.patch(
        f"/knowledge-bases/{base.id}/entries/{created_ids[0]}",
        headers=await _token(client, reviewer.email, "member-pw-123"),
        json={"verification_status": "verified"},
    )
    assert reviewer_review.status_code == 200
    assert reviewer_review.json()["verification_status"] == "verified"


@pytest.mark.asyncio
async def test_product_fact_claim_flag_is_derived_only_from_its_payload(client, session, admin):
    """A second top-level flag must not contradict the reviewed fact payload."""

    lead, workspace, _account = await _member(
        session, admin, email="claim-lead@test.com", role=WorkspaceRole.LEAD
    )
    base = await _brand_base(
        session, org_id=admin.org_id, client_id=workspace.id, name="Claims", creator_id=lead.id
    )
    headers = await _token(client, lead.email, "member-pw-123")
    created = await client.post(
        f"/knowledge-bases/{base.id}/entries",
        headers=headers,
        json={
            "title": "Warranty",
            "content": "Ten years.",
            "category": "prompt_library",
            "source_label": "Policy",
            "entry_kind": "product_fact",
            "payload": {
                "schema_version": 1,
                "kind": "product_fact",
                "product_code": "YH-001",
                "fact_key": "warranty_years",
                "value": 10,
                "claim_text": "10 year warranty",
                "allowed_for_external_claim": False,
            },
        },
    )
    assert created.status_code == 201
    entry_id = created.json()["id"]

    payload_only = await client.patch(
        f"/knowledge-bases/{base.id}/entries/{entry_id}",
        headers=headers,
        json={
            "payload": {
                "schema_version": 1,
                "kind": "product_fact",
                "product_code": "YH-001",
                "fact_key": "warranty_years",
                "value": 10,
                "claim_text": "10 year warranty",
                "allowed_for_external_claim": True,
            }
        },
    )
    assert payload_only.status_code == 200
    assert payload_only.json()["payload"]["allowed_for_external_claim"] is True
    assert payload_only.json()["allowed_for_external_claim"] is True

    contradiction = await client.patch(
        f"/knowledge-bases/{base.id}/entries/{entry_id}",
        headers=headers,
        json={
            "payload": {
                "schema_version": 1,
                "kind": "product_fact",
                "product_code": "YH-001",
                "fact_key": "warranty_years",
                "value": 10,
                "claim_text": "10 year warranty",
                "allowed_for_external_claim": True,
            },
            "allowed_for_external_claim": False,
        },
    )
    assert contradiction.status_code == 422
    unchanged = await session.get(KnowledgeEntry, entry_id)
    assert unchanged is not None
    assert unchanged.allowed_for_external_claim is True
    assert unchanged.payload["allowed_for_external_claim"] is True

    policy = await client.post(
        f"/knowledge-bases/{base.id}/entries",
        headers=headers,
        json={
            "title": "Policy",
            "content": "No external promise.",
            "category": "prompt_library",
            "source_label": "Policy",
            "entry_kind": "policy",
            "payload": {"schema_version": 1, "kind": "policy"},
        },
    )
    assert policy.status_code == 201
    non_product_flag = await client.patch(
        f"/knowledge-bases/{base.id}/entries/{policy.json()['id']}",
        headers=headers,
        json={"allowed_for_external_claim": True},
    )
    assert non_product_flag.status_code == 422


@pytest.mark.asyncio
async def test_binding_returns_conflict_after_a_partial_unique_failure(
    client, session, admin, monkeypatch
):
    """A remaining uniqueness race must roll back and surface a stable 409, never a 500."""

    lead, workspace, account = await _member(
        session, admin, email="conflict-lead@test.com", role=WorkspaceRole.LEAD
    )
    base = await _brand_base(
        session, org_id=admin.org_id, client_id=workspace.id, name="Conflict", creator_id=lead.id
    )
    account_id = account.id
    headers = await _token(client, lead.email, "member-pw-123")

    async def raise_partial_unique(*_args, **_kwargs):
        raise IntegrityError(
            "insert", {}, Exception("uq_account_knowledge_bindings_active_primary_brand")
        )

    monkeypatch.setattr(knowledge_api, "bind_account_primary_knowledge_base", raise_partial_unique)
    response = await client.put(
        f"/accounts/{account_id}/knowledge-binding",
        headers=headers,
        json={"knowledge_base_id": base.id},
    )
    assert response.status_code == 409
    assert await session.scalar(
        select(func.count()).select_from(AccountKnowledgeBinding).where(
            AccountKnowledgeBinding.account_id == account_id,
            AccountKnowledgeBinding.status == "active",
        )
    ) == 0


@pytest.mark.asyncio
async def test_binding_fails_closed_then_rebinds_and_unbinds_without_removing_citations(
    client, session, admin
):
    """Binding scope and lifecycle must not expose other brands or erase history."""

    lead, workspace, account = await _member(
        session, admin, email="binding-lead@test.com", role=WorkspaceRole.LEAD
    )
    first = await _brand_base(
        session, org_id=admin.org_id, client_id=workspace.id, name="First", creator_id=lead.id
    )
    second = await _brand_base(
        session, org_id=admin.org_id, client_id=workspace.id, name="Second", creator_id=lead.id
    )
    other_client = Client(org_id=admin.org_id, name="Other client")
    session.add(other_client)
    await session.flush()
    other_client_base = await _brand_base(
        session,
        org_id=admin.org_id,
        client_id=other_client.id,
        name="Other client base",
        creator_id=lead.id,
    )
    other_account = Account(
        org_id=admin.org_id,
        client=other_client,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="Other client account",
    )
    session.add(other_account)
    foreign_org = Org(name="Foreign organization")
    session.add(foreign_org)
    await session.flush()
    foreign_client = Client(org_id=foreign_org.id, name="Foreign client")
    session.add(foreign_client)
    await session.flush()
    foreign_base = await _brand_base(
        session,
        org_id=foreign_org.id,
        client_id=foreign_client.id,
        name="Foreign base",
        creator_id=None,
    )
    entry = KnowledgeEntry(
        org_id=admin.org_id,
        client_id=workspace.id,
        category=KnowledgeCategory.PROMPT_LIBRARY,
        title="Cited policy",
        content="A preserved source.",
        payload={},
        source_type="manual",
        source_label="Policy",
        created_by_id=lead.id,
        knowledge_base_id=first.id,
        knowledge_base_kind="brand",
        entry_kind="policy",
        verification_status="verified",
    )
    session.add(entry)
    await session.flush()
    session.add(
        KnowledgeCitation(
            org_id=admin.org_id,
            client_id=workspace.id,
            entry_id=entry.id,
            agent_code="06-operator",
            context="Published source",
        )
    )
    await session.commit()
    headers = await _token(client, lead.email, "member-pw-123")

    for invalid_base in (other_client_base, foreign_base):
        rejected = await client.put(
            f"/accounts/{account.id}/knowledge-binding",
            headers=headers,
            json={"knowledge_base_id": invalid_base.id},
        )
        assert rejected.status_code == 404

    inaccessible_account = await client.put(
        f"/accounts/{other_account.id}/knowledge-binding",
        headers=headers,
        json={"knowledge_base_id": first.id},
    )
    assert inaccessible_account.status_code == 404

    initial = await client.put(
        f"/accounts/{account.id}/knowledge-binding",
        headers=headers,
        json={"knowledge_base_id": first.id},
    )
    assert initial.status_code == 200
    rebound = await client.put(
        f"/accounts/{account.id}/knowledge-binding",
        headers=headers,
        json={"knowledge_base_id": second.id},
    )
    assert rebound.status_code == 200
    assert rebound.json()["knowledge_base_id"] == second.id
    active_count = await session.scalar(
        select(func.count()).select_from(AccountKnowledgeBinding).where(
            AccountKnowledgeBinding.account_id == account.id,
            AccountKnowledgeBinding.binding_type == "primary_brand",
            AccountKnowledgeBinding.status == "active",
        )
    )
    assert active_count == 1

    current = await client.get(f"/accounts/{account.id}/knowledge-binding", headers=headers)
    assert current.status_code == 200
    assert current.json()["knowledge_base_id"] == second.id
    deleted = await client.delete(f"/accounts/{account.id}/knowledge-binding", headers=headers)
    assert deleted.status_code == 204
    assert await session.scalar(select(func.count()).select_from(KnowledgeCitation)) == 1
    binding = await client.get(f"/accounts/{account.id}/knowledge-binding", headers=headers)
    assert binding.status_code == 404


@pytest.mark.asyncio
async def test_binding_requires_lead_or_admin_and_account_access(client, session, admin):
    """A visible account alone must not let an operator change its brand source."""

    operator, workspace, account = await _member(
        session, admin, email="binding-operator@test.com", role=WorkspaceRole.OPERATOR
    )
    base = await _brand_base(
        session,
        org_id=admin.org_id,
        client_id=workspace.id,
        name="Operator base",
        creator_id=operator.id,
    )
    response = await client.put(
        f"/accounts/{account.id}/knowledge-binding",
        headers=await _token(client, operator.email, "member-pw-123"),
        json={"knowledge_base_id": base.id},
    )
    assert response.status_code == 403
