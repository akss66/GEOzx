import importlib

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.core.security import create_access_token
from app.models import (
    Account,
    AgentInvocation,
    AgentRun,
    AgentToolCall,
    BrainTask,
    ContentItem,
    ConversationThread,
    ConversationTurn,
    Deliverable,
    Event,
    SkillRun,
    ToolExecutionAttempt,
    TurnInterrupt,
)
from app.models.enums import (
    AccountStatus,
    AgentCode,
    BrainTaskStatus,
    ContentStage,
    ContentStatus,
    DeliverableStatus,
    DeliverableType,
    Platform,
)
from app.services.turn_interrupts import request_interrupt, resolve_interrupt


@pytest.fixture(autouse=True)
def _enable_conversation_runtime(monkeypatch) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    monkeypatch.setattr(settings, "main_agent_typed_runtime_enabled", True)


def _auth(user) -> dict[str, str]:
    token = create_access_token(str(user.id), user.role.value)
    return {"Authorization": f"Bearer {token}"}


async def _runtime_context(session, admin, *, key: str, with_approval: bool = False):
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname=f"account-{key}",
        status=AccountStatus.ACTIVE,
        auth={"auth_status": "manual"},
    )
    session.add(account)
    await session.flush()
    thread = ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title=key,
    )
    session.add(thread)
    await session.flush()
    turn = ConversationTurn(
        thread_id=thread.id,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id=key,
        user_input=f"message-{key}",
        status="running",
    )
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title=f"task-{key}",
        status=BrainTaskStatus.RUNNING,
    )
    session.add_all([turn, task])
    await session.flush()
    run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        task_id=task.id,
        thread_id=thread.id,
        turn_id=turn.id,
        client_message_id=key,
        status="running",
        phase="running",
        request_payload={"message": turn.user_input},
    )
    session.add(run)
    await session.flush()
    skill = None
    tool = None
    if with_approval:
        skill = SkillRun(
            org_id=admin.org_id,
            thread_id=thread.id,
            turn_id=turn.id,
            run_id=run.id,
            task_id=task.id,
            idempotency_key=f"skill-{key}",
            skill_code="operation_iteration",
            skill_version=1,
            status="running",
            input_snapshot={},
            output_snapshot={},
        )
        session.add(skill)
        await session.flush()
        tool = AgentToolCall(
            org_id=admin.org_id,
            task_id=task.id,
            skill_run_id=skill.id,
            thread_id=thread.id,
            turn_id=turn.id,
            tool_code="publish_package_prepare",
            tool_name="Prepare manual publish package",
            idempotency_key=f"tool-{key}",
            side_effect_level="idempotent_write",
            status="waiting_approval",
            requires_human_confirmation=True,
            input_summary="private tool input",
            output_summary="package ready",
        )
        session.add(tool)
    await session.commit()
    return account, thread, turn, run, task, skill, tool


