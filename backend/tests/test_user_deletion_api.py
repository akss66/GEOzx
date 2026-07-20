"""Protected two-phase user deletion lifecycle tests."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy import delete, select

from app.config import settings
from app.core.security import hash_password
from app.models import (
    Account,
    AdminSecurityCredential,
    AgentInvocation,
    AgentToolCall,
    BrainTask,
    Client,
    ClientMembership,
    ContentItem,
    Deliverable,
    Event,
    GateApproval,
    KnowledgeCitation,
    KnowledgeEntry,
    LLMCall,
    MaterialAsset,
    MatrixDistributionItem,
    MatrixDistributionPlan,
    Notification,
    Org,
    Project,
    TaskBrief,
    User,
)
from app.models.enums import (
    AgentCode,
    DeliverableType,
    GateType,
    KnowledgeCategory,
    Platform,
    UserRole,
    WorkspaceRole,
)


async def _login(client, email: str, password: str) -> str:
    response = await client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _code(response) -> str:
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    return detail["code"]


async def _target(
    session,
    admin: User,
    suffix: str,
    *,
    role: UserRole = UserRole.USER,
    is_active: bool = True,
) -> User:
    target = User(
        org_id=admin.org_id,
        email=f"target-{suffix}@test.com",
        hashed_password=hash_password("target-pw-123"),
        display_name=f"Target {suffix}",
        role=role,
        is_active=is_active,
    )
    session.add(target)
    await session.commit()
    await session.refresh(target)
    return target


async def _ready_secondary_password(session, admin: User) -> AdminSecurityCredential:
    credential = AdminSecurityCredential(
        user_id=admin.id,
        password_hash=hash_password("delete-pass-123"),
        changed_at=datetime.now(UTC) - timedelta(minutes=20),
        delete_available_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    session.add(credential)
    await session.commit()
    return credential


async def _preview(client, token: str, target: User) -> dict:
    response = await client.post(
        f"/users/{target.id}/deletion-preview",
        headers=_auth(token),
    )
    assert response.status_code == 200
    return response.json()


async def _delete(client, token: str, target: User, preview_token: str, **overrides):
    body = {
        "preview_token": preview_token,
        "target_email": target.email,
        "secondary_password": "delete-pass-123",
    }
    body.update(overrides)
    return await client.request(
        "DELETE",
        f"/users/{target.id}/permanent",
        headers=_auth(token),
        json=body,
    )


@pytest.mark.asyncio
async def test_member_cannot_use_protected_user_lifecycle_endpoints(
    client, session, admin, member
):
    target = await _target(session, admin, "member-forbidden")
    token = await _login(client, member.email, "user-pw-123")

    reset = await client.post(
        f"/users/{target.id}/reset-password",
        headers=_auth(token),
        json={"new_password": "replacement-pw-123"},
    )
    preview = await client.post(
        f"/users/{target.id}/deletion-preview", headers=_auth(token)
    )
    permanent = await client.request(
        "DELETE",
        f"/users/{target.id}/permanent",
        headers=_auth(token),
        json={
            "preview_token": "x" * 32,
            "target_email": target.email,
            "secondary_password": "delete-pass-123",
        },
    )

    assert reset.status_code == 403
    assert preview.status_code == 403
    assert permanent.status_code == 403
    assert await session.get(User, target.id) is not None


@pytest.mark.asyncio
async def test_permanent_delete_rejects_stale_same_count_preview(
    client, session, admin
):
    target = await _target(session, admin, "stale")
    original = BrainTask(org_id=admin.org_id, created_by_id=target.id, title="Original")
    session.add(original)
    await session.commit()
    token = await _login(client, admin.email, "admin-pw-123")
    await _ready_secondary_password(session, admin)
    preview = await _preview(client, token, target)

    await session.delete(original)
    await session.commit()
    replacement = BrainTask(
        org_id=admin.org_id,
        created_by_id=target.id,
        title="Replacement with the same count",
    )
    session.add(replacement)
    await session.commit()
    response = await _delete(client, token, target, preview["preview_token"])

    assert response.status_code == 409
    assert _code(response) == "USER_DELETION_PREVIEW_STALE"
    assert await session.get(User, target.id) is not None
    assert await session.get(BrainTask, replacement.id) is not None


@pytest.mark.asyncio
async def test_permanent_delete_rejects_target_email_mismatch_without_password_attempt(
    client, session, admin
):
    target = await _target(session, admin, "email")
    token = await _login(client, admin.email, "admin-pw-123")
    credential = await _ready_secondary_password(session, admin)
    preview = await _preview(client, token, target)

    response = await _delete(
        client,
        token,
        target,
        preview["preview_token"],
        target_email="different@test.com",
    )

    assert response.status_code == 409
    assert _code(response) == "USER_DELETION_EMAIL_MISMATCH"
    await session.refresh(credential)
    assert credential.failed_attempts == 0
    assert await session.get(User, target.id) is not None


@pytest.mark.asyncio
async def test_permanent_delete_rejects_wrong_secondary_password_and_keeps_assets(
    client, session, admin
):
    target = await _target(session, admin, "wrong-password")
    owned = BrainTask(org_id=admin.org_id, created_by_id=target.id, title="Keep me")
    session.add(owned)
    await session.commit()
    token = await _login(client, admin.email, "admin-pw-123")
    credential = await _ready_secondary_password(session, admin)
    preview = await _preview(client, token, target)

    response = await _delete(
        client,
        token,
        target,
        preview["preview_token"],
        secondary_password="wrong-secondary-password",
    )

    assert response.status_code == 401
    assert _code(response) == "SECONDARY_PASSWORD_INVALID"
    await session.refresh(credential)
    assert credential.failed_attempts == 1
    assert await session.get(User, target.id) is not None
    assert await session.get(BrainTask, owned.id) is not None
    assert "wrong-secondary-password" not in str(
        [event.payload for event in await session.scalars(select(Event))]
    )


@pytest.mark.asyncio
async def test_permanent_delete_reports_secondary_password_cooldown(
    client, session, admin
):
    target = await _target(session, admin, "cooldown")
    token = await _login(client, admin.email, "admin-pw-123")
    credential = await _ready_secondary_password(session, admin)
    credential.delete_available_at = datetime.now(UTC) + timedelta(minutes=10)
    await session.commit()
    target_id = target.id
    preview = await _preview(client, token, target)

    response = await _delete(client, token, target, preview["preview_token"])

    assert response.status_code == 409
    assert _code(response) == "SECONDARY_PASSWORD_COOLDOWN"
    assert await session.get(User, target_id) is not None


@pytest.mark.asyncio
async def test_permanent_delete_reports_secondary_password_lock(
    client, session, admin
):
    target = await _target(session, admin, "locked")
    token = await _login(client, admin.email, "admin-pw-123")
    credential = await _ready_secondary_password(session, admin)
    credential.failed_attempts = 5
    credential.locked_until = datetime.now(UTC) + timedelta(minutes=15)
    await session.commit()
    target_id = target.id
    preview = await _preview(client, token, target)

    response = await _delete(client, token, target, preview["preview_token"])

    assert response.status_code == 429
    assert _code(response) == "SECONDARY_PASSWORD_LOCKED"
    assert await session.get(User, target_id) is not None


@pytest.mark.asyncio
async def test_deletion_preview_is_bound_to_the_executing_administrator(
    client, session, admin
):
    target = await _target(session, admin, "actor-bound")
    second_admin = await _target(session, admin, "other-actor", role=UserRole.ADMIN)
    first_token = await _login(client, admin.email, "admin-pw-123")
    second_token = await _login(client, second_admin.email, "target-pw-123")
    preview = await _preview(client, first_token, target)

    response = await _delete(client, second_token, target, preview["preview_token"])

    assert response.status_code == 409
    assert _code(response) == "USER_DELETION_PREVIEW_INVALID"
    assert await session.get(User, target.id) is not None


@pytest.mark.asyncio
async def test_deletion_preview_hides_cross_organization_users(client, session, admin):
    other_org = Org(name="Other deletion org")
    target = User(
        org=other_org,
        email="cross-org-delete@test.com",
        hashed_password=hash_password("cross-org-pw"),
        display_name="Cross org target",
    )
    session.add(target)
    await session.commit()
    token = await _login(client, admin.email, "admin-pw-123")

    response = await client.post(
        f"/users/{target.id}/deletion-preview", headers=_auth(token)
    )

    assert response.status_code == 404
    assert await session.get(User, target.id) is not None


@pytest.mark.asyncio
async def test_permanent_delete_rolls_back_every_change_on_transaction_failure(
    client, session, admin, monkeypatch
):
    import app.services.user_deletion as user_deletion

    target = await _target(session, admin, "rollback")
    owned = BrainTask(org_id=admin.org_id, created_by_id=target.id, title="Rollback")
    session.add(owned)
    await session.commit()
    target_id = target.id
    owned_id = owned.id
    token = await _login(client, admin.email, "admin-pw-123")
    credential = await _ready_secondary_password(session, admin)
    credential.failed_attempts = 4
    await session.commit()
    credential_id = credential.id
    preview = await _preview(client, token, target)

    async def fail_after_first_write(deletion_session, impact):
        await deletion_session.execute(
            delete(BrainTask).where(BrainTask.id.in_(impact.record_ids["brain_tasks"]))
        )
        raise RuntimeError("forced transaction failure")

    monkeypatch.setattr(user_deletion, "_delete_owned_records", fail_after_first_write)
    response = await _delete(client, token, target, preview["preview_token"])

    assert response.status_code == 500
    assert _code(response) == "USER_DELETION_TRANSACTION_FAILED"
    assert "forced transaction failure" not in response.text
    session.expire_all()
    assert await session.get(User, target_id) is not None
    assert await session.get(BrainTask, owned_id) is not None
    stored_credential = await session.get(AdminSecurityCredential, credential_id)
    assert stored_credential is not None
    assert stored_credential.failed_attempts == 4


@pytest.mark.asyncio
async def test_permanent_delete_removes_owned_roots_and_keeps_sanitized_receipt(
    client, session, admin
):
    target = await _target(session, admin, "owned-assets")
    workspace = Client(org_id=admin.org_id, name="Deletion client")
    project = Project(org_id=admin.org_id, client=workspace, name="Deletion project")
    account = Account(
        org_id=admin.org_id,
        client=workspace,
        project=project,
        platform=Platform.DOUYIN,
        nickname="Deletion account",
    )
    session.add_all([workspace, project, account])
    await session.flush()

    content = ContentItem(
        project_id=project.id,
        created_by_id=target.id,
        account_id=account.id,
        title="Owned content",
    )
    shared_content = ContentItem(project_id=project.id, title="Unowned legacy content")
    task = BrainTask(org_id=admin.org_id, created_by_id=target.id, title="Owned task")
    shared_task = BrainTask(org_id=admin.org_id, title="Unowned legacy task")
    session.add_all([content, shared_content, task, shared_task])
    await session.flush()
    task.brief = TaskBrief(goal="Delete this runtime")
    invocation = AgentInvocation(
        task_id=task.id,
        agent_code=AgentCode.POSITIONING,
        agent_name="Positioning",
    )
    tool_call = AgentToolCall(
        org_id=admin.org_id,
        task_id=task.id,
        tool_code="owned-tool",
        tool_name="Owned tool",
    )
    deliverable = Deliverable(
        content_item_id=content.id,
        agent_code="02-content",
        type=DeliverableType.VIDEO_SCRIPT,
        payload={"title": "Owned"},
    )
    approval = GateApproval(
        content_item_id=shared_content.id,
        gate=GateType.PRE_PUBLISH_REVIEW,
        decided_by=target.id,
    )
    material = MaterialAsset(org_id=admin.org_id, content_item_id=content.id)
    session.add_all([invocation, tool_call, deliverable, approval, material])
    await session.flush()

    plan = MatrixDistributionPlan(
        org_id=admin.org_id,
        created_by_id=target.id,
        title="Owned distribution",
    )
    shared_plan = MatrixDistributionPlan(org_id=admin.org_id, title="Unowned distribution")
    knowledge = KnowledgeEntry(
        org_id=admin.org_id,
        client_id=workspace.id,
        project_id=project.id,
        category=KnowledgeCategory.USER_PERSONA,
        title="Owned knowledge",
        content="Owned knowledge content",
        source_label="Manual",
        created_by_id=target.id,
    )
    shared_knowledge = KnowledgeEntry(
        org_id=admin.org_id,
        client_id=workspace.id,
        category=KnowledgeCategory.USER_PERSONA,
        title="Unowned knowledge",
        content="Shared",
        source_label="Legacy",
    )
    owned_call = LLMCall(
        org_id=admin.org_id,
        created_by_id=target.id,
        provider="deepseek",
        model="deepseek-chat",
    )
    shared_call = LLMCall(
        org_id=admin.org_id,
        provider="deepseek",
        model="deepseek-chat",
    )
    session.add_all([plan, shared_plan, knowledge, shared_knowledge, owned_call, shared_call])
    await session.flush()
    item = MatrixDistributionItem(
        org_id=admin.org_id,
        plan_id=plan.id,
        account_id=account.id,
        material_id=material.id,
        platform="douyin",
    )
    citation = KnowledgeCitation(
        org_id=admin.org_id,
        client_id=workspace.id,
        entry_id=knowledge.id,
        task_id=task.id,
        invocation_id=invocation.id,
        agent_code="01-positioning",
    )
    membership = ClientMembership(
        client_id=workspace.id,
        user_id=target.id,
        role=WorkspaceRole.OPERATOR,
    )
    notification = Notification(
        org_id=admin.org_id,
        user_id=target.id,
        type="owned",
        title="Owned notification",
    )
    target_event = Event(
        type="target.activity",
        payload={"actor_user_id": target.id, "content": "private history"},
    )
    nested_review_event = Event(
        type="target.reviewed",
        payload={"review": {"reviewer_id": target.id}, "content": "private review"},
    )
    unrelated_event = Event(type="unrelated", payload={"actor_user_id": admin.id})
    session.add_all(
        [
            item,
            citation,
            membership,
            notification,
            target_event,
            nested_review_event,
            unrelated_event,
        ]
    )
    await session.commit()
    deleted_ids = {
        User: target.id,
        BrainTask: task.id,
        ContentItem: content.id,
        MatrixDistributionPlan: plan.id,
        KnowledgeEntry: knowledge.id,
        LLMCall: owned_call.id,
        AgentInvocation: invocation.id,
        AgentToolCall: tool_call.id,
        Deliverable: deliverable.id,
        GateApproval: approval.id,
        MatrixDistributionItem: item.id,
        KnowledgeCitation: citation.id,
        Notification: notification.id,
    }
    retained_ids = {
        BrainTask: shared_task.id,
        ContentItem: shared_content.id,
        MatrixDistributionPlan: shared_plan.id,
        KnowledgeEntry: shared_knowledge.id,
        LLMCall: shared_call.id,
    }
    unrelated_event_id = unrelated_event.id

    token = await _login(client, admin.email, "admin-pw-123")
    await _ready_secondary_password(session, admin)
    preview = await _preview(client, token, target)
    response = await _delete(client, token, target, preview["preview_token"])

    assert response.status_code == 200
    receipt = response.json()
    assert receipt["counts"]["brain_tasks"] == 1
    assert receipt["counts"]["content_items"] == 1
    assert receipt["counts"]["matrix_distribution_plans"] == 1
    assert receipt["counts"]["knowledge_entries"] == 1
    assert receipt["counts"]["llm_calls"] == 1
    session.expire_all()
    for model, row_id in deleted_ids.items():
        assert await session.get(model, row_id) is None
    for model, row_id in retained_ids.items():
        assert await session.get(model, row_id) is not None

    receipts = list(
        await session.scalars(select(Event).where(Event.type == "user.permanently_deleted"))
    )
    assert len(receipts) == 1
    assert set(receipts[0].payload) == {"actor_id", "operation_id", "timestamp", "counts"}
    assert "target-owned-assets@test.com" not in str(receipts[0].payload)
    assert "Target owned-assets" not in str(receipts[0].payload)
    assert "private history" not in str(receipts[0].payload)
    assert await session.get(Event, unrelated_event_id) is not None


@pytest.mark.asyncio
async def test_permanent_delete_rejects_self_deletion(client, session, admin):
    second_admin = await _target(session, admin, "second-admin", role=UserRole.ADMIN)
    assert second_admin.is_active is True
    token = await _login(client, admin.email, "admin-pw-123")
    await _ready_secondary_password(session, admin)
    preview = await _preview(client, token, admin)
    assert preview["allowed"] is False
    assert "USER_SELF_DELETION_FORBIDDEN" in preview["blockers"]

    response = await _delete(client, token, admin, preview["preview_token"])

    assert response.status_code == 409
    assert _code(response) == "USER_SELF_DELETION_FORBIDDEN"
    assert await session.get(User, admin.id) is not None


@pytest.mark.asyncio
async def test_permanent_delete_protects_last_active_administrator(
    client, session, admin
):
    token = await _login(client, admin.email, "admin-pw-123")
    await _ready_secondary_password(session, admin)
    preview = await _preview(client, token, admin)
    assert preview["allowed"] is False
    assert "LAST_ACTIVE_ADMIN" in preview["blockers"]

    response = await _delete(client, token, admin, preview["preview_token"])

    assert response.status_code == 409
    assert _code(response) == "LAST_ACTIVE_ADMIN"
    stored = await session.get(User, admin.id)
    assert stored is not None and stored.is_active is True


@pytest.mark.asyncio
async def test_permanent_delete_rejects_expired_preview(client, session, admin):
    target = await _target(session, admin, "expired")
    token = await _login(client, admin.email, "admin-pw-123")
    await _ready_secondary_password(session, admin)
    preview = await _preview(client, token, target)
    claims = jwt.decode(
        preview["preview_token"],
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        options={"verify_exp": False},
    )
    claims["exp"] = datetime.now(UTC) - timedelta(seconds=1)
    expired_token = jwt.encode(
        claims, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )

    response = await _delete(client, token, target, expired_token)

    assert response.status_code == 409
    assert _code(response) == "USER_DELETION_PREVIEW_EXPIRED"
    assert await session.get(User, target.id) is not None


@pytest.mark.asyncio
async def test_permanent_delete_preview_is_single_use(client, session, admin):
    target = await _target(session, admin, "single-use")
    token = await _login(client, admin.email, "admin-pw-123")
    await _ready_secondary_password(session, admin)
    preview = await _preview(client, token, target)

    first = await _delete(client, token, target, preview["preview_token"])
    replay = await _delete(client, token, target, preview["preview_token"])

    assert first.status_code == 200
    assert replay.status_code == 409
    assert _code(replay) == "USER_DELETION_PREVIEW_USED"
