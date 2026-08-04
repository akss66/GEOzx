from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.core.security import create_access_token
from app.models import (
    AgentRun,
    ContentScheduleEntry,
    Event,
    ProjectMembership,
    ShootTask,
    TurnInterrupt,
    User,
)
from app.models.enums import DeliverableType, WorkspaceRole
from tests.test_artifacts_api import _seed_artifact, _video_script_payload


def _headers(user: User) -> dict[str, str]:
    token = create_access_token(str(user.id), user.role.value)
    return {"Authorization": f"Bearer {token}"}


async def _seed_pending_sources(session, owner: User, *, account_name: str):
    seeded = await _seed_artifact(
        session,
        owner,
        account_name=account_name,
        payload=_video_script_payload("spoken"),
        skill_code="script_generation",
        deliverable_type=DeliverableType.VIDEO_SCRIPT,
    )
    project, account, content, thread, turn, task, run, _, artifact = seeded
    return project, account, content, thread, turn, task, run, artifact


async def _pending_interrupt(
    session,
    *,
    owner: User,
    account,
    thread,
    turn,
    task,
    semantic_key: str,
    kind: str = "clarification",
):
    run = AgentRun(
        org_id=owner.org_id,
        requested_by_id=owner.id,
        task_id=task.id,
        thread_id=thread.id,
        turn_id=turn.id,
        client_message_id=f"pending-{semantic_key}",
        status="waiting_user",
        phase="waiting_user",
    )
    session.add(run)
    await session.flush()
    interrupt = TurnInterrupt(
        org_id=owner.org_id,
        account_id=account.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        kind=kind,
        status="pending",
        public_message=f"请处理 {semantic_key}",
        action_label=None,
        response_schema={},
        semantic_key=semantic_key,
        version=1,
    )
    session.add(interrupt)
    await session.flush()
    return interrupt


@pytest.mark.asyncio
async def test_pending_work_projects_only_selected_account_and_current_users_personal_work(
    client, session, admin, member
) -> None:
    source_a = await _seed_pending_sources(session, admin, account_name="pending-account-a")
    source_b = await _seed_pending_sources(session, admin, account_name="pending-account-b")
    project_a, account_a, content_a, thread_a, turn_a, task_a, _, artifact_a = source_a
    project_b, account_b, content_b, _, _, _, _, artifact_b = source_b
    session.add_all([
        ProjectMembership(project_id=project_a.id, user_id=member.id, role=WorkspaceRole.OPERATOR),
        ProjectMembership(project_id=project_b.id, user_id=member.id, role=WorkspaceRole.OPERATOR),
    ])
    await session.flush()

    admin_interrupt = await _pending_interrupt(
        session,
        owner=admin,
        account=account_a,
        thread=thread_a,
        turn=turn_a,
        task=task_a,
        semantic_key="admin-only",
    )
    member_interrupt = await _pending_interrupt(
        session,
        owner=member,
        account=account_a,
        thread=thread_a,
        turn=turn_a,
        task=task_a,
        semantic_key="member-only",
    )
    assigned = ShootTask(
        org_id=admin.org_id,
        account_id=account_a.id,
        content_item_id=content_a.id,
        source_artifact_id=artifact_a.id,
        source_artifact_version=artifact_a.version,
        created_by_id=admin.id,
        assignee_id=member.id,
        title="成员负责拍摄",
        status="pending",
        due_at=datetime.now(UTC) + timedelta(days=1),
    )
    owner_only = ShootTask(
        org_id=admin.org_id,
        account_id=account_a.id,
        content_item_id=content_a.id,
        source_artifact_id=artifact_a.id,
        source_artifact_version=artifact_a.version,
        created_by_id=admin.id,
        assignee_id=None,
        title="创建者负责拍摄",
        status="pending",
    )
    other_account = ShootTask(
        org_id=admin.org_id,
        account_id=account_b.id,
        content_item_id=content_b.id,
        source_artifact_id=artifact_b.id,
        source_artifact_version=artifact_b.version,
        created_by_id=member.id,
        assignee_id=None,
        title="另一个账号的拍摄",
        status="pending",
    )
    schedule = ContentScheduleEntry(
        org_id=admin.org_id,
        account_id=account_a.id,
        content_item_id=content_a.id,
        source_artifact_id=artifact_a.id,
        source_artifact_version=artifact_a.version,
        created_by_id=admin.id,
        scheduled_at=datetime.now(UTC) + timedelta(days=2),
        timezone="Asia/Shanghai",
        status="planned",
    )
    session.add_all([assigned, owner_only, other_account, schedule])
    await session.commit()

    member_response = await client.get(
        f"/accounts/{account_a.id}/pending-work", headers=_headers(member)
    )
    assert member_response.status_code == 200, member_response.text
    member_items = [
        item
        for group in member_response.json()["groups"]
        for item in group["items"]
    ]
    member_ids = {item["id"] for item in member_items}
    assert f"interrupt:{member_interrupt.id}" in member_ids
    assert f"interrupt:{admin_interrupt.id}" not in member_ids
    assert f"shoot_task:{assigned.id}" in member_ids
    assert f"shoot_task:{owner_only.id}" not in member_ids
    assert f"shoot_task:{other_account.id}" not in member_ids
    assert f"schedule_entry:{schedule.id}" not in member_ids
    assert [group["kind"] for group in member_response.json()["groups"]] == [
        "clarification",
        "approval",
        "shoot_task",
        "manual_publish",
        "account_data",
    ]
    assert member_response.json()["groups"][-1]["count"] == 1

    admin_response = await client.get(
        f"/accounts/{account_a.id}/pending-work", headers=_headers(admin)
    )
    assert admin_response.status_code == 200
    admin_ids = {
        item["id"]
        for group in admin_response.json()["groups"]
        for item in group["items"]
    }
    assert f"interrupt:{admin_interrupt.id}" in admin_ids
    assert f"interrupt:{member_interrupt.id}" not in admin_ids
    assert f"shoot_task:{assigned.id}" not in admin_ids
    assert f"shoot_task:{owner_only.id}" in admin_ids
    assert f"schedule_entry:{schedule.id}" in admin_ids