async def _finish_approval_context(session, admin, *, key: str, nested: bool = False):
    account, thread, turn, run, task, *_ = await _runtime_context(
        session,
        admin,
        key=key,
    )
    content = ContentItem(
        created_by_id=admin.id,
        account_id=account.id,
        title=f"content-{key}",
        current_stage=ContentStage.OPERATION,
        status=ContentStatus.IN_PROGRESS,
    )
    session.add(content)
    await session.flush()
    task.content_item_id = content.id
    parent = SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        task_id=task.id,
        idempotency_key=f"parent-{key}",
        skill_code="operation_iteration" if nested else "publishing_preparation",
        skill_version=1,
        status="waiting_permission",
        input_snapshot={},
        output_snapshot={},
    )
    session.add(parent)
    await session.flush()
    skill = parent
    if nested:
        skill = SkillRun(
            org_id=admin.org_id,
            thread_id=thread.id,
            turn_id=turn.id,
            run_id=run.id,
            task_id=task.id,
            idempotency_key=f"child-{key}",
            skill_code="publishing_preparation",
            skill_version=1,
            status="waiting_permission",
            input_snapshot={},
            output_snapshot={"composite_parent_skill_run_id": parent.id},
        )
        session.add(skill)
        await session.flush()
    deliverable = Deliverable(
        content_item_id=content.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        skill_run_id=skill.id,
        agent_code=AgentCode.OPERATOR.value,
        type=DeliverableType.REVIEW_REPORT,
        version=1,
        status=DeliverableStatus.PENDING_REVIEW,
        payload={"summary": "Ready for approval"},
    )
    session.add(deliverable)
    await session.flush()
    skill.output_snapshot = {
        **dict(skill.output_snapshot or {}),
        "status": "waiting_permission",
        "artifact_type": "publish_package",
        "report": {"summary": "Ready for approval"},
    }
    invocation = AgentInvocation(
        task_id=task.id,
        run_id=run.id,
        skill_run_id=skill.id,
        thread_id=thread.id,
        turn_id=turn.id,
        step_key=f"finish-{key}",
        agent_code=AgentCode.OPERATOR,
        agent_name="Operator",
    )
    session.add(invocation)
    await session.flush()
    tool = AgentToolCall(
        org_id=admin.org_id,
        task_id=task.id,
        invocation_id=invocation.id,
        skill_run_id=skill.id,
        thread_id=thread.id,
        turn_id=turn.id,
        tool_code="publish_package_prepare",
        tool_name="Prepare manual publish package",
        idempotency_key=f"finish-tool-{key}",
        side_effect_level="idempotent_write",
        status="waiting_approval",
        requires_human_confirmation=True,
        meta={"approval_stage": "before_finish", "artifact_id": deliverable.id},
    )
    session.add(tool)
    await session.flush()
    session.add(
        ToolExecutionAttempt(
            tool_call_id=tool.id,
            attempt_no=1,
            status="dispatched",
        )
    )
    run.status = "waiting_permission"
    run.phase = "waiting_permission"
    turn.status = "waiting_permission"
    task.status = BrainTaskStatus.PENDING_CONFIRMATION
    await session.commit()
    return run, turn, task, parent, skill, deliverable, tool


def test_0500_migration_parent_and_model_contract() -> None:
    migration = importlib.import_module(
        "migrations.versions.20260804_0500_turn_interrupts"
    )
    assert migration.revision == "20260804_0500"
    assert migration.down_revision == "20260804_0450"
    table = TurnInterrupt.__table__
    assert {column.name for column in table.columns} >= {
        "org_id",
        "account_id",
        "thread_id",
        "turn_id",
        "run_id",
        "skill_run_id",
        "kind",
        "status",
        "public_message",
        "response_schema",
        "semantic_key",
        "version",
        "resolution_payload",
        "resolution_hash",
        "resolution_idempotency_key",
        "resolved_by_id",
        "resolved_at",
    }
    assert {constraint.name for constraint in table.constraints} >= {
        "uq_turn_interrupts_run_semantic_key",
        "fk_turn_interrupts_thread_account_org",
        "fk_turn_interrupts_turn_thread_org",
        "fk_turn_interrupts_run_thread_turn_org",
        "fk_turn_interrupts_skill_run_scope",
    }
    lifecycle = next(
        constraint
        for constraint in table.constraints
        if constraint.name == "ck_turn_interrupts_resolution_lifecycle"
    )
    assert "resolved_by_id IS NOT NULL" not in str(lifecycle.sqltext)


