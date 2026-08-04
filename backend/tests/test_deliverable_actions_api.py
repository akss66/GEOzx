import pytest
from sqlalchemy import select, text

from app.config import settings
from app.core.security import hash_password
from app.models import (
    AgentRun,
    ConversationTurn,
    Deliverable,
    DeliverableActionExecution,
    ProjectMembership,
    User,
)
from app.models.enums import (
    DeliverableStatus,
    DeliverableType,
    UserRole,
    WorkspaceRole,
)
from app.schemas.deliverable_actions import DeliverableActionRequest
from app.services.deliverable_action_registry import SERVER_ACTIONS
from app.services.deliverable_actions import ACTION_HANDLERS, _request_fingerprint
from tests.test_artifacts_api import _auth, _seed_artifact, _token, _video_script_payload


async def _seed_script_artifact(session, admin, *, account_name: str = "shoot-action-account"):
    return await _seed_artifact(
        session,
        admin,
        account_name=account_name,
        payload=_video_script_payload("spoken"),
        skill_code="script_generation",
        deliverable_type=DeliverableType.VIDEO_SCRIPT,
    )


async def test_create_shoot_task_action_creates_one_real_task_and_replays_same_request(
    client, session, admin
) -> None:
    seeded = await _seed_script_artifact(session, admin)
    token = await _token(client, admin.email, "admin-pw-123")
    headers = {
        **_auth(token),
        "Idempotency-Key": "shoot-task-replay-key",
    }

    first = await client.post(
        f"/artifacts/{seeded[8].id}/actions/create_shoot_task",
        headers=headers,
        json={"confirmed": True},
    )
    second = await client.post(
        f"/artifacts/{seeded[8].id}/actions/create_shoot_task",
        headers=headers,
        json={"confirmed": True},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["action_code"] == "create_shoot_task"
    assert first.json()["status"] == "succeeded"
    assert first.json()["resource"]["type"] == "shoot_task"
    assert first.json()["resource"]["id"] > 0
    assert first.json()["replayed"] is False
    assert second.json() == {**first.json(), "replayed": True}
    assert await session.scalar(text("SELECT COUNT(*) FROM shoot_tasks")) == 1
    assert await session.scalar(text("SELECT COUNT(*) FROM deliverable_action_executions")) == 1


async def test_action_latest_gate_is_scoped_to_the_source_agent_stream(
    client, session, admin
) -> None:
    seeded = await _seed_script_artifact(
        session, admin, account_name="cross-agent-action-stream"
    )
    source = seeded[8]
    session.add(
        Deliverable(
            content_item_id=source.content_item_id,
            agent_code="02-content-director",
            type=source.type,
            version=2,
            status=DeliverableStatus.PENDING_REVIEW,
            payload=dict(source.payload),
        )
    )
    await session.commit()
    token = await _token(client, admin.email, "admin-pw-123")

    response = await client.post(
        f"/artifacts/{source.id}/actions/create_shoot_task",
        headers={
            **_auth(token),
            "Idempotency-Key": "cross-agent-action-stream-key",
        },
        json={"confirmed": True},
    )

    assert response.status_code == 200, response.text
    assert response.json()["artifact_version"] == 1


async def test_create_shoot_task_rejects_same_key_with_different_request_fingerprint(
    client, session, admin
) -> None:
    seeded = await _seed_script_artifact(session, admin, account_name="shoot-key-conflict")
    token = await _token(client, admin.email, "admin-pw-123")
    headers = {
        **_auth(token),
        "Idempotency-Key": "shoot-task-conflict-key",
    }

    first = await client.post(
        f"/artifacts/{seeded[8].id}/actions/create_shoot_task",
        headers=headers,
        json={"confirmed": True},
    )
    conflict = await client.post(
        f"/artifacts/{seeded[8].id}/actions/create_shoot_task",
        headers=headers,
        json={"confirmed": True, "due_at": "2026-08-05T09:30:00Z"},
    )

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_key_conflict"
    assert await session.scalar(text("SELECT COUNT(*) FROM shoot_tasks")) == 1
    assert await session.scalar(text("SELECT COUNT(*) FROM deliverable_action_executions")) == 1


async def test_create_shoot_task_requires_confirmation_and_creates_no_side_effect(
    client, session, admin
) -> None:
    seeded = await _seed_script_artifact(session, admin, account_name="shoot-confirmation")
    token = await _token(client, admin.email, "admin-pw-123")
    headers = {
        **_auth(token),
        "Idempotency-Key": "shoot-task-confirmation-key",
    }

    response = await client.post(
        f"/artifacts/{seeded[8].id}/actions/create_shoot_task",
        headers=headers,
        json={"confirmed": False},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "confirmation_required"
    assert await session.scalar(text("SELECT COUNT(*) FROM shoot_tasks")) == 0
    assert await session.scalar(text("SELECT COUNT(*) FROM deliverable_action_executions")) == 0


async def test_add_to_schedule_creates_one_real_schedule_entry(
    client, session, admin
) -> None:
    seeded = await _seed_artifact(
        session,
        admin,
        account_name="schedule-action-account",
        payload={
            "period": "2026-08-10 至 2026-08-16",
            "items": [{"date": "2026-08-10", "title": "第一条"}],
            "operating_notes": [],
        },
        skill_code="content_calendar_planning",
        deliverable_type=DeliverableType.PUBLISH_CALENDAR,
    )
    token = await _token(client, admin.email, "admin-pw-123")
    response = await client.post(
        f"/artifacts/{seeded[8].id}/actions/add_to_schedule",
        headers={**_auth(token), "Idempotency-Key": "schedule-entry-key"},
        json={
            "confirmed": True,
            "scheduled_at": "2026-08-10T09:00:00+08:00",
            "timezone": "Asia/Shanghai",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["resource"]["type"] == "schedule_entry"
    assert await session.scalar(text("SELECT COUNT(*) FROM content_schedule_entries")) == 1


async def test_client_only_or_unadvertised_action_fails_without_a_ledger_row(
    client, session, admin
) -> None:
    seeded = await _seed_script_artifact(session, admin, account_name="unavailable-action")
    token = await _token(client, admin.email, "admin-pw-123")

    response = await client.post(
        f"/artifacts/{seeded[8].id}/actions/export",
        headers={**_auth(token), "Idempotency-Key": "client-only-action-key"},
        json={},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "action_unavailable"
    assert await session.scalar(text("SELECT COUNT(*) FROM deliverable_action_executions")) == 0


async def test_action_hides_another_account_and_rejects_reviewer_role(
    client, session, admin, member
) -> None:
    visible = await _seed_script_artifact(session, admin, account_name="action-visible")
    hidden = await _seed_script_artifact(session, admin, account_name="action-hidden")
    session.add(
        ProjectMembership(
            project_id=visible[0].id,
            user_id=member.id,
            role=WorkspaceRole.REVIEWER,
        )
    )
    await session.commit()
    token = await _token(client, member.email, "user-pw-123")

    reviewer = await client.post(
        f"/artifacts/{visible[8].id}/actions/create_shoot_task",
        headers={**_auth(token), "Idempotency-Key": "reviewer-action-key"},
        json={"confirmed": True},
    )
    hidden_response = await client.post(
        f"/artifacts/{hidden[8].id}/actions/create_shoot_task",
        headers={**_auth(token), "Idempotency-Key": "hidden-action-key"},
        json={"confirmed": True},
    )

    assert reviewer.status_code == 403
    assert hidden_response.status_code == 404
    assert await session.scalar(text("SELECT COUNT(*) FROM shoot_tasks")) == 0


async def test_schedule_requires_operator_role_and_assignee_needs_account_access(
    client, session, admin, member
) -> None:
    calendar = await _seed_artifact(
        session,
        admin,
        account_name="schedule-role-boundary",
        payload={
            "period": "2026-08-10 至 2026-08-16",
            "items": [{"date": "2026-08-10", "title": "第一条"}],
            "operating_notes": [],
        },
        skill_code="content_calendar_planning",
        deliverable_type=DeliverableType.PUBLISH_CALENDAR,
    )
    script = await _seed_script_artifact(
        session,
        admin,
        account_name="shoot-assignee-boundary",
    )
    session.add(
        ProjectMembership(
            project_id=calendar[0].id,
            user_id=member.id,
            role=WorkspaceRole.EDITOR,
        )
    )
    session.add(
        ProjectMembership(
            project_id=script[0].id,
            user_id=member.id,
            role=WorkspaceRole.EDITOR,
        )
    )
    unrelated_member = User(
        org_id=admin.org_id,
        email="unrelated-assignee@test.com",
        hashed_password=hash_password("unused-password"),
        display_name="无账号权限成员",
        role=UserRole.USER,
    )
    session.add(unrelated_member)
    await session.commit()
    member_token = await _token(client, member.email, "user-pw-123")
    admin_token = await _token(client, admin.email, "admin-pw-123")

    denied_schedule = await client.post(
        f"/artifacts/{calendar[8].id}/actions/add_to_schedule",
        headers={**_auth(member_token), "Idempotency-Key": "editor-schedule-key"},
        json={
            "confirmed": True,
            "scheduled_at": "2026-08-10T09:00:00+08:00",
            "timezone": "Asia/Shanghai",
        },
    )
    invalid_assignee = await client.post(
        f"/artifacts/{script[8].id}/actions/create_shoot_task",
        headers={**_auth(admin_token), "Idempotency-Key": "invalid-assignee-key"},
        json={"confirmed": True, "assignee_id": unrelated_member.id},
    )
    denied_assignment = await client.post(
        f"/artifacts/{script[8].id}/actions/create_shoot_task",
        headers={**_auth(member_token), "Idempotency-Key": "editor-assignment-key"},
        json={"confirmed": True, "assignee_id": admin.id},
    )

    assert denied_schedule.status_code == 403
    assert invalid_assignee.status_code == 422
    assert invalid_assignee.json()["detail"]["code"] == "invalid_assignee"
    assert denied_assignment.status_code == 403
    assert await session.scalar(text("SELECT COUNT(*) FROM content_schedule_entries")) == 0
    assert await session.scalar(text("SELECT COUNT(*) FROM shoot_tasks")) == 0


async def test_successful_action_replays_after_new_version_but_a_new_key_is_stale(
    client, session, admin
) -> None:
    seeded = await _seed_script_artifact(session, admin, account_name="action-stale")
    source = seeded[8]
    token = await _token(client, admin.email, "admin-pw-123")
    first_headers = {**_auth(token), "Idempotency-Key": "stale-replay-original"}
    first = await client.post(
        f"/artifacts/{source.id}/actions/create_shoot_task",
        headers=first_headers,
        json={"confirmed": True},
    )
    assert first.status_code == 200

    source.status = DeliverableStatus.SUPERSEDED
    session.add(
        Deliverable(
            content_item_id=source.content_item_id,
            thread_id=source.thread_id,
            turn_id=source.turn_id,
            run_id=source.run_id,
            skill_run_id=source.skill_run_id,
            agent_code=source.agent_code,
            type=source.type,
            version=2,
            status=DeliverableStatus.PENDING_REVIEW,
            payload=source.payload,
        )
    )
    await session.commit()

    replay = await client.post(
        f"/artifacts/{source.id}/actions/create_shoot_task",
        headers=first_headers,
        json={"confirmed": True},
    )
    stale = await client.post(
        f"/artifacts/{source.id}/actions/create_shoot_task",
        headers={**_auth(token), "Idempotency-Key": "stale-new-key"},
        json={"confirmed": True},
    )

    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] in {
        "action_unavailable",
        "content_version_updated",
    }
    assert await session.scalar(text("SELECT COUNT(*) FROM shoot_tasks")) == 1


async def test_request_revision_atomically_creates_v2_and_replays_without_v3(
    client, session, admin
) -> None:
    seeded = await _seed_script_artifact(session, admin, account_name="revision-action")
    source = seeded[8]
    token = await _token(client, admin.email, "admin-pw-123")
    headers = {**_auth(token), "Idempotency-Key": "revision-action-key"}
    payload = _video_script_payload("spoken")
    payload["title"] = "Updated shooting script"

    first = await client.post(
        f"/artifacts/{source.id}/actions/request_revision",
        headers=headers,
        json={"note": "Tighten the opening hook", "payload": payload},
    )
    replay = await client.post(
        f"/artifacts/{source.id}/actions/request_revision",
        headers=headers,
        json={"note": "Tighten the opening hook", "payload": payload},
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert first.json()["status"] == "succeeded"
    assert first.json()["resource"]["type"] == "artifact"
    assert first.json()["resource"]["id"] != source.id
    assert first.json()["result"] == {
        "artifact_id": first.json()["resource"]["id"],
        "artifact_version": 2,
        "message": "修改版本已保存",
    }
    assert replay.json() == {**first.json(), "replayed": True}
    await session.refresh(source)
    assert source.status == DeliverableStatus.SUPERSEDED
    revisions = list(
        await session.scalars(
            select(Deliverable)
            .where(
                Deliverable.content_item_id == source.content_item_id,
                Deliverable.type == source.type,
            )
            .order_by(Deliverable.version)
        )
    )
    assert [(row.version, row.status) for row in revisions] == [
        (1, DeliverableStatus.SUPERSEDED),
        (2, DeliverableStatus.PENDING_REVIEW),
    ]
    assert await session.scalar(text("SELECT COUNT(*) FROM deliverable_action_executions")) == 1


async def test_request_revision_fingerprint_uses_normalized_note_for_replay(
    client, session, admin
) -> None:
    seeded = await _seed_script_artifact(session, admin, account_name="revision-normalized-note")
    source = seeded[8]
    token = await _token(client, admin.email, "admin-pw-123")
    headers = {**_auth(token), "Idempotency-Key": "revision-normalized-note-key"}
    payload = _video_script_payload("spoken")
    payload["title"] = "Normalized note revision"

    first = await client.post(
        f"/artifacts/{source.id}/actions/request_revision",
        headers=headers,
        json={"note": "  Tighten the opening hook  ", "payload": payload},
    )
    replay = await client.post(
        f"/artifacts/{source.id}/actions/request_revision",
        headers=headers,
        json={"note": "Tighten the opening hook", "payload": payload},
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == {**first.json(), "replayed": True}
    assert await session.scalar(text("SELECT COUNT(*) FROM deliverable_action_executions")) == 1
    assert await session.scalar(
        text("SELECT COUNT(*) FROM deliverables WHERE content_item_id = :content_item_id"),
        {"content_item_id": source.content_item_id},
    ) == 2


async def test_request_revision_rejects_action_irrelevant_fields_before_side_effects(
    client, session, admin
) -> None:
    seeded = await _seed_script_artifact(session, admin, account_name="revision-strict-body")
    source = seeded[8]
    token = await _token(client, admin.email, "admin-pw-123")

    response = await client.post(
        f"/artifacts/{source.id}/actions/request_revision",
        headers={**_auth(token), "Idempotency-Key": "revision-strict-key"},
        json={
            "confirmed": True,
            "note": "Valid note",
            "payload": _video_script_payload("spoken"),
        },
    )

    assert response.status_code == 422
    assert await session.scalar(text("SELECT COUNT(*) FROM deliverable_action_executions")) == 0
    assert await session.scalar(
        text("SELECT COUNT(*) FROM deliverables WHERE content_item_id = :content_item_id"),
        {"content_item_id": source.content_item_id},
    ) == 1


async def test_generate_next_iteration_queues_one_trusted_turn_and_replays(
    client, session, admin, monkeypatch
) -> None:
    seeded = await _seed_artifact(
        session,
        admin,
        account_name="iteration-action",
        status=DeliverableStatus.APPROVED,
        skill_code="performance_review",
        deliverable_type=DeliverableType.REVIEW_REPORT,
    )
    source = seeded[8]
    enqueued: list[int] = []

    async def _enqueue(*, run_id: int) -> None:
        enqueued.append(run_id)

    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    monkeypatch.setattr(settings, "main_agent_typed_runtime_enabled", True)
    monkeypatch.setattr(
        "app.services.deliverable_actions.enqueue_agent_runtime",
        _enqueue,
        raising=False,
    )
    token = await _token(client, admin.email, "admin-pw-123")
    headers = {**_auth(token), "Idempotency-Key": "next-iteration-action-key"}

    first = await client.post(
        f"/artifacts/{source.id}/actions/generate_next_iteration",
        headers=headers,
        json={},
    )
    replay = await client.post(
        f"/artifacts/{source.id}/actions/generate_next_iteration",
        headers=headers,
        json={},
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert first.json()["status"] == "queued"
    assert first.json()["resource"]["type"] == "conversation_turn"
    assert replay.json() == {**first.json(), "replayed": True}
    queued_turn = await session.get(ConversationTurn, first.json()["resource"]["id"])
    queued_run = await session.get(AgentRun, first.json()["result"]["run_id"])
    assert queued_turn is not None
    assert queued_run is not None
    assert queued_turn.thread_id == source.thread_id
    assert queued_run.status == "queued"
    assert queued_run.request_payload["requested_skill_code"] == "operation_iteration"
    assert queued_run.request_payload["trusted_structured_input"] == {
        "confirmed_review_artifact_id": source.id,
        "cycle_days": 7,
        "topic_count": 5,
        "constraints": [],
    }
    assert enqueued == [queued_run.id]
    assert await session.scalar(text("SELECT COUNT(*) FROM deliverable_action_executions")) == 1


async def test_processing_replay_returns_structured_conflict_without_second_side_effect(
    client, session, admin
) -> None:
    seeded = await _seed_script_artifact(session, admin, account_name="processing-replay")
    source = seeded[8]
    body = DeliverableActionRequest(confirmed=True)
    session.add(
        DeliverableActionExecution(
            org_id=admin.org_id,
            account_id=seeded[1].id,
            requested_by_id=admin.id,
            artifact_id=source.id,
            artifact_version=source.version,
            action_code="create_shoot_task",
            idempotency_key="processing-action-key",
            request_fingerprint=_request_fingerprint(
                source.id,
                "create_shoot_task",
                body,
            ),
            status="processing",
            result_payload={},
        )
    )
    await session.commit()
    token = await _token(client, admin.email, "admin-pw-123")

    response = await client.post(
        f"/artifacts/{source.id}/actions/create_shoot_task",
        headers={**_auth(token), "Idempotency-Key": "processing-action-key"},
        json={"confirmed": True},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "action_in_progress",
        "message": "该操作正在处理中，请稍后重试",
        "retryable": True,
    }
    assert await session.scalar(text("SELECT COUNT(*) FROM shoot_tasks")) == 0


async def test_request_revision_incomplete_payload_creates_no_execution_or_version(
    client, session, admin
) -> None:
    seeded = await _seed_script_artifact(session, admin, account_name="revision-incomplete")
    source = seeded[8]
    token = await _token(client, admin.email, "admin-pw-123")

    response = await client.post(
        f"/artifacts/{source.id}/actions/request_revision",
        headers={**_auth(token), "Idempotency-Key": "revision-incomplete-key"},
        json={"note": "Missing required script fields", "payload": {"title": "Only title"}},
    )

    assert response.status_code == 422
    await session.refresh(source)
    assert source.status == DeliverableStatus.PENDING_REVIEW
    assert await session.scalar(text("SELECT COUNT(*) FROM deliverable_action_executions")) == 0
    assert await session.scalar(
        text("SELECT COUNT(*) FROM deliverables WHERE content_item_id = :content_item_id"),
        {"content_item_id": source.content_item_id},
    ) == 1


async def test_generate_next_iteration_requires_accepted_review_without_side_effects(
    client, session, admin, monkeypatch
) -> None:
    seeded = await _seed_artifact(
        session,
        admin,
        account_name="iteration-not-accepted",
        status=DeliverableStatus.PENDING_REVIEW,
        deliverable_type=DeliverableType.REVIEW_REPORT,
    )
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    monkeypatch.setattr(settings, "main_agent_typed_runtime_enabled", True)
    token = await _token(client, admin.email, "admin-pw-123")
    before_turns = await session.scalar(text("SELECT COUNT(*) FROM conversation_turns"))
    before_runs = await session.scalar(text("SELECT COUNT(*) FROM agent_runs"))

    response = await client.post(
        f"/artifacts/{seeded[8].id}/actions/generate_next_iteration",
        headers={**_auth(token), "Idempotency-Key": "iteration-not-accepted-key"},
        json={},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "action_unavailable"
    assert await session.scalar(text("SELECT COUNT(*) FROM conversation_turns")) == before_turns
    assert await session.scalar(text("SELECT COUNT(*) FROM agent_runs")) == before_runs
    assert await session.scalar(text("SELECT COUNT(*) FROM deliverable_action_executions")) == 0


def test_server_action_registry_and_handler_map_are_closed() -> None:
    assert set(SERVER_ACTIONS) == set(ACTION_HANDLERS)


async def test_iteration_enqueue_failure_keeps_durable_queued_state_and_replays(
    client, session, admin, monkeypatch
) -> None:
    seeded = await _seed_artifact(
        session,
        admin,
        account_name="iteration-enqueue-failure",
        status=DeliverableStatus.APPROVED,
        deliverable_type=DeliverableType.REVIEW_REPORT,
    )
    calls: list[int] = []

    async def _fail_enqueue(*, run_id: int) -> None:
        calls.append(run_id)
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    monkeypatch.setattr(settings, "main_agent_typed_runtime_enabled", True)
    monkeypatch.setattr(
        "app.services.deliverable_actions.enqueue_agent_runtime",
        _fail_enqueue,
    )
    token = await _token(client, admin.email, "admin-pw-123")
    headers = {**_auth(token), "Idempotency-Key": "iteration-enqueue-failure-key"}

    with pytest.raises(RuntimeError, match="queue unavailable"):
        await client.post(
            f"/artifacts/{seeded[8].id}/actions/generate_next_iteration",
            headers=headers,
            json={},
        )

    execution = await session.scalar(select(DeliverableActionExecution))
    assert execution is not None
    assert execution.status == "queued"
    run = await session.get(AgentRun, execution.result_payload["run_id"])
    turn = await session.get(ConversationTurn, execution.resource_id)
    assert run is not None and run.status == "queued"
    assert turn is not None and turn.status == "queued"

    replay = await client.post(
        f"/artifacts/{seeded[8].id}/actions/generate_next_iteration",
        headers=headers,
        json={},
    )

    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert calls == [run.id]


async def test_successful_replay_needs_read_access_not_current_write_role(
    client, session, admin, member
) -> None:
    seeded = await _seed_script_artifact(session, admin, account_name="replay-role-change")
    membership = ProjectMembership(
        project_id=seeded[0].id,
        user_id=member.id,
        role=WorkspaceRole.EDITOR,
    )
    session.add(membership)
    await session.commit()
    token = await _token(client, member.email, "user-pw-123")
    headers = {**_auth(token), "Idempotency-Key": "replay-role-change-key"}

    first = await client.post(
        f"/artifacts/{seeded[8].id}/actions/create_shoot_task",
        headers=headers,
        json={"confirmed": True},
    )
    membership.role = WorkspaceRole.REVIEWER
    await session.commit()
    replay = await client.post(
        f"/artifacts/{seeded[8].id}/actions/create_shoot_task",
        headers=headers,
        json={"confirmed": True},
    )
    changed_action = await client.post(
        f"/artifacts/{seeded[8].id}/actions/request_revision",
        headers=headers,
        json={
            "note": "Different action",
            "payload": _video_script_payload("spoken"),
        },
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert changed_action.status_code == 409
    assert changed_action.json()["detail"]["code"] == "idempotency_key_conflict"


async def test_generate_next_iteration_does_not_impersonate_source_thread_owner(
    client, session, admin, member, monkeypatch
) -> None:
    seeded = await _seed_artifact(
        session,
        admin,
        account_name="iteration-thread-owner",
        status=DeliverableStatus.APPROVED,
        deliverable_type=DeliverableType.REVIEW_REPORT,
    )
    session.add(
        ProjectMembership(
            project_id=seeded[0].id,
            user_id=member.id,
            role=WorkspaceRole.EDITOR,
        )
    )
    await session.commit()
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    monkeypatch.setattr(settings, "main_agent_typed_runtime_enabled", True)
    token = await _token(client, member.email, "user-pw-123")
    before_turns = await session.scalar(text("SELECT COUNT(*) FROM conversation_turns"))

    response = await client.post(
        f"/artifacts/{seeded[8].id}/actions/generate_next_iteration",
        headers={**_auth(token), "Idempotency-Key": "iteration-thread-owner-key"},
        json={},
    )

    assert response.status_code == 404
    assert await session.scalar(text("SELECT COUNT(*) FROM conversation_turns")) == before_turns
    assert await session.scalar(text("SELECT COUNT(*) FROM deliverable_action_executions")) == 0