@pytest.mark.asyncio
async def test_pending_work_rejects_an_account_outside_the_users_workspace(
    client, session, admin, member
) -> None:
    _, account, *_ = await _seed_pending_sources(
        session, admin, account_name="pending-hidden-account"
    )

    response = await client.get(
        f"/accounts/{account.id}/pending-work", headers=_headers(member)
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_pending_work_projects_data_actions_only_to_workspace_operators(
    client, session, admin, member
) -> None:
    project, account, *_ = await _seed_pending_sources(
        session,
        admin,
        account_name="pending-data-role-boundary",
    )
    membership = ProjectMembership(
        project_id=project.id,
        user_id=member.id,
        role=WorkspaceRole.OPERATOR,
    )
    session.add(membership)
    await session.commit()

    async def data_count(role: WorkspaceRole) -> int:
        membership.role = role
        await session.commit()
        response = await client.get(
            f"/accounts/{account.id}/pending-work",
            headers=_headers(member),
        )
        assert response.status_code == 200, response.text
        return next(
            group["count"]
            for group in response.json()["groups"]
            if group["kind"] == "account_data"
        )

    assert await data_count(WorkspaceRole.OPERATOR) == 1
    assert await data_count(WorkspaceRole.LEAD) == 1
    assert await data_count(WorkspaceRole.EDITOR) == 0
    assert await data_count(WorkspaceRole.REVIEWER) == 0


@pytest.mark.asyncio
async def test_pending_work_completion_is_ownership_safe_idempotent_and_disappears(
    client, session, admin, member
) -> None:
    project, account, content, _, _, _, _, artifact = await _seed_pending_sources(
        session, admin, account_name="pending-lifecycle"
    )
    session.add(
        ProjectMembership(project_id=project.id, user_id=member.id, role=WorkspaceRole.OPERATOR)
    )
    shoot = ShootTask(
        org_id=admin.org_id,
        account_id=account.id,
        content_item_id=content.id,
        source_artifact_id=artifact.id,
        source_artifact_version=artifact.version,
        created_by_id=admin.id,
        assignee_id=member.id,
        title="完成这次拍摄",
        status="pending",
    )
    schedule = ContentScheduleEntry(
        org_id=admin.org_id,
        account_id=account.id,
        content_item_id=content.id,
        source_artifact_id=artifact.id,
        source_artifact_version=artifact.version,
        created_by_id=admin.id,
        scheduled_at=datetime.now(UTC),
        timezone="Asia/Shanghai",
        status="planned",
    )
    session.add_all([shoot, schedule])
    await session.commit()

    forbidden_shoot = await client.post(
        f"/accounts/{account.id}/pending-work/shoot-tasks/{shoot.id}/complete",
        headers=_headers(admin),
    )
    forbidden_schedule = await client.post(
        f"/accounts/{account.id}/pending-work/schedule-entries/{schedule.id}/publish",
        headers=_headers(member),
    )
    assert forbidden_shoot.status_code == 404
    assert forbidden_schedule.status_code == 404

    first_shoot = await client.post(
        f"/accounts/{account.id}/pending-work/shoot-tasks/{shoot.id}/complete",
        headers=_headers(member),
    )
    replay_shoot = await client.post(
        f"/accounts/{account.id}/pending-work/shoot-tasks/{shoot.id}/complete",
        headers=_headers(member),
    )
    first_schedule = await client.post(
        f"/accounts/{account.id}/pending-work/schedule-entries/{schedule.id}/publish",
        headers=_headers(admin),
    )
    replay_schedule = await client.post(
        f"/accounts/{account.id}/pending-work/schedule-entries/{schedule.id}/publish",
        headers=_headers(admin),
    )
    assert first_shoot.status_code == 200
    assert replay_shoot.json() == first_shoot.json()
    assert first_schedule.status_code == 200
    assert replay_schedule.json() == first_schedule.json()

    member_projection = await client.get(
        f"/accounts/{account.id}/pending-work", headers=_headers(member)
    )
    admin_projection = await client.get(
        f"/accounts/{account.id}/pending-work", headers=_headers(admin)
    )
    projected_ids = {
        item["id"]
        for response in (member_projection, admin_projection)
        for group in response.json()["groups"]
        for item in group["items"]
    }
    assert f"shoot_task:{shoot.id}" not in projected_ids
    assert f"schedule_entry:{schedule.id}" not in projected_ids
    lifecycle_events = list(
        await session.scalars(
            select(Event).where(
                Event.type == "pending_work.updated",
                Event.account_id == account.id,
            )
        )
    )
    assert len(lifecycle_events) == 2
    assert {event.payload["resource_kind"] for event in lifecycle_events} == {
        "shoot_task",
        "schedule_entry",
    }


@pytest.mark.asyncio
async def test_pending_work_uses_safe_business_fallback_when_source_artifact_is_missing(
    client, session, admin
) -> None:
    _, account, content, _, _, _, _, artifact = await _seed_pending_sources(
        session, admin, account_name="pending-missing-source"
    )
    shoot = ShootTask(
        org_id=admin.org_id,
        account_id=account.id,
        content_item_id=content.id,
        source_artifact_id=artifact.id,
        source_artifact_version=artifact.version,
        created_by_id=admin.id,
        assignee_id=None,
        title="来源缺失也能处理",
        status="pending",
    )
    session.add(shoot)
    await session.commit()
    await session.execute(text("PRAGMA foreign_keys = OFF"))
    await session.execute(text("DELETE FROM deliverables WHERE id = :id"), {"id": artifact.id})
    await session.commit()
    await session.execute(text("PRAGMA foreign_keys = ON"))

    response = await client.get(
        f"/accounts/{account.id}/pending-work", headers=_headers(admin)
    )

    assert response.status_code == 200
    item = next(
        item
        for group in response.json()["groups"]
        for item in group["items"]
        if item["id"] == f"shoot_task:{shoot.id}"
    )
    assert item["target"] == {"type": "task_workspace"}
    assert "deliverable" not in response.text.lower()