@pytest.mark.asyncio
async def test_pending_unique_and_lineage(session, admin) -> None:
    account, thread, turn, run, *_ = await _runtime_context(
        session, admin, key="pending-unique"
    )
    first = TurnInterrupt(
        org_id=admin.org_id,
        account_id=account.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        kind="clarification",
        status="pending",
        public_message="Please provide the missing brief.",
        response_schema={"type": "object"},
        semantic_key="missing-brief",
    )
    second = TurnInterrupt(
        org_id=admin.org_id,
        account_id=account.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        kind="manual_pause",
        status="pending",
        public_message="Paused for operator input.",
        response_schema={"type": "object"},
        semantic_key="manual-pause",
    )
    session.add(first)
    await session.commit()
    session.add(second)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_request_clarification_interrupt_pauses_original_run(session, admin) -> None:
    account, thread, turn, run, *_ = await _runtime_context(
        session, admin, key="clarification"
    )
    result = await request_interrupt(
        session,
        user=admin,
        run_id=run.id,
        kind="clarification",
        semantic_key="missing-product-facts",
        public_message="Please provide the product price and core benefit.",
        response_schema={
            "type": "object",
            "required": ["price", "benefit"],
        },
    )
    await session.commit()

    await session.refresh(run)
    await session.refresh(turn)
    assert result.interrupt.account_id == account.id
    assert result.interrupt.thread_id == thread.id
    assert result.interrupt.turn_id == turn.id
    assert result.interrupt.run_id == run.id
    assert result.interrupt.status == "pending"
    assert result.interrupt.version == 1
    assert run.status == "waiting_user"
    assert turn.status == "waiting_user"
    assert await session.scalar(
        select(func.count(AgentRun.id)).where(AgentRun.turn_id == turn.id)
    ) == 1


@pytest.mark.asyncio
async def test_non_approval_interrupt_rejects_source_version_only(session, admin) -> None:
    _account, _thread, _turn, run, *_ = await _runtime_context(
        session, admin, key="clarification-source-version"
    )

    with pytest.raises(
        ValueError,
        match="only approval interrupts may bind a source object",
    ):
        await request_interrupt(
            session,
            user=admin,
            run_id=run.id,
            kind="clarification",
            semantic_key="missing-product-facts",
            public_message="Please provide the missing product facts.",
            response_schema={"type": "object"},
            source_version=1,
        )

    assert await session.scalar(select(func.count(TurnInterrupt.id))) == 0


@pytest.mark.asyncio
async def test_request_approval_interrupt_uses_tool_as_source_only(session, admin) -> None:
    _account, _thread, turn, run, _task, skill, tool = await _runtime_context(
        session, admin, key="approval", with_approval=True
    )
    assert skill is not None and tool is not None
    result = await request_interrupt(
        session,
        user=admin,
        run_id=run.id,
        kind="approval",
        semantic_key=f"tool-approval:{tool.id}",
        public_message="Confirm the manual publishing package.",
        action_label="Confirm publishing package",
        response_schema={"type": "boolean"},
        skill_run_id=skill.id,
        source_type="tool_call",
        source_id=tool.id,
        source_version=1,
    )
    await session.commit()

    await session.refresh(run)
    await session.refresh(turn)
    await session.refresh(tool)
    assert result.interrupt.source_type == "tool_call"
    assert result.interrupt.source_id == tool.id
    assert result.interrupt.skill_run_id == skill.id
    assert run.status == "waiting_permission"
    assert turn.status == "waiting_permission"
    assert tool.status == "waiting_approval"


@pytest.mark.asyncio
async def test_resolve_requeues_original_run_without_claim_and_enqueues_after_commit(
    client, session, admin, monkeypatch
) -> None:
    _account, _thread, _turn, run, _task, _skill, _tool = await _runtime_context(
        session, admin, key="resolve-original"
    )
    requested = await request_interrupt(
        session,
        user=admin,
        run_id=run.id,
        kind="clarification",
        semantic_key="resolve-original",
        public_message="Provide the missing product facts.",
        response_schema={"type": "object"},
    )
    await session.commit()
    enqueued: list[int] = []

    async def capture_enqueue(*, run_id: int) -> bool:
        assert session.in_transaction() is False
        enqueued.append(run_id)
        return True

    monkeypatch.setattr(
        "app.api.turn_interrupts.enqueue_agent_runtime", capture_enqueue
    )
    response = await client.post(
        f"/turn-interrupts/{requested.interrupt.id}/resolve",
        headers={**_auth(admin), "Idempotency-Key": "resolve-original-key"},
        json={
            "expected_version": 1,
            "resolution": {"benefit": "heat insulation", "price": 299},
        },
    )

    assert response.status_code == 200
    assert response.json()["run_id"] == run.id
    assert response.json()["dispatch_deferred"] is False
    assert enqueued == [run.id]
    await session.refresh(run)
    assert run.status == "queued"
    assert run.phase == "queued"
    assert run.request_payload["resume_interrupt"]["interrupt_id"] == requested.interrupt.id
    assert await session.scalar(
        select(func.count(AgentRun.id)).where(AgentRun.turn_id == run.turn_id)
    ) == 1


@pytest.mark.asyncio
async def test_resolve_is_idempotent_and_version_safe(
    client, session, admin, monkeypatch
) -> None:
    _account, _thread, _turn, run, *_ = await _runtime_context(
        session, admin, key="resolve-idempotent"
    )
    requested = await request_interrupt(
        session,
        user=admin,
        run_id=run.id,
        kind="manual_pause",
        semantic_key="resolve-idempotent",
        public_message="Paused for operator direction.",
        response_schema={"type": "object"},
    )
    await session.commit()
    dispatched: list[int] = []

    async def capture_enqueue(*, run_id: int) -> bool:
        dispatched.append(run_id)
        return True

    monkeypatch.setattr(
        "app.api.turn_interrupts.enqueue_agent_runtime", capture_enqueue
    )
    headers = {**_auth(admin), "Idempotency-Key": "same-resolution-key"}
    body = {"expected_version": 1, "resolution": {"continue": True}}
    first = await client.post(
        f"/turn-interrupts/{requested.interrupt.id}/resolve",
        headers=headers,
        json=body,
    )
    replay = await client.post(
        f"/turn-interrupts/{requested.interrupt.id}/resolve",
        headers=headers,
        json=body,
    )
    changed = await client.post(
        f"/turn-interrupts/{requested.interrupt.id}/resolve",
        headers=headers,
        json={"expected_version": 1, "resolution": {"continue": False}},
    )

    assert first.status_code == replay.status_code == 200
    assert first.json()["interrupt"] == replay.json()["interrupt"]
    assert changed.status_code == 409
    interrupt = await session.get(TurnInterrupt, requested.interrupt.id)
    assert interrupt is not None
    assert interrupt.status == "resolved"
    assert interrupt.version == 2
    assert len(interrupt.resolution_hash or "") == 64
    assert interrupt.resolution_idempotency_key == "same-resolution-key"
    assert dispatched == [run.id, run.id]


@pytest.mark.asyncio
async def test_resolve_stale_pending_version_is_conflict(session, admin) -> None:
    _account, _thread, _turn, run, *_ = await _runtime_context(
        session, admin, key="resolve-stale"
    )
    requested = await request_interrupt(
        session,
        user=admin,
        run_id=run.id,
        kind="clarification",
        semantic_key="resolve-stale",
        public_message="Need one answer.",
        response_schema={"type": "object"},
    )
    await session.commit()
    with pytest.raises(Exception) as exc_info:
        await resolve_interrupt(
            session,
            user=admin,
            interrupt_id=requested.interrupt.id,
            expected_version=7,
            idempotency_key="stale-version-key",
            resolution={"answer": "value"},
        )
    assert getattr(exc_info.value, "status_code", None) == 409


@pytest.mark.asyncio
async def test_resolve_cross_user_account_is_404(client, session, admin, member) -> None:
    _account, _thread, _turn, run, *_ = await _runtime_context(
        session, admin, key="resolve-hidden"
    )
    requested = await request_interrupt(
        session,
        user=admin,
        run_id=run.id,
        kind="clarification",
        semantic_key="resolve-hidden",
        public_message="Hidden question.",
        response_schema={"type": "object"},
    )
    await session.commit()
    response = await client.post(
        f"/turn-interrupts/{requested.interrupt.id}/resolve",
        headers={**_auth(member), "Idempotency-Key": "hidden-resolution-key"},
        json={"expected_version": 1, "resolution": {"answer": "leak"}},
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("approved", "runtime_status", "deliverable_status"),
    [
        (True, "completed", DeliverableStatus.APPROVED),
        (False, "blocked", DeliverableStatus.REJECTED),
    ],
)
@pytest.mark.asyncio
async def test_canonical_resolve_finalizes_finish_approval_without_enqueue(
    client,
    session,
    admin,
    monkeypatch,
    approved,
    runtime_status,
    deliverable_status,
) -> None:
    run, turn, _task, _parent, skill, deliverable, tool = (
        await _finish_approval_context(
            session,
            admin,
            key=f"canonical-finish-{approved}",
        )
    )
    requested = await request_interrupt(
        session,
        user=admin,
        run_id=run.id,
        kind="approval",
        semantic_key=f"tool-approval:{tool.id}",
        public_message="Confirm the publishing package.",
        response_schema={"type": "object", "required": ["approved"]},
        skill_run_id=skill.id,
        source_type="tool_call",
        source_id=tool.id,
        source_version=1,
    )
    interrupt_id = requested.interrupt.id
    await session.commit()
    enqueued: list[int] = []

    async def capture_enqueue(*, run_id: int) -> bool:
        enqueued.append(run_id)
        return True

    import app.services.turn_interrupts as interrupt_service

    original_finalize = interrupt_service.finalize_skill_finish_approval
    locked_sources: list[tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]] = []

    async def capture_finalize(*args, **kwargs):
        token = kwargs["prelocked"]
        locked_sources.append(
            (token.invocation_ids, token.tool_call_ids, token.attempt_ids)
        )
        return await original_finalize(*args, **kwargs)

    monkeypatch.setattr(
        interrupt_service,
        "finalize_skill_finish_approval",
        capture_finalize,
    )
    monkeypatch.setattr(
        "app.api.turn_interrupts.enqueue_agent_runtime",
        capture_enqueue,
    )
    response = await client.post(
        f"/turn-interrupts/{interrupt_id}/resolve",
        headers={**_auth(admin), "Idempotency-Key": f"finish-{approved}-key"},
        json={
            "expected_version": 1,
            "resolution": {"approved": approved, "comment": "Reviewed"},
        },
    )

    assert response.status_code == 200
    assert enqueued == []
    assert len(locked_sources) == 1
    assert tool.invocation_id in locked_sources[0][0]
    assert tool.id in locked_sources[0][1]
    assert locked_sources[0][2]
    for row in (run, turn, skill, deliverable, tool):
        await session.refresh(row)
    assert (run.status, turn.status, skill.status) == (
        runtime_status,
        runtime_status,
        runtime_status,
    )
    assert deliverable.status is deliverable_status
    assert tool.status == ("success" if approved else "failed")


@pytest.mark.asyncio
async def test_canonical_reject_blocks_nested_finish_without_enqueue(
    client,
    session,
    admin,
    monkeypatch,
) -> None:
    run, turn, task, parent, child, deliverable, tool = (
        await _finish_approval_context(
            session,
            admin,
            key="canonical-nested-reject",
            nested=True,
        )
    )
    requested = await request_interrupt(
        session,
        user=admin,
        run_id=run.id,
        kind="approval",
        semantic_key=f"tool-approval:{tool.id}",
        public_message="Confirm the nested publishing package.",
        response_schema={"type": "object", "required": ["approved"]},
        skill_run_id=child.id,
        source_type="tool_call",
        source_id=tool.id,
        source_version=1,
    )
    interrupt_id = requested.interrupt.id
    await session.commit()
    enqueued: list[int] = []

    async def capture_enqueue(*, run_id: int) -> bool:
        enqueued.append(run_id)
        return True

    monkeypatch.setattr(
        "app.api.turn_interrupts.enqueue_agent_runtime",
        capture_enqueue,
    )
    response = await client.post(
        f"/turn-interrupts/{interrupt_id}/resolve",
        headers={**_auth(admin), "Idempotency-Key": "nested-reject-key"},
        json={
            "expected_version": 1,
            "resolution": {"approved": False, "comment": "Reject child"},
        },
    )

    assert response.status_code == 200
    assert enqueued == []
    for row in (run, turn, task, parent, child, deliverable):
        await session.refresh(row)
    assert (run.status, turn.status, task.status) == (
        "blocked",
        "blocked",
        BrainTaskStatus.FAILED,
    )
    assert (parent.status, child.status) == ("blocked", "blocked")
    assert deliverable.status is DeliverableStatus.REJECTED


@pytest.mark.asyncio
async def test_enqueue_failure_keeps_durable_original_run_for_idempotent_redispatch(
    client, session, admin, monkeypatch
) -> None:
    _account, _thread, _turn, run, *_ = await _runtime_context(
        session, admin, key="resolve-deferred"
    )
    requested = await request_interrupt(
        session,
        user=admin,
        run_id=run.id,
        kind="clarification",
        semantic_key="resolve-deferred",
        public_message="Need one answer.",
        response_schema={"type": "object"},
    )
    await session.commit()
    attempts: list[int] = []

    async def flaky_enqueue(*, run_id: int) -> bool:
        attempts.append(run_id)
        if len(attempts) == 1:
            raise ConnectionError("queue unavailable")
        return True

    monkeypatch.setattr(
        "app.api.turn_interrupts.enqueue_agent_runtime", flaky_enqueue
    )
    headers = {**_auth(admin), "Idempotency-Key": "deferred-resolution-key"}
    body = {"expected_version": 1, "resolution": {"answer": "ready"}}
    first = await client.post(
        f"/turn-interrupts/{requested.interrupt.id}/resolve",
        headers=headers,
        json=body,
    )
    replay = await client.post(
        f"/turn-interrupts/{requested.interrupt.id}/resolve",
        headers=headers,
        json=body,
    )

    assert first.status_code == replay.status_code == 200
    assert first.json()["dispatch_deferred"] is True
    assert replay.json()["dispatch_deferred"] is False
    assert attempts == [run.id, run.id]
    await session.refresh(run)
    assert run.status == "queued"


@pytest.mark.asyncio
async def test_legacy_tool_approval_delegates_to_interrupt_resolve(
    client, session, admin, monkeypatch
) -> None:
    _account, _thread, _turn, run, _task, _skill, tool = await _runtime_context(
        session, admin, key="legacy-approval", with_approval=True
    )
    assert tool is not None
    enqueued: list[int] = []

    async def capture_enqueue(*, run_id: int) -> bool:
        enqueued.append(run_id)
        return True

    monkeypatch.setattr("app.api.brain.enqueue_agent_runtime", capture_enqueue)
    response = await client.post(
        f"/brain/tool-calls/{tool.id}/approve",
        headers=_auth(admin),
        json={"approved": True, "comment": "Proceed"},
    )

    assert response.status_code == 200
    interrupt = await session.scalar(
        select(TurnInterrupt).where(
            TurnInterrupt.run_id == run.id,
            TurnInterrupt.source_type == "tool_call",
            TurnInterrupt.source_id == tool.id,
        )
    )
    assert interrupt is not None and interrupt.status == "resolved"
    assert enqueued == [run.id]
    assert await session.scalar(
        select(func.count(AgentRun.id)).where(AgentRun.turn_id == run.turn_id)
    ) == 1


@pytest.mark.asyncio
async def test_legacy_stop_delegates_to_scoped_stop(
    client, session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run, task, *_ = await _runtime_context(
        session, admin, key="legacy-stop"
    )
    requested = await request_interrupt(
        session,
        user=admin,
        run_id=run.id,
        kind="clarification",
        semantic_key="legacy-stop",
        public_message="Waiting for input.",
        response_schema={"type": "object"},
    )
    await session.commit()
    aborted: list[int] = []

    async def capture_abort(run_id: int) -> bool:
        aborted.append(run_id)
        return True

    monkeypatch.setattr("app.api.brain.abort_agent_runtime", capture_abort)
    response = await client.post(
        f"/brain/generations/{run.client_message_id}/stop",
        headers=_auth(admin),
        json={"task_id": task.id},
    )

    assert response.status_code == 202
    await session.refresh(run)
    await session.refresh(turn)
    interrupt = await session.get(TurnInterrupt, requested.interrupt.id)
    assert interrupt is not None and interrupt.status == "cancelled"
    assert run.status == "stopped"
    assert turn.status == "stopped"
    assert aborted == [run.id]
    event_types = list(
        await session.scalars(
            select(Event.type)
            .where(Event.turn_id == turn.id)
            .order_by(Event.sequence)
        )
    )
    assert "turn.interrupt_cancelled" in event_types
    assert event_types[-1] == "turn.stopped"


@pytest.mark.asyncio
async def test_pending_interrupt_is_in_snapshot_list_and_public_event_replay(
    client,
    session,
    admin,
) -> None:
    _account, thread, _turn, run, *_ = await _runtime_context(
        session,
        admin,
        key="interrupt-snapshot",
    )
    requested = await request_interrupt(
        session,
        user=admin,
        run_id=run.id,
        kind="clarification",
        semantic_key="snapshot-question",
        public_message="Which product should this plan promote?",
        action_label="Provide product",
        response_schema={
            "type": "object",
            "properties": {"secret_prompt": {"type": "string"}},
        },
    )
    await session.commit()

    snapshot = await client.get(
        f"/brain/conversations/{thread.id}",
        headers=_auth(admin),
    )
    pending = await client.get(
        f"/brain/conversations/{thread.id}/turn-interrupts",
        headers=_auth(admin),
        params={"status": "pending"},
    )
    events = await client.get(
        f"/conversation-threads/{thread.id}/events",
        headers=_auth(admin),
        params={"after_id": 0},
    )

    assert snapshot.status_code == pending.status_code == events.status_code == 200
    snapshot_interrupt = snapshot.json()["turns"][0]["pending_interrupt"]
    assert snapshot_interrupt["id"] == requested.interrupt.id
    assert snapshot_interrupt["response_schema"] == requested.interrupt.response_schema
    assert [row["id"] for row in pending.json()] == [requested.interrupt.id]
    requested_event = next(
        row for row in events.json()["data"] if row["type"] == "turn.interrupt_requested"
    )
    assert requested_event["payload"] == {
        "interrupt_id": requested.interrupt.id,
        "kind": "clarification",
        "status": "pending",
        "message": "Which product should this plan promote?",
        "action_label": "Provide product",
        "version": 1,
    }
    assert "response_schema" not in requested_event["payload"]
    assert "secret_prompt" not in str(requested_event["payload"])


@pytest.mark.asyncio
async def test_resolve_projects_no_pending_interrupt_and_safe_events(
    client,
    session,
    admin,
    monkeypatch,
) -> None:
    _account, thread, _turn, run, *_ = await _runtime_context(
        session,
        admin,
        key="interrupt-resolved-events",
    )
    requested = await request_interrupt(
        session,
        user=admin,
        run_id=run.id,
        kind="manual_pause",
        semantic_key="operator-pause",
        public_message="Paused for operator direction.",
        response_schema={"type": "object"},
    )
    interrupt_id = requested.interrupt.id
    await session.commit()

    async def capture_enqueue(*, run_id: int) -> bool:
        return run_id == run.id

    monkeypatch.setattr(
        "app.api.turn_interrupts.enqueue_agent_runtime",
        capture_enqueue,
    )
    resolved = await client.post(
        f"/turn-interrupts/{interrupt_id}/resolve",
        headers={**_auth(admin), "Idempotency-Key": "safe-events-key"},
        json={
            "expected_version": 1,
            "resolution": {"private_direction": "Do not expose this"},
        },
    )
    snapshot = await client.get(
        f"/brain/conversations/{thread.id}",
        headers=_auth(admin),
    )
    events = await client.get(
        f"/conversation-threads/{thread.id}/events",
        headers=_auth(admin),
        params={"after_id": 0},
    )

    assert resolved.status_code == snapshot.status_code == events.status_code == 200
    assert snapshot.json()["turns"][0]["pending_interrupt"] is None
    public_events = {
        row["type"]: row["payload"]
        for row in events.json()["data"]
        if row["type"] in {"turn.interrupt_resolved", "turn.resuming"}
    }
    assert set(public_events) == {"turn.interrupt_resolved", "turn.resuming"}
    assert "private_direction" not in str(public_events)
    assert "resolution" not in str(public_events)


@pytest.mark.asyncio
async def test_pending_interrupt_blocks_thread_delete_even_if_runtime_is_terminal(
    client,
    session,
    admin,
) -> None:
    _account, thread, turn, run, task, *_ = await _runtime_context(
        session,
        admin,
        key="interrupt-delete-pending",
    )
    requested = await request_interrupt(
        session,
        user=admin,
        run_id=run.id,
        kind="clarification",
        semantic_key="delete-pending",
        public_message="Need one answer.",
        response_schema={"type": "object"},
    )
    interrupt_id = requested.interrupt.id
    run.status = "completed"
    run.phase = "completed"
    turn.status = "completed"
    task.status = BrainTaskStatus.COMPLETED
    await session.commit()

    response = await client.delete(
        f"/brain/conversations/{thread.id}",
        headers=_auth(admin),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CONVERSATION_DELETE_BLOCKED"
    assert await session.get(TurnInterrupt, interrupt_id) is not None


@pytest.mark.asyncio
async def test_terminal_interrupt_is_deleted_with_private_runtime_trace(
    client,
    session,
    admin,
    monkeypatch,
) -> None:
    _account, thread, turn, run, task, *_ = await _runtime_context(
        session,
        admin,
        key="interrupt-delete-terminal",
    )
    requested = await request_interrupt(
        session,
        user=admin,
        run_id=run.id,
        kind="clarification",
        semantic_key="delete-terminal",
        public_message="Need one answer.",
        response_schema={"type": "object"},
    )
    interrupt_id = requested.interrupt.id
    await session.commit()

    async def capture_enqueue(*, run_id: int) -> bool:
        return True

    monkeypatch.setattr(
        "app.api.turn_interrupts.enqueue_agent_runtime",
        capture_enqueue,
    )
    resolved = await client.post(
        f"/turn-interrupts/{interrupt_id}/resolve",
        headers={**_auth(admin), "Idempotency-Key": "delete-terminal-key"},
        json={"expected_version": 1, "resolution": {"answer": "ready"}},
    )
    assert resolved.status_code == 200
    run.status = "completed"
    run.phase = "completed"
    turn.status = "completed"
    task.status = BrainTaskStatus.COMPLETED
    await session.commit()

    response = await client.delete(
        f"/brain/conversations/{thread.id}",
        headers=_auth(admin),
    )

    assert response.status_code == 200
    assert response.json()["interrupts_deleted"] == 1
    assert await session.get(TurnInterrupt, interrupt_id) is None
    assert await session.scalar(
        select(func.count(Event.id)).where(Event.thread_id == thread.id)
    ) == 0
