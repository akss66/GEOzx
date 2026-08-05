"""Route-specific execution contracts for one main-Agent conversation Turn."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

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
    RunRevision,
    SkillRun,
    StrategyPlan,
    ToolExecutionAttempt,
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
from app.orchestrator.brain_runtime import runtime_graph
from app.orchestrator.checkpoint_graph_contracts import require_checkpoint_graph_contract
from app.orchestrator.skill_runtime import SkillRuntime, skill_input_hash
from app.orchestrator.skills.registry import SkillRegistry, skill_registry
from app.orchestrator.tool_executor import DurableToolExecutor
from app.schemas.attachment import AttachmentContext
from app.schemas.brain import RuntimeToolCall
from app.schemas.conversation import (
    CreateConversationTurnRequest,
    TurnExecutionMode,
    TurnExecutionResult,
    TurnRouteDecision,
)
from app.schemas.run_revision import (
    FullRecompute,
    ManualReconciliation,
    PartialExecution,
    ResolvedStageOutput,
    StageDataEnvelope,
    StageReuseBinding,
)
from app.services.agent_runs import (
    acquire_agent_run,
    cancel_agent_run,
    complete_agent_run,
    request_agent_run_cancel,
)
from app.services.runtime_locking import (
    RuntimeForestLock,
    RuntimeLockConflict,
    RuntimeRootLock,
    lock_runtime_root_forest,
    lock_runtime_root_scope,
    require_runtime_forest_lock,
    require_runtime_root_lock,
)
from app.services.runtime_state import (
    RuntimeStateScope,
    close_runtime_state,
)
from app.services.turn_events import TurnEventScope, append_turn_event
from app.services.turn_execution import execute_conversation_turn, execute_revision_task_run
from app.tools import ToolAdapter, ToolExecutionContext, ToolSpec


async def _turn_context(session, admin, *, key: str):
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
    )
    session.add(turn)
    await session.flush()
    run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        thread_id=thread.id,
        turn_id=turn.id,
        client_message_id=key,
        status="claimed",
        request_payload={},
    )
    session.add(run)
    await session.commit()
    return account, thread, turn, run


async def _four_ledger_context(session, admin, *, key: str):
    account, thread, turn, run = await _turn_context(session, admin, key=key)
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title=f"task-{key}",
        status=BrainTaskStatus.RUNNING,
        progress=17,
        current_focus="running",
    )
    session.add(task)
    await session.flush()
    run.task_id = task.id
    skill_run = SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        task_id=task.id,
        idempotency_key=f"runtime-state:{key}",
        skill_code="account_inspection",
        skill_version=1,
        status="running",
        input_snapshot={},
        output_snapshot={},
    )
    session.add(skill_run)
    await session.commit()
    return account, thread, turn, run, task, skill_run


async def _revision_execution_context(session, admin, *, key: str):
    account, thread, source_turn, source_run, task, source_skill = await _four_ledger_context(
        session, admin, key=key
    )
    source_skill.skill_code = "operation_iteration"
    source_skill.status = "completed"
    revision_turn = ConversationTurn(
        thread_id=thread.id,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id=f"revision-turn-{key}",
        user_input="补充脚本时长",
        target_turn_id=source_turn.id,
        steering_mode="supplement",
    )
    session.add(revision_turn)
    await session.flush()
    revision_run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        task_id=task.id,
        thread_id=thread.id,
        turn_id=revision_turn.id,
        client_message_id=f"revision-run-{key}",
        status="queued",
        phase="queued",
        request_payload={"operation": "execute_revision", "task_id": task.id},
    )
    session.add(revision_run)
    await session.flush()
    revision_skill = SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=revision_turn.id,
        run_id=revision_run.id,
        task_id=task.id,
        idempotency_key=f"revision-skill-{key}",
        skill_code="operation_iteration",
        skill_version=1,
        status="running",
        input_snapshot={},
        output_snapshot={},
    )
    session.add(revision_skill)
    await session.flush()
    contract = require_checkpoint_graph_contract("operation_iteration", 1)
    revision = RunRevision(
        org_id=admin.org_id,
        account_id=account.id,
        thread_id=thread.id,
        task_id=task.id,
        source_turn_id=source_turn.id,
        source_run_id=source_run.id,
        source_skill_run_id=source_skill.id,
        revision_turn_id=revision_turn.id,
        revision_run_id=revision_run.id,
        revision_skill_run_id=revision_skill.id,
        mode="full_recompute",
        status="planned",
        dependency_graph_version=contract.graph_version,
        earliest_affected_step=contract.steps[0].key,
        changed_constraints={"unknown_constraint": {"operation": "changed"}},
        direct_affected_steps=[],
        affected_steps=[step.key for step in contract.steps],
        reused_steps=[],
        plan_hash="a" * 64,
        fallback_reason="unknown_constraint",
    )
    session.add(revision)
    await session.commit()
    revision_run.request_payload = {
        **revision_run.request_payload,
        "revision_id": revision.id,
        "revision_skill_run_id": revision_skill.id,
    }
    await session.commit()
    return account, thread, revision_turn, revision_run, task, revision_skill, revision


async def _real_revision_runtime_context(session, admin, monkeypatch, *, key: str):
    """Bind a revision to the real SkillRuntime with only external seams faked."""

    from tests.test_operating_skills import _Harness, _Tools

    account, thread, turn, run, task, skill, revision = (
        await _revision_execution_context(session, admin, key=key)
    )
    content = ContentItem(
        account_id=account.id,
        created_by_id=admin.id,
        title=f"revision-content-{key}",
        current_stage=ContentStage.OPERATION,
        status=ContentStatus.IN_PROGRESS,
    )
    session.add(content)
    await session.flush()
    review = Deliverable(
        content_item_id=content.id,
        agent_code=AgentCode.OPERATOR.value,
        type=DeliverableType.REVIEW_REPORT,
        version=1,
        status=DeliverableStatus.APPROVED,
        payload={"summary": "approved revision source"},
    )
    session.add(review)
    await session.flush()
    task.content_item_id = content.id
    frozen_input = {
        "account_id": account.id,
        "confirmed_review_artifact_id": review.id,
        "cycle_days": 7,
        "topic_count": 4,
        "script_duration_seconds": 30,
        "positioning_artifact_id": None,
        "constraints": [],
    }
    skill.input_snapshot = frozen_input
    skill.input_hash = skill_input_hash(frozen_input)
    await session.commit()

    tools = _Tools()
    harness = _Harness()

    class PassingCritic:
        async def review(self, **_kwargs):
            return SimpleNamespace(passed=True, score=95)

    runtime = SkillRuntime(
        tool_executor=tools,
        harness=harness,
        critic=PassingCritic(),
    )
    monkeypatch.setattr("app.services.turn_execution.skill_runtime", runtime)
    return account, thread, turn, run, task, skill, revision, runtime, tools, harness


async def _real_runtime_counts(session, *, run_id: int, tools, harness) -> dict[str, int]:
    tool_calls = tools.calls if isinstance(tools.calls, int) else len(tools.calls)
    return {
        "provider": len(harness.calls),
        "tool": tool_calls,
        "expert": int(
            await session.scalar(
                select(func.count(AgentInvocation.id)).where(
                    AgentInvocation.run_id == run_id
                )
            )
            or 0
        ),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("run_status", "revision_status"),
    [("queued", "planned"), ("waiting_predecessor", "waiting_predecessor")],
)
async def test_revision_pre_acquire_cancel_converges_three_ledgers_without_completion_event(
    session, admin, run_status: str, revision_status: str
) -> None:
    _account, _thread, _turn, run, _task, skill, revision = (
        await _revision_execution_context(
            session, admin, key=f"cancel-before-acquire-{run_status}"
        )
    )
    run.status = run_status
    run.phase = run_status
    revision.status = revision_status
    await session.commit()
    await request_agent_run_cancel(session, run.id)

    assert await acquire_agent_run(
        session,
        run.id,
        worker_id="cancel-pre-acquire-worker",
        lease_seconds=60,
    ) is None
    await session.refresh(run)
    await session.refresh(skill)
    await session.refresh(revision)
    first_finished_at = revision.finished_at
    assert (run.status, skill.status, revision.status) == (
        "cancelled",
        "cancelled",
        "cancelled",
    )
    assert first_finished_at is not None
    assert await session.scalar(
        select(func.count(Event.id)).where(
            Event.run_id == run.id,
            Event.type == "run.revision_completed",
        )
    ) == 0

    assert await acquire_agent_run(
        session,
        run.id,
        worker_id="cancel-pre-acquire-worker",
        lease_seconds=60,
    ) is None
    await session.refresh(revision)
    assert revision.status == "cancelled"
    assert revision.finished_at == first_finished_at


@pytest.mark.asyncio
async def test_running_revision_cancelled_error_hook_is_idempotent_and_never_completes(
    session, admin
) -> None:
    _account, _thread, _turn, run, _task, skill, revision = (
        await _revision_execution_context(session, admin, key="running-cancelled-error")
    )
    now = datetime.now(UTC)
    run.status = "running"
    run.phase = "running"
    run.started_at = now
    skill.status = "running"
    skill.started_at = now
    revision.status = "running"
    revision.started_at = now
    child = SkillRun(
        org_id=run.org_id,
        thread_id=run.thread_id,
        turn_id=run.turn_id,
        run_id=run.id,
        task_id=run.task_id,
        idempotency_key="cancel-active-child",
        skill_code="topic_planning",
        skill_version=1,
        status="running",
        input_snapshot={"account_id": revision.account_id, "days": 7, "topic_count": 4},
        output_snapshot={},
    )
    session.add(child)
    completed_child_output = {
        "status": "completed",
        "report": {"summary": "durable child fact"},
        "composite_parent_skill_run_id": skill.id,
    }
    completed_child = SkillRun(
        org_id=run.org_id,
        thread_id=run.thread_id,
        turn_id=run.turn_id,
        run_id=run.id,
        task_id=run.task_id,
        idempotency_key="cancel-completed-child",
        skill_code="script_generation",
        skill_version=1,
        status="completed",
        input_snapshot={"account_id": revision.account_id},
        output_snapshot=completed_child_output,
    )
    session.add(completed_child)
    await session.flush()
    completed_receipt = AgentToolCall(
        org_id=run.org_id,
        task_id=run.task_id,
        skill_run_id=completed_child.id,
        thread_id=run.thread_id,
        turn_id=run.turn_id,
        tool_code="durable-child-receipt",
        tool_name="Durable child receipt",
        idempotency_key="cancel-completed-child-receipt",
        side_effect_level="read",
        status="completed",
        input_summary="safe",
        output_summary="safe",
    )
    session.add(completed_receipt)
    await session.commit()

    await cancel_agent_run(session, run.id)
    await session.refresh(run)
    await session.refresh(skill)
    await session.refresh(child)
    await session.refresh(completed_child)
    await session.refresh(completed_receipt)
    await session.refresh(revision)
    first_finished_at = revision.finished_at
    assert (run.status, skill.status, child.status, revision.status) == (
        "cancelled",
        "cancelled",
        "cancelled",
        "cancelled",
    )
    assert first_finished_at is not None
    assert completed_child.status == "completed"
    assert completed_child.output_snapshot == completed_child_output
    assert completed_receipt.status == "completed"

    await cancel_agent_run(session, run.id)
    await session.refresh(revision)
    assert revision.finished_at == first_finished_at
    assert await session.scalar(
        select(func.count(Event.id)).where(
            Event.run_id == run.id,
            Event.type == "run.revision_completed",
        )
    ) == 0


@pytest.mark.asyncio
async def test_all_terminal_revision_cancel_never_rebinds_completed_skill_or_receipt(
    session, admin
) -> None:
    _account, _thread, _turn, run, task, root, revision = (
        await _revision_execution_context(session, admin, key="all-terminal-cancel")
    )
    content = ContentItem(
        created_by_id=admin.id,
        title="all-terminal-cancel-content",
    )
    session.add(content)
    await session.flush()
    task.content_item_id = content.id
    root_snapshot = {"status": "completed", "report": {"fact": "root immutable"}}
    root.status = "completed"
    root.output_snapshot = root_snapshot
    child_snapshot = {
        "status": "completed",
        "report": {"fact": "child immutable"},
        "composite_parent_skill_run_id": root.id,
    }
    child = SkillRun(
        org_id=run.org_id,
        thread_id=run.thread_id,
        turn_id=run.turn_id,
        run_id=run.id,
        task_id=run.task_id,
        idempotency_key="all-terminal-child",
        skill_code="script_generation",
        skill_version=1,
        status="completed",
        input_snapshot={},
        output_snapshot=child_snapshot,
    )
    session.add(child)
    await session.flush()
    receipt = AgentToolCall(
        org_id=run.org_id,
        task_id=run.task_id,
        skill_run_id=child.id,
        thread_id=run.thread_id,
        turn_id=run.turn_id,
        tool_code="terminal-receipt",
        tool_name="Terminal receipt",
        idempotency_key="all-terminal-receipt",
        side_effect_level="read",
        status="completed",
    )
    session.add(receipt)
    run.status = "running"
    run.phase = "running"
    revision.status = "completed"
    revision.finished_at = datetime.now(UTC)
    await session.commit()

    await cancel_agent_run(session, run.id)
    await cancel_agent_run(session, run.id)
    await session.refresh(root)
    await session.refresh(child)
    await session.refresh(receipt)
    await session.refresh(revision)

    assert run.status == "cancelled"
    assert (root.status, root.output_snapshot) == ("completed", root_snapshot)
    assert (child.status, child.output_snapshot) == ("completed", child_snapshot)
    assert receipt.status == "completed"
    assert revision.status == "completed"


@pytest.mark.asyncio
async def test_runtime_root_lock_token_is_unforgeable_and_transaction_bound(
    session, admin
) -> None:
    _account, _thread, turn, run, task, skill = await _four_ledger_context(
        session, admin, key="runtime-lock-token"
    )

    with pytest.raises(TypeError, match="created only by the lock helper"):
        RuntimeRootLock(
            _seal=object(),
            session_identity=id(session.sync_session),
            transaction_identity=0,
            run_id=run.id,
            turn_id=turn.id,
            task_id=task.id,
            content_item_id=None,
            root_skill_run_id=skill.id,
            child_skill_run_ids=(),
            run_revision_ids=(),
            deliverable_ids=(),
            invocation_ids=(),
            tool_call_ids=(),
            attempt_ids=(),
        )

    token = await lock_runtime_root_scope(
        session,
        run_id=run.id,
        expected_turn_id=turn.id,
        expected_task_id=task.id,
        root_skill_run_id=skill.id,
    )
    require_runtime_root_lock(
        session,
        token,
        run_id=run.id,
        turn_id=turn.id,
        task_id=task.id,
        skill_run_id=skill.id,
    )
    with pytest.raises(RuntimeLockConflict, match="Deliverable was not prelocked"):
        require_runtime_root_lock(
            session,
            token,
            run_id=run.id,
            deliverable_id=999_999,
        )

    maker = async_sessionmaker(session.bind, expire_on_commit=False)
    async with maker() as other_session:
        await other_session.execute(select(AgentRun.id).where(AgentRun.id == run.id))
        with pytest.raises(RuntimeLockConflict, match="does not belong to this session"):
            require_runtime_root_lock(other_session, token, run_id=run.id)

    await session.commit()
    with pytest.raises(RuntimeLockConflict, match="active transaction"):
        require_runtime_root_lock(session, token, run_id=run.id)
    await session.execute(select(AgentRun.id).where(AgentRun.id == run.id))
    with pytest.raises(RuntimeLockConflict, match="another transaction"):
        require_runtime_root_lock(session, token, run_id=run.id)


@pytest.mark.asyncio
async def test_runtime_lock_requires_revision_invocation_tool_and_attempt_subsets(
    session, admin
) -> None:
    _account, _thread, _turn, run, task, skill = await _four_ledger_context(
        session, admin, key="runtime-lock-subsets"
    )
    invocation = AgentInvocation(
        task_id=task.id,
        run_id=run.id,
        skill_run_id=skill.id,
        thread_id=run.thread_id,
        turn_id=run.turn_id,
        step_key="subset-invocation",
        agent_code=AgentCode.OPERATOR,
        agent_name="Operator",
    )
    session.add(invocation)
    await session.flush()
    tool_call = AgentToolCall(
        org_id=run.org_id,
        task_id=task.id,
        invocation_id=invocation.id,
        skill_run_id=skill.id,
        thread_id=run.thread_id,
        turn_id=run.turn_id,
        tool_code="subset.tool",
        tool_name="Subset tool",
        status="running",
        side_effect_level="read",
    )
    session.add(tool_call)
    await session.flush()
    attempt = ToolExecutionAttempt(
        tool_call_id=tool_call.id,
        attempt_no=1,
        status="dispatched",
    )
    session.add(attempt)
    await session.commit()

    token = await lock_runtime_root_scope(
        session,
        run_id=run.id,
        expected_turn_id=run.turn_id,
        expected_task_id=task.id,
        root_skill_run_id=skill.id,
        invocation_ids=(invocation.id,),
        tool_call_ids=(tool_call.id,),
        attempt_ids=(attempt.id,),
    )
    require_runtime_root_lock(
        session,
        token,
        run_id=run.id,
        invocation_ids=(invocation.id,),
        tool_call_ids=(tool_call.id,),
        attempt_ids=(attempt.id,),
    )
    for keyword, expected in (
        ("run_revision_ids", "RunRevision"),
        ("invocation_ids", "AgentInvocation"),
        ("tool_call_ids", "AgentToolCall"),
        ("attempt_ids", "ToolExecutionAttempt"),
    ):
        with pytest.raises(RuntimeLockConflict, match=f"{expected} was not prelocked"):
            require_runtime_root_lock(
                session,
                token,
                run_id=run.id,
                **{keyword: (999_999,)},
            )


@pytest.mark.asyncio
async def test_runtime_forest_expands_both_run_revision_endpoints(
    session, admin
) -> None:
    (
        _account,
        _thread,
        _turn,
        revision_run,
        _task,
        _skill,
        revision,
    ) = await _revision_execution_context(
        session, admin, key="runtime-forest-revision-endpoints"
    )

    tokens = await lock_runtime_root_forest(
        session,
        run_ids=(revision.source_run_id,),
    )

    assert tuple(token.run_id for token in tokens) == (
        revision.source_run_id,
        revision_run.id,
    )
    assert all(revision.id in token.run_revision_ids for token in tokens)


@pytest.mark.asyncio
async def test_runtime_forest_locks_task_and_deliverable_content_before_deliverables(
    session, admin, monkeypatch
) -> None:
    account, thread, turn, run, task, _skill = await _four_ledger_context(
        session, admin, key="runtime-forest-deliverable-content"
    )
    task_content = ContentItem(
        account_id=account.id,
        created_by_id=admin.id,
        title="Task content",
    )
    deliverable_content = ContentItem(
        account_id=account.id,
        created_by_id=admin.id,
        title="Deliverable content",
    )
    session.add_all([task_content, deliverable_content])
    await session.flush()
    task.content_item_id = task_content.id
    deliverable = Deliverable(
        content_item_id=deliverable_content.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        agent_code="forest-content-order",
        type=DeliverableType.VIDEO_SCRIPT,
        payload={"title": "Forest content order"},
    )
    session.add(deliverable)
    await session.commit()
    import app.services.runtime_locking as locking_module

    real_lock_rows = locking_module._lock_rows
    locked_families = []

    async def observe_family(lock_session, model, row_ids):
        if row_ids:
            locked_families.append((model, tuple(row_ids)))
        return await real_lock_rows(lock_session, model, row_ids)

    monkeypatch.setattr(locking_module, "_lock_rows", observe_family)

    (token,) = await lock_runtime_root_forest(
        session,
        run_ids=(run.id,),
        extra_deliverable_ids=(deliverable.id,),
    )

    assert token.content_item_id == task_content.id
    assert token.content_item_ids == tuple(
        sorted((task_content.id, deliverable_content.id))
    )
    content_index = next(
        index
        for index, (model, _ids) in enumerate(locked_families)
        if model is ContentItem
    )
    deliverable_index = next(
        index
        for index, (model, _ids) in enumerate(locked_families)
        if model is Deliverable
    )
    assert content_index < deliverable_index
    require_runtime_root_lock(
        session,
        token,
        run_id=run.id,
        content_item_ids=(task_content.id, deliverable_content.id),
    )
    with pytest.raises(RuntimeLockConflict, match="ContentItem was not prelocked"):
        require_runtime_root_lock(
            session,
            token,
            run_id=run.id,
            content_item_ids=(999_999,),
        )


@pytest.mark.asyncio
async def test_runtime_forest_proof_binds_runless_extras_to_session_and_transaction(
    session, admin
) -> None:
    account, _thread, _turn, run, _task, _skill = await _four_ledger_context(
        session, admin, key="runtime-forest-proof"
    )
    content = ContentItem(
        account_id=account.id,
        created_by_id=admin.id,
        title="Runless proof content",
    )
    session.add(content)
    await session.flush()
    runless = Deliverable(
        content_item_id=content.id,
        agent_code="runless-proof",
        type=DeliverableType.VIDEO_SCRIPT,
        payload={"title": "Runless proof"},
    )
    session.add(runless)
    await session.commit()

    with pytest.raises(TypeError, match="created only by the lock helper"):
        RuntimeForestLock(
            _seal=object(),
            session_identity=id(session.sync_session),
            transaction_identity=object(),
            run_tokens=(),
        )

    forest = await lock_runtime_root_forest(
        session,
        run_ids=(run.id,),
        extra_deliverable_ids=(runless.id,),
        allow_runless_extras=True,
    )

    assert isinstance(forest, RuntimeForestLock)
    (run_token,) = forest
    assert runless.id not in run_token.deliverable_ids
    assert content.id not in run_token.content_item_ids
    assert forest.extra_deliverable_ids == (runless.id,)
    assert forest.extra_content_item_ids == (content.id,)
    require_runtime_forest_lock(
        session,
        forest,
        run_ids=(run.id,),
        extra_deliverable_ids=(runless.id,),
        extra_content_item_ids=(content.id,),
    )
    with pytest.raises(RuntimeLockConflict, match="extra Deliverable was not prelocked"):
        require_runtime_forest_lock(
            session,
            forest,
            run_ids=(run.id,),
            extra_deliverable_ids=(999_999,),
        )
    with pytest.raises(RuntimeLockConflict, match="extra ContentItem was not prelocked"):
        require_runtime_forest_lock(
            session,
            forest,
            run_ids=(run.id,),
            extra_content_item_ids=(999_999,),
        )

    maker = async_sessionmaker(session.bind, expire_on_commit=False)
    async with maker() as other_session:
        await other_session.execute(select(AgentRun.id).where(AgentRun.id == run.id))
        with pytest.raises(RuntimeLockConflict, match="does not belong to this session"):
            require_runtime_forest_lock(
                other_session,
                forest,
                run_ids=(run.id,),
            )

    await session.commit()
    await session.execute(select(AgentRun.id).where(AgentRun.id == run.id))
    with pytest.raises(RuntimeLockConflict, match="another transaction"):
        require_runtime_forest_lock(session, forest, run_ids=(run.id,))


@pytest.mark.asyncio
async def test_runtime_lock_rejects_tool_via_unlocked_invocation(
    session, admin
) -> None:
    _account, thread, turn, run, task, skill = await _four_ledger_context(
        session, admin, key="runtime-lock-cross-run-tool"
    )
    unlocked_invocation = AgentInvocation(
        task_id=task.id,
        run_id=run.id,
        thread_id=thread.id,
        turn_id=turn.id,
        step_key="unlocked-invocation",
        agent_code=AgentCode.OPERATOR,
        agent_name="Unlocked operator",
    )
    session.add(unlocked_invocation)
    await session.flush()
    cross_run_tool = AgentToolCall(
        org_id=run.org_id,
        task_id=task.id,
        invocation_id=unlocked_invocation.id,
        thread_id=thread.id,
        turn_id=turn.id,
        tool_code="cross.run.tool",
        tool_name="Cross-run tool",
        status="running",
        side_effect_level="read",
    )
    session.add(cross_run_tool)
    await session.commit()

    with pytest.raises(RuntimeLockConflict, match="AgentToolCall lineage mismatch"):
        await lock_runtime_root_scope(
            session,
            run_id=run.id,
            expected_turn_id=run.turn_id,
            expected_task_id=task.id,
            root_skill_run_id=skill.id,
            tool_call_ids=(cross_run_tool.id,),
        )


@pytest.mark.asyncio
async def test_runtime_forest_rejects_cross_run_extra_deliverables(session, admin) -> None:
    account, _thread, _turn, run, _task, _skill = await _four_ledger_context(
        session, admin, key="runtime-forest-extra-deliverable"
    )
    content = ContentItem(
        account_id=account.id,
        created_by_id=admin.id,
        title="cross-forest-deliverable",
    )
    session.add(content)
    await session.flush()
    deliverable = Deliverable(
        content_item_id=content.id,
        agent_code="02-content",
        type=DeliverableType.VIDEO_SCRIPT,
        payload={},
    )
    session.add(deliverable)
    await session.commit()

    with pytest.raises(RuntimeLockConflict, match="Deliverable lineage mismatch"):
        await lock_runtime_root_forest(
            session,
            run_ids=(run.id,),
            extra_deliverable_ids=(deliverable.id,),
        )


@pytest.mark.asyncio
async def test_runtime_lock_extension_rejects_preexisting_unlocked_rows(
    session, admin
) -> None:
    _account, _thread, _turn, run, task, root = await _four_ledger_context(
        session, admin, key="runtime-lock-extension-existing"
    )
    child = SkillRun(
        org_id=run.org_id,
        thread_id=run.thread_id,
        turn_id=run.turn_id,
        run_id=run.id,
        task_id=task.id,
        idempotency_key="runtime-lock-extension-existing-child",
        skill_code="script_generation",
        skill_version=1,
        status="running",
        input_snapshot={},
        output_snapshot={"composite_parent_skill_run_id": root.id},
    )
    session.add(child)
    await session.commit()
    token = await lock_runtime_root_scope(
        session,
        run_id=run.id,
        expected_turn_id=run.turn_id,
        expected_task_id=task.id,
        root_skill_run_id=root.id,
    )
    import app.services.runtime_locking as runtime_locking_module

    with pytest.raises(RuntimeLockConflict, match="inserted in the current transaction"):
        await runtime_locking_module.extend_runtime_root_lock(
            session,
            token,
            task=task,
            content=None,
            skill_run=child,
        )


@pytest.mark.asyncio
async def test_runtime_lock_extension_rejects_rows_pending_before_gate(
    session, admin
) -> None:
    _account, _thread, _turn, run, task, root = await _four_ledger_context(
        session, admin, key="runtime-lock-extension-pregate"
    )
    child = SkillRun(
        org_id=run.org_id,
        thread_id=run.thread_id,
        turn_id=run.turn_id,
        run_id=run.id,
        task_id=task.id,
        idempotency_key="runtime-lock-extension-pregate-child",
        skill_code="script_generation",
        skill_version=1,
        status="running",
        input_snapshot={},
        output_snapshot={"composite_parent_skill_run_id": root.id},
    )
    session.add(child)
    with session.no_autoflush:
        token = await lock_runtime_root_scope(
            session,
            run_id=run.id,
            expected_turn_id=run.turn_id,
            expected_task_id=task.id,
            root_skill_run_id=root.id,
        )
    import app.services.runtime_locking as runtime_locking_module

    with pytest.raises(RuntimeLockConflict, match="after root lock acquisition"):
        await runtime_locking_module.extend_runtime_root_lock(
            session,
            token,
            task=task,
            content=None,
            skill_run=child,
        )


@pytest.mark.asyncio
async def test_close_runtime_state_locks_run_before_runtime_rows(session, admin) -> None:
    account, thread, turn, run, task, skill = await _four_ledger_context(
        session, admin, key="run-first-close"
    )
    content = ContentItem(
        account_id=account.id,
        created_by_id=admin.id,
        title="run-first-close",
    )
    session.add(content)
    await session.flush()
    task.content_item_id = content.id
    await session.commit()
    locked_tables: list[str] = []

    def capture_for_update(_conn, clauseelement, *_args, **_kwargs) -> None:
        if getattr(clauseelement, "_for_update_arg", None) is not None:
            sql = str(clauseelement)
            for table in (
                "content_items",
                "agent_runs",
                "conversation_turns",
                "brain_tasks",
                "skill_runs",
            ):
                if f"FROM {table}" in sql:
                    locked_tables.append(table)
                    break

    sqlalchemy_event.listen(session.bind.sync_engine, "before_execute", capture_for_update)
    try:
        await close_runtime_state(
            session,
            scope=RuntimeStateScope(
                run_id=run.id,
                org_id=admin.org_id,
                account_id=account.id,
                thread_id=thread.id,
                turn_id=turn.id,
                task_id=task.id,
                skill_run_id=skill.id,
                content_item_id=content.id,
            ),
            status="retry_wait",
            message="retry later",
        )
    finally:
        sqlalchemy_event.remove(
            session.bind.sync_engine, "before_execute", capture_for_update
        )

    assert locked_tables[:5] == [
        "agent_runs",
        "conversation_turns",
        "brain_tasks",
        "content_items",
        "skill_runs",
    ]


@pytest.mark.asyncio
async def test_caller_owned_runtime_closure_publishes_only_after_outer_commit(
    session, admin, monkeypatch
) -> None:
    from app.services.runtime_state import (
        RuntimePublishIntent,
        publish_runtime_state_closure,
        publish_runtime_state_intents,
    )

    account, thread, turn, run, task, skill = await _four_ledger_context(
        session, admin, key="caller-owned-publish"
    )
    published: list[int] = []

    async def capture_publish(_event_type, _payload, **kwargs) -> None:
        published.append(kwargs["event_id"])

    monkeypatch.setattr(
        "app.orchestrator.brain_runtime.publish_realtime_event", capture_publish
    )
    closure = await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            run_id=run.id,
            org_id=admin.org_id,
            account_id=account.id,
            thread_id=thread.id,
            turn_id=turn.id,
            task_id=task.id,
            skill_run_id=skill.id,
        ),
        status="blocked",
        message="nested child rejected",
        error_code="SKILL_APPROVAL_REJECTED",
        commit=False,
    )
    assert published == []
    assert closure.publish_intents
    await session.commit()
    await publish_runtime_state_closure(session, closure)
    assert published == [intent.event_id for intent in closure.publish_intents]
    first = closure.publish_intents[0]
    await publish_runtime_state_intents(
        session,
        (
            RuntimePublishIntent(
                event_id=first.event_id,
                event_type="brain.runtime.mismatched",
                turn_id=first.turn_id,
            ),
            RuntimePublishIntent(
                event_id=first.event_id,
                event_type=first.event_type,
                turn_id=(first.turn_id or 0) + 1,
            ),
        ),
    )
    assert published == [intent.event_id for intent in closure.publish_intents]


@pytest.mark.asyncio
async def test_revision_barrier_precedes_external_and_terminal_retry_is_exactly_once(
    session, admin, monkeypatch
) -> None:
    _account, _thread, _turn, run, task, _skill, revision = await _revision_execution_context(
        session, admin, key="barrier-order"
    )
    calls: list[str] = []
    original_commit = session.commit

    async def barrier(*_args, **_kwargs):
        calls.append("barrier")
        return FullRecompute(
            reason="unknown_constraint",
            execute_steps=tuple(revision.affected_steps),
            plan_hash=revision.plan_hash,
        )

    async def track_commit():
        calls.append("commit")
        await original_commit()

    async def execute(*_args, **_kwargs):
        calls.append("external")
        return SimpleNamespace(status="completed", response="revision completed")

    monkeypatch.setattr(
        "app.services.turn_execution.prepare_revision_execution", barrier, raising=False
    )
    monkeypatch.setattr("app.services.turn_execution.skill_runtime.execute", execute, raising=False)
    monkeypatch.setattr(session, "commit", track_commit)

    first = await execute_revision_task_run(
        session, run=run, task=task, worker_id="revision-worker"
    )
    second = await execute_revision_task_run(
        session, run=run, task=task, worker_id="revision-worker"
    )

    assert first == "completed"
    assert second == "completed"
    assert calls.count("barrier") == 1
    assert calls.count("external") == 1
    assert calls.index("commit") < calls.index("external")


@pytest.mark.asyncio
async def test_revision_crash_before_barrier_commit_retries_with_zero_early_external_calls(
    session, admin, monkeypatch
) -> None:
    _account, _thread, _turn, run, task, _skill, revision = (
        await _revision_execution_context(session, admin, key="barrier-commit-crash")
    )
    real_commit = session.commit
    commit_calls = 0
    external_calls = 0

    async def barrier(*_args, **_kwargs):
        return FullRecompute(
            reason="unknown_constraint",
            execute_steps=tuple(revision.affected_steps),
            plan_hash=revision.plan_hash,
        )

    async def fail_first_commit():
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 1:
            raise ConnectionError("barrier commit interrupted")
        await real_commit()

    async def execute(*_args, **_kwargs):
        nonlocal external_calls
        external_calls += 1
        return SimpleNamespace(status="completed", response="done")

    monkeypatch.setattr(
        "app.services.turn_execution.prepare_revision_execution", barrier, raising=False
    )
    monkeypatch.setattr(
        "app.services.turn_execution.skill_runtime.execute", execute, raising=False
    )
    monkeypatch.setattr(session, "commit", fail_first_commit)
    run_id = run.id
    task_id = task.id

    with pytest.raises(ConnectionError, match="barrier commit interrupted"):
        await execute_revision_task_run(
            session, run=run, task=task, worker_id="revision-worker"
        )
    assert external_calls == 0
    await session.rollback()
    run = await session.get(AgentRun, run_id)
    task = await session.get(BrainTask, task_id)
    assert run is not None
    assert task is not None

    status = await execute_revision_task_run(
        session, run=run, task=task, worker_id="revision-worker"
    )

    assert status == "completed"
    assert external_calls == 1


@pytest.mark.asyncio
async def test_revision_external_success_without_local_completion_retries_to_manual_once(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run, task, skill, revision = (
        await _revision_execution_context(session, admin, key="external-success-crash")
    )
    provider_calls = 0

    async def barrier(*_args, **_kwargs):
        return FullRecompute(
            reason="unknown_constraint",
            execute_steps=tuple(revision.affected_steps),
            plan_hash=revision.plan_hash,
        )

    async def execute(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        session.add(
            AgentToolCall(
                org_id=run.org_id,
                task_id=task.id,
                skill_run_id=skill.id,
                thread_id=turn.thread_id,
                turn_id=turn.id,
                tool_code="publish-non-idempotent",
                tool_name="publish",
                idempotency_key="external-success-crash",
                side_effect_level="non_idempotent_write",
                status="success",
                input_summary="safe",
                output_summary="safe",
            )
        )
        await session.commit()
        raise ConnectionError("worker lost after provider success")

    monkeypatch.setattr(
        "app.services.turn_execution.prepare_revision_execution", barrier, raising=False
    )
    monkeypatch.setattr(
        "app.services.turn_execution.skill_runtime.execute", execute, raising=False
    )

    with pytest.raises(ConnectionError, match="worker lost"):
        await execute_revision_task_run(
            session, run=run, task=task, worker_id="revision-worker"
        )

    status = await execute_revision_task_run(
        session, run=run, task=task, worker_id="revision-worker"
    )

    await session.refresh(revision)
    assert status == "stopped"
    assert provider_calls == 1
    assert revision.status == "manual_reconciliation"
    assert revision.finished_at is not None


@pytest.mark.asyncio
async def test_revision_completed_skill_before_revision_completion_retries_without_reexecution(
    session, admin, monkeypatch
) -> None:
    _account, _thread, _turn, run, task, skill, revision = (
        await _revision_execution_context(session, admin, key="skill-complete-crash")
    )
    external_calls = 0
    completion_calls = 0
    from app.services import turn_execution as turn_execution_service

    real_complete_revision = turn_execution_service.complete_revision

    async def barrier(*_args, **_kwargs):
        return FullRecompute(
            reason="unknown_constraint",
            execute_steps=tuple(revision.affected_steps),
            plan_hash=revision.plan_hash,
        )

    async def execute(*_args, **_kwargs):
        nonlocal external_calls
        external_calls += 1
        skill.status = "completed"
        skill.output_snapshot = {"status": "completed", "response": "durable"}
        await session.commit()
        return SimpleNamespace(status="completed", response="durable")

    async def fail_once(*args, **kwargs):
        nonlocal completion_calls
        completion_calls += 1
        if completion_calls == 1:
            raise ConnectionError("revision completion interrupted")
        return await real_complete_revision(*args, **kwargs)

    monkeypatch.setattr(
        "app.services.turn_execution.prepare_revision_execution", barrier, raising=False
    )
    monkeypatch.setattr(
        "app.services.turn_execution.skill_runtime.execute", execute, raising=False
    )
    monkeypatch.setattr(
        "app.services.turn_execution.complete_revision", fail_once, raising=False
    )

    with pytest.raises(ConnectionError, match="revision completion interrupted"):
        await execute_revision_task_run(
            session, run=run, task=task, worker_id="revision-worker"
        )

    status = await execute_revision_task_run(
        session, run=run, task=task, worker_id="revision-worker"
    )

    await session.refresh(revision)
    assert status == "completed"
    assert external_calls == 1
    assert revision.status == "completed"


@pytest.mark.asyncio
async def test_revision_barrier_committed_before_step_start_retries_without_early_external_call(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run, task, _skill, revision = (
        await _revision_execution_context(session, admin, key="after-barrier-before-step")
    )
    event_scope = TurnEventScope(
        org_id=run.org_id,
        account_id=revision.account_id,
        thread_id=turn.thread_id,
        turn_id=turn.id,
        run_id=run.id,
        skill_run_id=revision.revision_skill_run_id,
    )
    await append_turn_event(
        session,
        event_scope,
        "step.invalidated",
        {
            "revision_id": revision.id,
            "revision_run_id": run.id,
            "task_id": task.id,
            "step": "script_generation",
            "step_key": "script_generation",
            "status": "invalidated",
        },
        f"revision:{revision.id}:invalidated:script_generation",
    )
    await session.commit()
    executor_entries = 0
    external_calls = 0

    async def barrier(*_args, **_kwargs):
        return FullRecompute(
            reason="unknown_constraint",
            execute_steps=tuple(revision.affected_steps),
            plan_hash=revision.plan_hash,
        )

    async def execute(*_args, **_kwargs):
        nonlocal executor_entries, external_calls
        executor_entries += 1
        if executor_entries == 1:
            persisted = await session.scalar(
                select(func.count(Event.id)).where(
                    Event.run_id == run.id,
                    Event.type == "step.invalidated",
                )
            )
            assert persisted > 0
            raise ConnectionError("crash before _start_skill_stage")
        external_calls += 1
        return SimpleNamespace(status="completed", response="done")

    monkeypatch.setattr(
        "app.services.turn_execution.prepare_revision_execution", barrier, raising=False
    )
    monkeypatch.setattr(
        "app.services.turn_execution.skill_runtime.execute", execute, raising=False
    )

    with pytest.raises(ConnectionError, match="before _start_skill_stage"):
        await execute_revision_task_run(
            session, run=run, task=task, worker_id="revision-worker"
        )
    await session.refresh(revision)
    assert revision.status == "running"
    assert external_calls == 0

    status = await execute_revision_task_run(
        session, run=run, task=task, worker_id="revision-worker"
    )

    assert status == "completed"
    assert executor_entries == 2
    assert external_calls == 1


@pytest.mark.asyncio
async def test_revision_terminal_executor_error_marks_revision_failed(session, admin, monkeypatch):
    _account, _thread, _turn, run, task, _skill, revision = (
        await _revision_execution_context(session, admin, key="terminal-executor-error")
    )

    async def barrier(*_args, **_kwargs):
        return FullRecompute(
            reason="unknown_constraint",
            execute_steps=tuple(revision.affected_steps),
            plan_hash=revision.plan_hash,
        )

    async def execute(*_args, **_kwargs):
        raise ValueError("terminal executor failure")

    monkeypatch.setattr(
        "app.services.turn_execution.prepare_revision_execution", barrier, raising=False
    )
    monkeypatch.setattr(
        "app.services.turn_execution.skill_runtime.execute", execute, raising=False
    )

    with pytest.raises(ValueError, match="terminal executor failure"):
        await execute_revision_task_run(
            session, run=run, task=task, worker_id="revision-worker"
        )

    await session.refresh(revision)
    assert revision.status == "failed"
    assert revision.finished_at is not None


@pytest.mark.asyncio
async def test_i4_rule_1_real_runtime_barrier_commit_failure_has_zero_external_calls(
    session, admin, monkeypatch
) -> None:
    (
        _account,
        _thread,
        _turn,
        run,
        task,
        _skill,
        _revision,
        _runtime,
        tools,
        harness,
    ) = await _real_revision_runtime_context(
        session, admin, monkeypatch, key="i4-rule-1"
    )
    real_commit = session.commit
    barrier_commits = 0

    async def fail_barrier_commit():
        nonlocal barrier_commits
        barrier_commits += 1
        if barrier_commits == 1:
            raise ConnectionError("barrier commit interrupted")
        await real_commit()

    monkeypatch.setattr(session, "commit", fail_barrier_commit)
    run_id = run.id
    task_id = task.id

    with pytest.raises(ConnectionError, match="barrier commit interrupted"):
        await execute_revision_task_run(
            session, run=run, task=task, worker_id="i4-real-worker"
        )

    assert await _real_runtime_counts(
        session, run_id=run_id, tools=tools, harness=harness
    ) == {"provider": 0, "tool": 0, "expert": 0}
    assert await session.scalar(
        select(func.count(AgentInvocation.id)).where(AgentInvocation.run_id == run.id)
    ) == 0
    assert await session.scalar(
        select(func.count(AgentToolCall.id)).where(AgentToolCall.task_id == task.id)
    ) == 0
    await session.rollback()
    assert await session.scalar(
        select(func.count(Event.id)).where(Event.run_id == run_id)
    ) == 0
    monkeypatch.setattr(session, "commit", real_commit)
    run = await session.get(AgentRun, run_id)
    task = await session.get(BrainTask, task_id)
    assert run is not None and task is not None

    retry = await execute_revision_task_run(
        session, run=run, task=task, worker_id="i4-real-worker"
    )

    assert retry == "waiting_user"
    assert await _real_runtime_counts(
        session, run_id=run_id, tools=tools, harness=harness
    ) == {"provider": 2, "tool": 3, "expert": 2}


@pytest.mark.asyncio
async def test_i4_rule_2_real_stage_retry_reuses_committed_revision_plan(
    session, admin, monkeypatch
) -> None:
    from app.orchestrator import skill_runtime as skill_runtime_module

    (
        _account,
        _thread,
        _turn,
        run,
        task,
        _skill,
        revision,
        _runtime,
        tools,
        harness,
    ) = await _real_revision_runtime_context(
        session, admin, monkeypatch, key="i4-rule-2"
    )
    original_plan_hash = revision.plan_hash
    real_start = skill_runtime_module._start_skill_stage
    starts = 0

    async def fail_before_first_stage(*_args, **_kwargs):
        nonlocal starts
        starts += 1
        if starts == 1:
            raise ConnectionError("after barrier before stage start")
        return await real_start(*_args, **_kwargs)

    monkeypatch.setattr(skill_runtime_module, "_start_skill_stage", fail_before_first_stage)
    run_id = run.id
    task_id = task.id

    with pytest.raises(ConnectionError, match="after barrier before stage start"):
        await execute_revision_task_run(
            session, run=run, task=task, worker_id="i4-real-worker"
        )
    await session.refresh(revision)
    durable_plan_hash = revision.plan_hash
    assert durable_plan_hash == original_plan_hash
    assert await _real_runtime_counts(
        session, run_id=run_id, tools=tools, harness=harness
    ) == {"provider": 0, "tool": 0, "expert": 0}
    first_events = list(
        await session.scalars(select(Event).where(Event.run_id == run_id))
    )
    assert len(
        [event for event in first_events if event.type == "step.invalidated"]
    ) == len(revision.affected_steps)
    assert not any(event.type == "step.started" for event in first_events)
    run = await session.get(AgentRun, run_id)
    task = await session.get(BrainTask, task_id)
    assert run is not None and task is not None

    status = await execute_revision_task_run(
        session, run=run, task=task, worker_id="i4-real-worker"
    )

    await session.refresh(revision)
    assert status == "waiting_user"
    assert revision.plan_hash == durable_plan_hash == original_plan_hash
    assert starts > 1
    assert await _real_runtime_counts(
        session, run_id=run.id, tools=tools, harness=harness
    ) == {"provider": 2, "tool": 3, "expert": 2}


@pytest.mark.asyncio
async def test_i4_rule_3_all_reused_real_runtime_has_only_reused_events_and_zero_external(
    session, admin, monkeypatch
) -> None:
    (
        _account,
        _thread,
        turn,
        run,
        task,
        _skill,
        revision,
        _runtime,
        tools,
        harness,
    ) = await _real_revision_runtime_context(
        session, admin, monkeypatch, key="i4-rule-3"
    )
    revision.mode = "partial"
    revision.affected_steps = []
    revision.direct_affected_steps = []
    contract = require_checkpoint_graph_contract("operation_iteration", 1)
    bindings = tuple(
        StageReuseBinding(
            step_key=step.key,
            source_checkpoint_id=100 + index,
            checkpoint_id=200 + index,
            output=StageDataEnvelope(
                schema_version=step.output_schema_version,
                data={key: {"summary": "durable"} for key in step.produces_outputs},
            ),
        )
        for index, step in enumerate(contract.steps, start=1)
    )
    revision.reused_steps = [binding.step_key for binding in bindings]

    async def reused_plan(*_args, **_kwargs):
        return PartialExecution(
            execute_steps=(),
            reused=bindings,
            hydrated_outputs={binding.step_key: binding.output for binding in bindings},
            plan_hash=revision.plan_hash,
        )

    async def hydrate(*_args, step_key, **_kwargs):
        binding = next(item for item in bindings if item.step_key == step_key)
        return ResolvedStageOutput(
            checkpoint_id=binding.checkpoint_id,
            source_checkpoint_id=binding.source_checkpoint_id,
            output=binding.output,
        )

    monkeypatch.setattr(
        "app.services.turn_execution.resolve_revision_executor_boundaries",
        lambda _contract: SimpleNamespace(requires_full_recompute=False),
    )
    monkeypatch.setattr(
        "app.services.turn_execution.prepare_revision_execution", reused_plan
    )
    monkeypatch.setattr("app.services.turn_execution.load_latest_stage_output", hydrate)
    await session.commit()

    status = await execute_revision_task_run(
        session, run=run, task=task, worker_id="i4-real-worker"
    )
    revision_events = list(
        await session.scalars(
            select(Event).where(Event.turn_id == turn.id).order_by(Event.sequence)
        )
    )

    assert status == "completed"
    first_counts = await _real_runtime_counts(
        session, run_id=run.id, tools=tools, harness=harness
    )
    retry = await execute_revision_task_run(
        session, run=run, task=task, worker_id="i4-real-worker"
    )
    assert retry == "completed"
    assert first_counts == await _real_runtime_counts(
        session, run_id=run.id, tools=tools, harness=harness
    ) == {"provider": 0, "tool": 0, "expert": 0}
    assert revision.affected_steps == []
    assert set(revision.reused_steps) == {step.key for step in contract.steps}
    assert [event.type for event in revision_events].count("step.reused") == len(
        contract.steps
    )
    assert not any(
        event.type in {"step.started", "step.completed", "step.invalidated"}
        for event in revision_events
    )


@pytest.mark.asyncio
async def test_i4_rule_4_invalidated_is_durable_before_real_stage_start_and_external(
    session, admin, monkeypatch
) -> None:
    from app.orchestrator import skill_runtime as skill_runtime_module

    (
        _account,
        _thread,
        turn,
        run,
        task,
        _skill,
        revision,
        _runtime,
        tools,
        harness,
    ) = await _real_revision_runtime_context(
        session, admin, monkeypatch, key="i4-rule-4"
    )
    real_start = skill_runtime_module._start_skill_stage

    async def fail_at_real_start(*_args, **_kwargs):
        invalidated = list(
            await session.scalars(
                select(Event).where(
                    Event.turn_id == turn.id,
                    Event.type == "step.invalidated",
                )
            )
        )
        assert {event.payload["step_key"] for event in invalidated} == set(
            revision.affected_steps
        )
        assert len(tools.calls) == len(harness.calls) == 0
        raise ConnectionError("invalidated durable before real start")

    monkeypatch.setattr(skill_runtime_module, "_start_skill_stage", fail_at_real_start)
    run_id = run.id
    task_id = task.id

    with pytest.raises(ConnectionError, match="invalidated durable before real start"):
        await execute_revision_task_run(
            session, run=run, task=task, worker_id="i4-real-worker"
        )
    assert await _real_runtime_counts(
        session, run_id=run_id, tools=tools, harness=harness
    ) == {"provider": 0, "tool": 0, "expert": 0}
    first_events = list(
        await session.scalars(select(Event).where(Event.run_id == run_id))
    )
    assert not any(event.type == "step.started" for event in first_events)
    monkeypatch.setattr(skill_runtime_module, "_start_skill_stage", real_start)
    run = await session.get(AgentRun, run_id)
    task = await session.get(BrainTask, task_id)
    assert run is not None and task is not None

    retry = await execute_revision_task_run(
        session, run=run, task=task, worker_id="i4-real-worker"
    )

    assert retry == "waiting_user"
    assert await _real_runtime_counts(
        session, run_id=run_id, tools=tools, harness=harness
    ) == {"provider": 2, "tool": 3, "expert": 2}


@pytest.mark.asyncio
async def test_i4_rule_5_durable_completed_skill_retry_does_not_reenter_real_stage(
    session, admin, monkeypatch
) -> None:
    from app.orchestrator import skill_runtime as skill_runtime_module

    (
        _account,
        _thread,
        _turn,
        run,
        task,
        skill,
        revision,
        _runtime,
        tools,
        harness,
    ) = await _real_revision_runtime_context(
        session, admin, monkeypatch, key="i4-rule-5"
    )
    revision.status = "running"
    revision.started_at = datetime.now(UTC)
    skill.status = "completed"
    skill.output_snapshot = {"status": "completed", "response": "durable"}
    await session.commit()

    async def forbidden_stage(*_args, **_kwargs):
        raise AssertionError("durable completed retry must not reenter a stage")

    monkeypatch.setattr(skill_runtime_module, "_start_skill_stage", forbidden_stage)

    first = await execute_revision_task_run(
        session, run=run, task=task, worker_id="i4-real-worker"
    )
    second = await execute_revision_task_run(
        session, run=run, task=task, worker_id="i4-real-worker"
    )

    assert first == second == "completed"
    assert await _real_runtime_counts(
        session, run_id=run.id, tools=tools, harness=harness
    ) == {"provider": 0, "tool": 0, "expert": 0}
    assert await session.scalar(
        select(func.count(Event.id)).where(
            Event.run_id == run.id,
            Event.type == "run.revision_completed",
        )
    ) == 1


@pytest.mark.asyncio
async def test_i4_rule_6_non_idempotent_child_success_without_local_completion_goes_manual_once(
    session, admin, monkeypatch
) -> None:
    (
        _account,
        _thread,
        _turn,
        run,
        task,
        _skill,
        revision,
        runtime,
        _tools,
        harness,
    ) = await _real_revision_runtime_context(
        session, admin, monkeypatch, key="i4-rule-6"
    )

    class ExternalSuccessThenCrash:
        calls = 0

        async def execute(self, *, scope, request, **_kwargs):
            self.calls += 1
            session.add(
                AgentToolCall(
                    org_id=run.org_id,
                    task_id=task.id,
                    skill_run_id=scope.skill_run_id,
                    thread_id=scope.thread_id,
                    turn_id=scope.turn_id,
                    tool_code=request.tool_code,
                    tool_name=request.tool_code,
                    idempotency_key=f"i4-rule-6:{self.calls}",
                    side_effect_level="non_idempotent_write",
                    status="success",
                    input_summary="safe",
                    output_summary="safe",
                )
            )
            await session.commit()
            raise ConnectionError("lost after external success")

    external = ExternalSuccessThenCrash()
    runtime._tool_executor = external

    with pytest.raises(ConnectionError, match="lost after external success"):
        await execute_revision_task_run(
            session, run=run, task=task, worker_id="i4-real-worker"
        )
    first_counts = await _real_runtime_counts(
        session, run_id=run.id, tools=external, harness=harness
    )

    status = await execute_revision_task_run(
        session, run=run, task=task, worker_id="i4-real-worker"
    )
    replay = await execute_revision_task_run(
        session, run=run, task=task, worker_id="i4-real-worker"
    )
    await session.refresh(revision)

    assert status == replay == "stopped"
    assert first_counts == await _real_runtime_counts(
        session, run_id=run.id, tools=external, harness=harness
    ) == {"provider": 0, "tool": 1, "expert": 0}
    assert revision.status == "manual_reconciliation"
    assert await session.scalar(
        select(func.count(Event.id)).where(
            Event.run_id == run.id,
            Event.type == "run.revision_manual_reconciliation",
        )
    ) == 1
    assert await session.scalar(
        select(func.count(Event.id)).where(
            Event.run_id == run.id,
            Event.type == "run.revision_completed",
        )
    ) == 0


@pytest.mark.asyncio
async def test_i4_non_idempotent_ambiguous_receipt_stops_before_external_replay(
    session, admin, monkeypatch
) -> None:
    (
        _account,
        _thread,
        turn,
        run,
        task,
        skill,
        revision,
        _runtime,
        tools,
        harness,
    ) = await _real_revision_runtime_context(
        session, admin, monkeypatch, key="i4-ambiguous-replay"
    )
    revision.status = "running"
    revision.started_at = datetime.now(UTC)
    receipt = AgentToolCall(
        org_id=run.org_id,
        task_id=task.id,
        skill_run_id=skill.id,
        thread_id=turn.thread_id,
        turn_id=turn.id,
        tool_code="provider.publish",
        tool_name="Provider publish",
        idempotency_key="i4-ambiguous-replay",
        side_effect_level="non_idempotent_write",
        status="ambiguous",
        input_summary="safe",
        output_summary="verification required",
    )
    session.add(receipt)
    await session.flush()
    session.add(
        ToolExecutionAttempt(
            tool_call_id=receipt.id,
            attempt_no=1,
            status="ambiguous",
            error="TOOL_RESULT_AMBIGUOUS",
            finished_at=datetime.now(UTC),
        )
    )
    await session.commit()

    first = await execute_revision_task_run(
        session, run=run, task=task, worker_id="i4-real-worker"
    )
    replay = await execute_revision_task_run(
        session, run=run, task=task, worker_id="i4-real-worker"
    )

    assert first == replay == "stopped"
    assert await _real_runtime_counts(
        session, run_id=run.id, tools=tools, harness=harness
    ) == {"provider": 0, "tool": 0, "expert": 0}
    assert await session.scalar(
        select(func.count(Event.id)).where(
            Event.run_id == run.id,
            Event.type == "run.revision_manual_reconciliation",
        )
    ) == 1


@pytest.mark.asyncio
async def test_i4_rule_7_terminal_duplicate_never_classifies_or_reenters_real_runtime(
    session, admin, monkeypatch
) -> None:
    from app.orchestrator import skill_runtime as skill_runtime_module

    (
        _account,
        _thread,
        _turn,
        run,
        task,
        _skill,
        revision,
        _runtime,
        tools,
        harness,
    ) = await _real_revision_runtime_context(
        session, admin, monkeypatch, key="i4-rule-7"
    )
    revision.status = "completed"
    revision.finished_at = datetime.now(UTC)
    await session.commit()

    def forbidden_classification(_exc):
        raise AssertionError("terminal duplicate must not classify")

    async def forbidden_stage(*_args, **_kwargs):
        raise AssertionError("terminal duplicate must not enter real runtime")

    monkeypatch.setattr(
        "app.services.turn_execution.classify_runtime_failure", forbidden_classification
    )
    monkeypatch.setattr(skill_runtime_module, "_start_skill_stage", forbidden_stage)

    first = await execute_revision_task_run(
        session, run=run, task=task, worker_id="i4-real-worker"
    )
    second = await execute_revision_task_run(
        session, run=run, task=task, worker_id="i4-real-worker"
    )

    assert first == second == "completed"
    assert await _real_runtime_counts(
        session, run_id=run.id, tools=tools, harness=harness
    ) == {"provider": 0, "tool": 0, "expert": 0}
    assert await session.scalar(
        select(func.count(Event.id)).where(Event.run_id == run.id)
    ) == 0


@pytest.mark.asyncio
async def test_revision_full_fallback_emits_each_new_invalidated_step_once(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run, task, _skill, revision = (
        await _revision_execution_context(session, admin, key="fallback-invalidated")
    )
    contract = require_checkpoint_graph_contract("operation_iteration", 1)
    event_scope = TurnEventScope(
        org_id=run.org_id,
        account_id=revision.account_id,
        thread_id=turn.thread_id,
        turn_id=turn.id,
        run_id=run.id,
        skill_run_id=revision.revision_skill_run_id,
    )
    await append_turn_event(
        session,
        event_scope,
        "step.invalidated",
        {
            "revision_id": revision.id,
            "revision_run_id": run.id,
            "task_id": task.id,
            "step": "script_generation",
            "step_key": "script_generation",
            "status": "invalidated",
        },
        f"revision:{revision.id}:invalidated:script_generation",
    )
    await session.commit()

    async def execute(*_args, **_kwargs):
        return SimpleNamespace(status="completed", response="done")

    monkeypatch.setattr(
        "app.services.turn_execution.skill_runtime.execute", execute, raising=False
    )

    first = await execute_revision_task_run(
        session, run=run, task=task, worker_id="revision-worker"
    )
    second = await execute_revision_task_run(
        session, run=run, task=task, worker_id="revision-worker"
    )

    invalidated = list(
        await session.scalars(
            select(Event).where(
                Event.run_id == run.id,
                Event.type == "step.invalidated",
            )
        )
    )
    assert first == second == "completed"
    invalidated_keys = [event.payload["step_key"] for event in invalidated]
    expected_keys = [step.key for step in contract.steps]
    assert len(invalidated_keys) == len(expected_keys)
    assert set(invalidated_keys) == set(expected_keys)
    for step_key in expected_keys:
        assert invalidated_keys.count(step_key) == 1


@pytest.mark.asyncio
async def test_revision_partial_hydrates_reused_output_without_completed_event(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run, task, _skill, revision = await _revision_execution_context(
        session, admin, key="hydrate"
    )
    envelope = StageDataEnvelope(
        schema_version="read_account_data-output/v1",
        data={"account_snapshot": {"summary": "durable"}},
    )
    binding = StageReuseBinding(
        step_key="read_account_data",
        source_checkpoint_id=11,
        checkpoint_id=12,
        output=envelope,
    )
    hydrated_calls: list[str] = []

    async def barrier(*_args, **_kwargs):
        return PartialExecution(
            execute_steps=("script_generation",),
            reused=(binding,),
            hydrated_outputs={"read_account_data": envelope},
            plan_hash=revision.plan_hash,
        )

    async def hydrate(*_args, step_key, **_kwargs):
        hydrated_calls.append(step_key)
        return ResolvedStageOutput(
            checkpoint_id=12,
            source_checkpoint_id=11,
            output=envelope,
        )

    async def execute(*_args, **_kwargs):
        return SimpleNamespace(status="completed", response="revision completed")

    monkeypatch.setattr(
        "app.services.turn_execution.resolve_revision_executor_boundaries",
        lambda _contract: SimpleNamespace(requires_full_recompute=False),
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.turn_execution.prepare_revision_execution", barrier, raising=False
    )
    monkeypatch.setattr(
        "app.services.turn_execution.load_latest_stage_output", hydrate, raising=False
    )
    monkeypatch.setattr("app.services.turn_execution.skill_runtime.execute", execute, raising=False)

    status = await execute_revision_task_run(
        session, run=run, task=task, worker_id="revision-worker"
    )

    events = list(
        await session.scalars(
            select(Event).where(Event.turn_id == turn.id).order_by(Event.sequence)
        )
    )
    assert status == "completed"
    assert hydrated_calls == ["read_account_data"]
    assert [event.type for event in events].count("step.reused") == 1
    assert not any(
        event.type == "step.completed" and event.payload.get("step") == "read_account_data"
        for event in events
    )


@pytest.mark.asyncio
async def test_revision_manual_reconciliation_stops_with_zero_external_calls(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run, task, _skill, revision = await _revision_execution_context(
        session, admin, key="manual"
    )
    external_calls = 0

    async def barrier(*_args, **_kwargs):
        return ManualReconciliation(
            reason="external_write_ambiguous",
            blocking_receipt_ids=(91,),
            plan_hash=revision.plan_hash,
        )

    async def forbidden_external(*_args, **_kwargs):
        nonlocal external_calls
        external_calls += 1
        raise AssertionError("manual reconciliation must not execute externally")

    monkeypatch.setattr(
        "app.services.turn_execution.resolve_revision_executor_boundaries",
        lambda _contract: SimpleNamespace(requires_full_recompute=False),
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.turn_execution.prepare_revision_execution", barrier, raising=False
    )
    monkeypatch.setattr(
        "app.services.turn_execution.skill_runtime.execute", forbidden_external, raising=False
    )

    status = await execute_revision_task_run(
        session, run=run, task=task, worker_id="revision-worker"
    )
    await complete_agent_run(session, run.id, task_id=task.id, status=status)

    events = list(await session.scalars(select(Event).where(Event.turn_id == turn.id)))
    assert status == "stopped"
    assert external_calls == 0
    await session.refresh(run)
    await session.refresh(_skill)
    await session.refresh(revision)
    assert (run.status, _skill.status, revision.status) == (
        "stopped",
        "stopped",
        "manual_reconciliation",
    )
    assert revision.finished_at is not None
    assert any(event.type == "run.revision_manual_reconciliation" for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["blocked", "failed", "stopped"])
async def test_revision_noncompleted_result_synchronizes_all_runtime_ledgers(
    session, admin, monkeypatch, terminal_status
) -> None:
    _account, _thread, _turn, run, task, skill, revision = (
        await _revision_execution_context(
            session, admin, key=f"revision-terminal-{terminal_status}"
        )
    )

    async def barrier(*_args, **_kwargs):
        return FullRecompute(
            reason="unknown_constraint",
            execute_steps=tuple(revision.affected_steps),
            plan_hash=revision.plan_hash,
        )

    async def execute(*_args, **_kwargs):
        return SimpleNamespace(status=terminal_status, response="revision terminal")

    monkeypatch.setattr(
        "app.services.turn_execution.prepare_revision_execution", barrier, raising=False
    )
    monkeypatch.setattr(
        "app.services.turn_execution.skill_runtime.execute", execute, raising=False
    )

    status = await execute_revision_task_run(
        session, run=run, task=task, worker_id="revision-worker"
    )
    await complete_agent_run(session, run.id, task_id=task.id, status=status)

    await session.refresh(run)
    await session.refresh(skill)
    await session.refresh(revision)
    assert status == terminal_status
    assert (run.status, skill.status, revision.status) == (
        terminal_status,
        terminal_status,
        terminal_status,
    )
    assert revision.finished_at is not None


@pytest.mark.asyncio
async def test_account_inspection_persists_ordered_public_turn_progress(
    session,
    admin,
    monkeypatch,
) -> None:
    from tests.test_account_inspection_skill import (
        _FakeHarness,
        _FakeTools,
        _PassingCritic,
    )

    account, thread, turn, run = await _turn_context(
        session,
        admin,
        key="durable-account-inspection-progress",
    )
    monkeypatch.setattr(
        "app.services.turn_execution.skill_runtime",
        SkillRuntime(
            tool_executor=_FakeTools(sufficient=True),
            harness=_FakeHarness(),
            critic=_PassingCritic(),
        ),
    )

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request(
            "durable-account-inspection-progress",
            requested_skill_code="account_inspection",
        ),
    )

    reliable_types = {
        "turn.received",
        "step.started",
        "step.completed",
        "step.failed",
        "deliverable.updated",
        "turn.completed",
        "turn.failed",
        "turn.blocked",
        "turn.cancelled",
        "turn.stopped",
    }
    events = list(
        await session.scalars(
            select(Event)
            .where(Event.turn_id == turn.id, Event.type.in_(reliable_types))
            .order_by(Event.sequence)
        )
    )

    assert result.status == "completed"
    assert [(event.type, event.payload.get("step")) for event in events] == [
        ("turn.received", None),
        ("step.started", "read_data"),
        ("step.completed", "read_data"),
        ("step.started", "specialist_work"),
        ("step.completed", "specialist_work"),
        ("step.started", "quality_review"),
        ("step.completed", "quality_review"),
        ("step.started", "prepare_deliverable"),
        ("deliverable.updated", None),
        ("step.completed", "prepare_deliverable"),
        ("turn.completed", None),
    ]
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert {
        (event.org_id, event.account_id, event.thread_id, event.turn_id, event.run_id)
        for event in events
    } == {(admin.org_id, account.id, thread.id, turn.id, run.id)}

    skill_run = await session.scalar(select(SkillRun).where(SkillRun.run_id == run.id))
    deliverable = await session.get(Deliverable, result.projections[0]["artifact_id"])
    assert skill_run is not None
    assert deliverable is not None
    assert events[0].skill_run_id is None
    assert {event.skill_run_id for event in events[1:]} == {skill_run.id}
    deliverable_event = next(
        event for event in events if event.type == "deliverable.updated"
    )
    assert deliverable_event.payload == {
        "deliverable_id": deliverable.id,
        "deliverable_type": deliverable.type.value,
        "version": deliverable.version,
        "status": deliverable.status.value,
    }

    repeated = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request(
            "durable-account-inspection-progress",
            requested_skill_code="account_inspection",
        ),
    )
    repeated_events = list(
        await session.scalars(
            select(Event)
            .where(Event.turn_id == turn.id, Event.type.in_(reliable_types))
            .order_by(Event.sequence)
        )
    )
    assert repeated == result
    assert [event.id for event in repeated_events] == [event.id for event in events]


@pytest.mark.asyncio
async def test_terminal_turn_event_is_first_writer_wins_across_terminal_types(
    session,
    admin,
) -> None:
    account, thread, turn, run = await _turn_context(
        session,
        admin,
        key="durable-terminal-first-wins",
    )
    scope = TurnEventScope(
        org_id=admin.org_id,
        account_id=account.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
    )

    completed = await append_turn_event(
        session,
        scope,
        "turn.completed",
        {"status": "completed"},
        "terminal",
    )
    late_failure = await append_turn_event(
        session,
        scope,
        "turn.failed",
        {"status": "failed"},
        "terminal",
    )

    assert late_failure.id == completed.id
    assert late_failure.type == "turn.completed"
    with pytest.raises(ValueError, match="reserved for terminal events"):
        await append_turn_event(
            session,
            scope,
            "step.started",
            {"step": "read_data"},
            "terminal",
        )


@pytest.mark.asyncio
async def test_terminal_event_first_writer_wins_across_skill_attribution(
    session,
    admin,
) -> None:
    account, thread, turn, run, _task, skill_run = await _four_ledger_context(
        session,
        admin,
        key="terminal-skill-attribution",
    )
    skill_scope = TurnEventScope(
        org_id=admin.org_id,
        account_id=account.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        skill_run_id=skill_run.id,
    )

    completed = await append_turn_event(
        session,
        skill_scope,
        "turn.completed",
        {"status": "completed"},
        "terminal",
    )
    late_failure = await append_turn_event(
        session,
        replace(skill_scope, skill_run_id=None),
        "turn.failed",
        {"status": "failed"},
        "terminal",
    )

    assert late_failure.id == completed.id
    assert late_failure.type == "turn.completed"
    assert late_failure.skill_run_id == skill_run.id
    assert (
        await session.scalar(
            select(func.count(Event.id)).where(
                Event.turn_id == turn.id,
                Event.type.in_(
                    {
                        "turn.completed",
                        "turn.failed",
                        "turn.blocked",
                        "turn.cancelled",
                        "turn.stopped",
                    }
                ),
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_terminal_logical_key_is_reserved_for_terminal_events(
    session,
    admin,
) -> None:
    account, thread, turn, run = await _turn_context(
        session, admin, key="terminal-key-reserved"
    )

    with pytest.raises(ValueError, match="reserved for terminal events"):
        await append_turn_event(
            session,
            TurnEventScope(
                org_id=admin.org_id,
                account_id=account.id,
                thread_id=thread.id,
                turn_id=turn.id,
                run_id=run.id,
            ),
            "step.started",
            {"step": "read_data"},
            "terminal",
        )


@pytest.mark.asyncio
async def test_nonterminal_events_do_not_replay_across_skill_attribution(
    session,
    admin,
) -> None:
    account, thread, turn, run, _task, skill_run = await _four_ledger_context(
        session, admin, key="nonterminal-skill-scope"
    )
    skill_scope = TurnEventScope(
        org_id=admin.org_id,
        account_id=account.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        skill_run_id=skill_run.id,
    )

    skill_event = await append_turn_event(
        session,
        skill_scope,
        "step.started",
        {"step": "read_data"},
        "step:read_data:attempt:1:started",
    )
    run_event = await append_turn_event(
        session,
        replace(skill_scope, skill_run_id=None),
        "step.started",
        {"step": "read_data"},
        "step:read_data:attempt:1:started",
    )

    assert skill_event.id != run_event.id
    assert skill_event.skill_run_id == skill_run.id
    assert run_event.skill_run_id is None


@pytest.mark.asyncio
async def test_account_inspection_failure_preserves_started_checkpoint_and_fails_step(
    session,
    admin,
    monkeypatch,
) -> None:
    from tests.test_account_inspection_skill import _FakeHarness, _PassingCritic

    class FailingReadTools:
        async def execute(self, **_kwargs):
            raise ValueError("read boundary failed")

    account, thread, turn, run = await _turn_context(
        session,
        admin,
        key="durable-account-inspection-failure",
    )
    monkeypatch.setattr(
        "app.services.turn_execution.skill_runtime",
        SkillRuntime(
            tool_executor=FailingReadTools(),
            harness=_FakeHarness(),
            critic=_PassingCritic(),
        ),
    )

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request(
            "durable-account-inspection-failure",
            requested_skill_code="account_inspection",
        ),
    )
    events = list(
        await session.scalars(
            select(Event)
            .where(
                Event.turn_id == turn.id,
                Event.type.in_(
                    {
                        "turn.received",
                        "step.started",
                        "step.completed",
                        "step.failed",
                        "deliverable.updated",
                        "turn.failed",
                    }
                ),
            )
            .order_by(Event.sequence)
        )
    )

    assert result.status == "failed"
    assert [(event.type, event.payload.get("step")) for event in events] == [
        ("turn.received", None),
        ("step.started", "read_data"),
        ("step.failed", "read_data"),
        ("turn.failed", None),
    ]
    assert [event.sequence for event in events] == [1, 2, 3, 4]
    assert {event.account_id for event in events} == {account.id}
    assert {event.thread_id for event in events} == {thread.id}
    assert not any(event.type == "step.completed" for event in events)
    assert not any(event.type == "deliverable.updated" for event in events)


@pytest.mark.parametrize(
    "failed_stage",
    ["specialist_work", "quality_review", "prepare_deliverable"],
)
@pytest.mark.asyncio
async def test_account_inspection_records_the_actual_failed_stage(
    session,
    admin,
    monkeypatch,
    failed_stage,
) -> None:
    from tests.test_account_inspection_skill import (
        _FakeHarness,
        _FakeTools,
        _PassingCritic,
    )

    class FailingHarness(_FakeHarness):
        async def execute(self, *args, **kwargs):
            raise ValueError("specialist boundary failed")

    class FailingCritic:
        async def review(self, **_kwargs):
            raise ValueError("quality boundary failed")

    account, _thread, turn, run = await _turn_context(
        session,
        admin,
        key=f"durable-{failed_stage}-failure",
    )
    runtime = SkillRuntime(
        tool_executor=_FakeTools(sufficient=True),
        harness=(FailingHarness() if failed_stage == "specialist_work" else _FakeHarness()),
        critic=(FailingCritic() if failed_stage == "quality_review" else _PassingCritic()),
    )
    monkeypatch.setattr("app.services.turn_execution.skill_runtime", runtime)
    if failed_stage == "prepare_deliverable":

        async def fail_deliverable(*_args, **_kwargs):
            raise ValueError("deliverable boundary failed")

        monkeypatch.setattr(
            "app.orchestrator.skill_runtime.write_runtime_deliverable",
            fail_deliverable,
        )

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request(
            f"durable-{failed_stage}-failure",
            requested_skill_code="account_inspection",
        ),
    )
    events = list(
        await session.scalars(
            select(Event)
            .where(
                Event.turn_id == turn.id,
                Event.type.in_(
                    {
                        "step.started",
                        "step.completed",
                        "step.failed",
                        "deliverable.updated",
                        "turn.failed",
                    }
                ),
            )
            .order_by(Event.sequence)
        )
    )
    failed_events = [event for event in events if event.type == "step.failed"]

    assert result.status == "failed"
    assert len(failed_events) == 1
    assert failed_events[0].payload["step"] == failed_stage
    assert failed_events[0].payload["metadata"] == {"attempt": 1}
    assert not any(
        event.type == "step.completed" and event.payload.get("step") == failed_stage
        for event in events
    )
    assert sum(event.type == "turn.failed" for event in events) == 1
    assert not any(event.type == "deliverable.updated" for event in events)
    assert {event.account_id for event in events} == {account.id}


@pytest.mark.asyncio
async def test_account_inspection_commit_failure_is_attributed_to_quality_stage(
    session,
    admin,
    monkeypatch,
) -> None:
    from tests.test_account_inspection_skill import _FakeHarness, _FakeTools, _PassingCritic

    class CommitFailingCritic(_PassingCritic):
        async def review(self, **kwargs):
            result = await super().review(**kwargs)
            runtime_session = kwargs["session"]
            real_commit = runtime_session.commit

            async def fail_once():
                runtime_session.commit = real_commit
                raise RuntimeError("quality checkpoint commit failed")

            runtime_session.commit = fail_once
            return result

    _account, _thread, turn, run = await _turn_context(
        session, admin, key="quality-checkpoint-commit-failure"
    )
    monkeypatch.setattr(
        "app.services.turn_execution.skill_runtime",
        SkillRuntime(
            tool_executor=_FakeTools(sufficient=True),
            harness=_FakeHarness(),
            critic=CommitFailingCritic(),
        ),
    )

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request(
            "quality-checkpoint-commit-failure",
            requested_skill_code="account_inspection",
        ),
    )
    failed_steps = list(
        await session.scalars(
            select(Event).where(Event.turn_id == turn.id, Event.type == "step.failed")
        )
    )

    assert result.status == "failed"
    assert [(event.payload["step"], event.payload["metadata"]) for event in failed_steps] == [
        ("quality_review", {"attempt": 1})
    ]


@pytest.mark.asyncio
async def test_completed_stage_is_not_released_when_checkpoint_commit_fails(
    monkeypatch,
) -> None:
    from app.orchestrator import skill_runtime as skill_runtime_module

    class FailingSession:
        async def commit(self):
            raise RuntimeError("checkpoint commit failed")

    async def append_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(skill_runtime_module, "_append_skill_step_event", append_noop)
    token = skill_runtime_module._ACTIVE_SKILL_STAGES.set((("quality_review", 1),))
    try:
        with pytest.raises(RuntimeError, match="checkpoint commit failed"):
            await skill_runtime_module._complete_skill_stage(
                FailingSession(),
                scope=object(),
                step_code="quality_review",
                attempt=1,
                commit=True,
            )
        assert skill_runtime_module._ACTIVE_SKILL_STAGES.get() == (
            ("quality_review", 1),
        )
    finally:
        skill_runtime_module._ACTIVE_SKILL_STAGES.reset(token)


@pytest.mark.asyncio
async def test_account_inspection_close_failure_is_attributed_to_deferred_stage(
    session,
    admin,
    monkeypatch,
) -> None:
    from app.orchestrator import skill_runtime as skill_runtime_module
    from tests.test_account_inspection_skill import _FakeHarness, _FakeTools, _PassingCritic

    real_close = skill_runtime_module.close_runtime_state
    failed = False

    async def fail_completed_close_once(*args, **kwargs):
        nonlocal failed
        if kwargs["status"] == "completed" and not failed:
            failed = True
            raise RuntimeError("owning closure failed")
        return await real_close(*args, **kwargs)

    monkeypatch.setattr(skill_runtime_module, "close_runtime_state", fail_completed_close_once)
    _account, _thread, turn, run = await _turn_context(
        session, admin, key="prepare-deferred-close-failure"
    )
    monkeypatch.setattr(
        "app.services.turn_execution.skill_runtime",
        SkillRuntime(
            tool_executor=_FakeTools(sufficient=True),
            harness=_FakeHarness(),
            critic=_PassingCritic(),
        ),
    )

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request(
            "prepare-deferred-close-failure",
            requested_skill_code="account_inspection",
        ),
    )
    failed_steps = list(
        await session.scalars(
            select(Event).where(Event.turn_id == turn.id, Event.type == "step.failed")
        )
    )

    assert result.status == "failed"
    assert [(event.payload["step"], event.payload["metadata"]) for event in failed_steps] == [
        ("prepare_deliverable", {"attempt": 1})
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "runtime_status",
        "expected_turn_status",
        "expected_run_status",
        "expected_skill_status",
        "expected_task_status",
    ),
    [
        (
            "completed",
            "completed",
            "completed",
            "completed",
            BrainTaskStatus.COMPLETED,
        ),
        ("failed", "failed", "failed", "failed", BrainTaskStatus.FAILED),
        (
            "dead_letter",
            "dead_letter",
            "dead_letter",
            "failed",
            BrainTaskStatus.FAILED,
        ),
        (
            "cancelled",
            "cancelled",
            "cancelled",
            "cancelled",
            BrainTaskStatus.FAILED,
        ),
        (
            "waiting_permission",
            "waiting_permission",
            "waiting_permission",
            "waiting_permission",
            BrainTaskStatus.PENDING_CONFIRMATION,
        ),
    ],
)
async def test_close_runtime_state_maps_all_four_ledgers_consistently(
    session,
    admin,
    runtime_status,
    expected_turn_status,
    expected_run_status,
    expected_skill_status,
    expected_task_status,
) -> None:
    account, _thread, turn, run, task, skill_run = await _four_ledger_context(
        session,
        admin,
        key=f"state-{runtime_status}",
    )

    await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            run_id=run.id,
            turn_id=turn.id,
            skill_run_id=skill_run.id,
            task_id=task.id,
            account_id=account.id,
            result_payload={"status": runtime_status},
        ),
        status=runtime_status,
        message=f"message-{runtime_status}",
        error_code="TEST_ERROR" if runtime_status != "completed" else None,
    )

    await session.refresh(turn)
    await session.refresh(run)
    await session.refresh(skill_run)
    await session.refresh(task)
    assert turn.status == expected_turn_status
    assert run.status == expected_run_status
    assert skill_run.status == expected_skill_status
    assert task.status == expected_task_status


@pytest.mark.asyncio
async def test_close_runtime_state_retry_wait_does_not_write_terminal_response(
    session,
    admin,
) -> None:
    account, _thread, turn, run, task, skill_run = await _four_ledger_context(
        session,
        admin,
        key="state-retry-wait",
    )

    await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            run_id=run.id,
            turn_id=turn.id,
            skill_run_id=skill_run.id,
            task_id=task.id,
            account_id=account.id,
        ),
        status="retry_wait",
        message="本次失败，稍后重试。",
        error_code="TRANSIENT",
    )

    await session.refresh(turn)
    await session.refresh(task)
    assert turn.status == "retry_wait"
    assert turn.assistant_response is None
    assert task.status == BrainTaskStatus.RUNNING
    assert (
        await session.scalar(
            select(func.count(Event.id)).where(
                Event.run_id == run.id,
                Event.type == "brain.runtime.message_done",
            )
        )
        == 0
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_status", ["failed", "cancelled"])
async def test_close_runtime_state_terminal_message_is_idempotent(
    session,
    admin,
    runtime_status,
) -> None:
    account, _thread, turn, run, task, skill_run = await _four_ledger_context(
        session,
        admin,
        key=f"state-idempotent-{runtime_status}",
    )
    scope = RuntimeStateScope(
        run_id=run.id,
        turn_id=turn.id,
        skill_run_id=skill_run.id,
        task_id=task.id,
        account_id=account.id,
        result_payload={
            "status": runtime_status,
            "projections": [{"type": "execution_blocked", "code": "TEST"}],
        },
    )

    await close_runtime_state(
        session,
        scope=scope,
        status=runtime_status,
        message="只允许写入一次的终态消息。",
        error_code="TEST_ERROR",
    )
    await close_runtime_state(
        session,
        scope=scope,
        status=runtime_status,
        message="只允许写入一次的终态消息。",
        error_code="TEST_ERROR",
    )

    await session.refresh(turn)
    assert turn.assistant_response == "只允许写入一次的终态消息。"
    assert (
        await session.scalar(
            select(func.count(Event.id)).where(
                Event.run_id == run.id,
                Event.type == "brain.runtime.message_done",
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_close_runtime_state_records_reconciliation_and_skill_timeout(
    session,
    admin,
) -> None:
    account, _thread, turn, run, task, skill_run = await _four_ledger_context(
        session,
        admin,
        key="state-diagnostics",
    )
    run.status = "failed"
    turn.status = "running"
    await session.commit()

    await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            run_id=run.id,
            turn_id=turn.id,
            skill_run_id=skill_run.id,
            task_id=task.id,
            account_id=account.id,
            result_payload={"status": "failed"},
        ),
        status="failed",
        message="终态不一致已自动收口。",
    )

    timeout_account, _thread, timeout_turn, timeout_run, timeout_task, timeout_skill_run = (
        await _four_ledger_context(
            session,
            admin,
            key="state-timeout-diagnostic",
        )
    )
    await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            run_id=timeout_run.id,
            turn_id=timeout_turn.id,
            skill_run_id=timeout_skill_run.id,
            task_id=timeout_task.id,
            account_id=timeout_account.id,
            result_payload={"status": "failed"},
        ),
        status="failed",
        message="专家阶段超时，任务已安全收口。",
        error_code="EXPERT_STAGE_TIMEOUT",
    )

    events = list(
        await session.scalars(
            select(Event).where(
                Event.run_id.in_({run.id, timeout_run.id}),
                Event.type.in_(
                    {
                        "brain.runtime.terminal_state_reconciled",
                        "brain.runtime.skill_stage_timeout",
                    }
                ),
            )
        )
    )
    assert {event.type for event in events} == {
        "brain.runtime.terminal_state_reconciled",
        "brain.runtime.skill_stage_timeout",
    }
    reconciled = next(
        event for event in events if event.type == "brain.runtime.terminal_state_reconciled"
    )
    assert reconciled.payload["previous_turn_status"] == "running"


@pytest.mark.asyncio
async def test_close_runtime_state_pause_then_complete_delivers_both_messages_once(
    session,
    admin,
) -> None:
    account, _thread, turn, run, task, skill_run = await _four_ledger_context(
        session,
        admin,
        key="state-pause-then-complete",
    )
    base_scope = {
        "run_id": run.id,
        "org_id": admin.org_id,
        "thread_id": turn.thread_id,
        "turn_id": turn.id,
        "skill_run_id": skill_run.id,
        "task_id": task.id,
        "account_id": account.id,
    }

    await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            **base_scope,
            result_payload={"status": "waiting_permission"},
        ),
        status="waiting_permission",
        message="请先确认工具授权。",
    )
    await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            **base_scope,
            result_payload={"status": "waiting_permission"},
        ),
        status="waiting_permission",
        message="请先确认工具授权。",
    )
    completed_scope = RuntimeStateScope(
        **base_scope,
        result_payload={"status": "completed"},
    )
    await close_runtime_state(
        session,
        scope=completed_scope,
        status="completed",
        message="授权后任务已完成。",
    )
    await close_runtime_state(
        session,
        scope=completed_scope,
        status="completed",
        message="授权后任务已完成。",
    )

    await session.refresh(turn)
    deliveries = list(
        await session.scalars(
            select(Event)
            .where(
                Event.run_id == run.id,
                Event.type == "brain.runtime.message_done",
            )
            .order_by(Event.id)
        )
    )
    assert [event.payload["content"] for event in deliveries] == [
        "请先确认工具授权。",
        "授权后任务已完成。",
    ]
    paused_events = list(
        await session.scalars(
            select(Event)
            .where(Event.turn_id == turn.id, Event.type == "turn.paused")
            .order_by(Event.id)
        )
    )
    assert [event.payload for event in paused_events] == [{
        "status": "waiting_permission",
        "message": "请先确认工具授权。",
    }]
    assert turn.status == "completed"
    assert turn.assistant_response == "授权后任务已完成。"


@pytest.mark.asyncio
async def test_close_runtime_state_records_a_second_pause_after_resume_but_replays_each_pause_once(
    session,
    admin,
) -> None:
    account, _thread, turn, run, task, skill_run = await _four_ledger_context(
        session,
        admin,
        key="state-distinct-pauses",
    )
    scope = RuntimeStateScope(
        run_id=run.id,
        org_id=admin.org_id,
        thread_id=turn.thread_id,
        turn_id=turn.id,
        skill_run_id=skill_run.id,
        task_id=task.id,
        account_id=account.id,
    )

    await close_runtime_state(
        session,
        scope=scope,
        status="waiting_permission",
        message="Approve the first action.",
    )
    await close_runtime_state(
        session,
        scope=scope,
        status="running",
        message="Continuing after approval.",
    )
    await close_runtime_state(
        session,
        scope=scope,
        status="waiting_permission",
        message="Approve the second action.",
    )
    await close_runtime_state(
        session,
        scope=scope,
        status="waiting_permission",
        message="Approve the second action.",
    )

    pauses = list(
        await session.scalars(
            select(Event)
            .where(Event.turn_id == turn.id, Event.type == "turn.paused")
            .order_by(Event.sequence.asc(), Event.id.asc())
        )
    )
    assert [(event.sequence, event.payload["message"]) for event in pauses] == [
        (1, "Approve the first action."),
        (2, "Approve the second action."),
    ]


@pytest.mark.asyncio
async def test_close_runtime_state_first_terminal_preserves_skill_snapshot_on_replay(
    session,
    admin,
) -> None:
    account, _thread, turn, run, task, skill_run = await _four_ledger_context(
        session,
        admin,
        key="state-terminal-snapshot",
    )
    scope_values = {
        "run_id": run.id,
        "turn_id": turn.id,
        "skill_run_id": skill_run.id,
        "task_id": task.id,
        "account_id": account.id,
    }
    formal_snapshot = {
        "status": "completed",
        "artifact_id": 701,
        "response": "正式成果已生成。",
    }

    await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            **scope_values,
            result_payload={"status": "completed"},
            skill_output_snapshot=formal_snapshot,
        ),
        status="completed",
        message="正式成果已生成。",
    )
    await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            **scope_values,
            result_payload={"status": "failed"},
            skill_output_snapshot={
                "status": "failed",
                "error_code": "LATE_FAILURE",
            },
        ),
        status="failed",
        message="迟到的失败不得覆盖正式成果。",
        error_code="LATE_FAILURE",
    )
    await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            **scope_values,
            result_payload={"status": "completed"},
            skill_output_snapshot={
                "status": "completed",
                "artifact_id": 999,
                "response": "重复重放的不同快照。",
            },
        ),
        status="completed",
        message="重复重放不得覆盖正式成果。",
    )

    await session.refresh(skill_run)
    assert skill_run.status == "completed"
    assert skill_run.output_snapshot == formal_snapshot


@pytest.mark.asyncio
async def test_close_runtime_state_stopped_is_terminal_and_first_writer_wins(
    session,
    admin,
) -> None:
    account, thread, turn, run, task, skill_run = await _four_ledger_context(
        session, admin, key="state-stopped-first-wins"
    )
    scope_values = {
        "run_id": run.id,
        "org_id": admin.org_id,
        "thread_id": thread.id,
        "turn_id": turn.id,
        "skill_run_id": skill_run.id,
        "task_id": task.id,
        "account_id": account.id,
    }

    await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            **scope_values,
            result_payload={"status": "stopped", "reason": "ambiguous side effect"},
        ),
        status="stopped",
        message="Execution stopped for manual reconciliation.",
        error_code="SIDE_EFFECT_AMBIGUOUS",
    )
    await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            **scope_values,
            result_payload={"status": "completed"},
        ),
        status="completed",
        message="Late completion must not overwrite stopped.",
    )

    await session.refresh(turn)
    await session.refresh(run)
    await session.refresh(skill_run)
    await session.refresh(task)
    assert (turn.status, run.status, skill_run.status, task.status) == (
        "stopped",
        "stopped",
        "stopped",
        BrainTaskStatus.FAILED,
    )
    terminal_events = list(
        await session.scalars(
            select(Event).where(
                Event.turn_id == turn.id,
                Event.type.in_(
                    {
                        "turn.completed",
                        "turn.failed",
                        "turn.blocked",
                        "turn.cancelled",
                        "turn.stopped",
                    }
                ),
            )
        )
    )
    assert [event.type for event in terminal_events] == ["turn.stopped"]


@pytest.mark.asyncio
async def test_close_runtime_state_rejects_cross_scope_without_partial_commit(
    session,
    admin,
) -> None:
    account, _thread, turn, run, task, _skill_run = await _four_ledger_context(
        session,
        admin,
        key="state-scope-owner",
    )
    (
        _other_account,
        _other_thread,
        _other_turn,
        _other_run,
        _other_task,
        other_skill_run,
    ) = await _four_ledger_context(
        session,
        admin,
        key="state-scope-other",
    )

    with pytest.raises(ValueError, match="ownership"):
        await close_runtime_state(
            session,
            scope=RuntimeStateScope(
                run_id=run.id,
                turn_id=turn.id,
                skill_run_id=other_skill_run.id,
                task_id=task.id,
                account_id=account.id,
            ),
            status="failed",
            message="不得部分写入。",
            error_code="SCOPE_MISMATCH",
        )

    await session.refresh(turn)
    await session.refresh(run)
    await session.refresh(task)
    assert turn.status == "queued"
    assert turn.assistant_response is None
    assert run.status == "claimed"
    assert task.status == BrainTaskStatus.RUNNING


def _request(
    key: str,
    message: str | None = None,
    *,
    execution_preference: str = "AUTO",
    requested_skill_code: str | None = None,
) -> CreateConversationTurnRequest:
    return CreateConversationTurnRequest(
        client_message_id=key,
        message=message or f"message-{key}",
        execution_preference=execution_preference,
        requested_skill_code=requested_skill_code,
    )


def _decision(mode: TurnExecutionMode, **updates) -> TurnRouteDecision:
    values = {
        "mode": mode,
        "intent": f"{mode.value}_intent",
        "confidence": 0.99,
        "reason": "test route",
        "requires_account_context": mode
        in {
            TurnExecutionMode.QUERY,
            TurnExecutionMode.SKILL,
            TurnExecutionMode.TASK,
            TurnExecutionMode.ACTION,
        },
        "requires_operation_task": mode
        in {
            TurnExecutionMode.SKILL,
            TurnExecutionMode.TASK,
            TurnExecutionMode.ACTION,
        },
    }
    values.update(updates)
    return TurnRouteDecision(**values)


@pytest.mark.asyncio
async def test_explicit_skill_receives_account_scoped_capability_request(
    session,
    admin,
    monkeypatch,
) -> None:
    _account, thread, turn, run = await _turn_context(
        session,
        admin,
        key="typed-capability-request",
    )
    turn.user_input = "规划未来14天的10个选题"
    await session.commit()
    captured = {}

    async def capture_capability_request(*_args, **kwargs):
        captured["request"] = kwargs["capability_request"]
        return TurnExecutionResult(
            mode=TurnExecutionMode.SKILL,
            status="completed",
            response="ok",
        )

    monkeypatch.setattr(
        "app.services.turn_execution._execute_composite_skill",
        capture_capability_request,
    )

    async def resolve_contexts(*_args, **_kwargs):
        return [
            AttachmentContext(
                id=41,
                filename="brief.txt",
                mime_type="text/plain",
                parsed_context={"text": "brief"},
            ),
            AttachmentContext(
                id=43,
                filename="metrics.csv",
                mime_type="text/csv",
                parsed_context={"text": "metric,value"},
            ),
        ]

    monkeypatch.setattr(
        "app.services.turn_execution.resolve_attachment_contexts",
        resolve_contexts,
    )

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        CreateConversationTurnRequest(
            client_message_id="typed-capability-request",
            message="规划未来14天的10个选题",
            requested_skill_code="topic_planning",
            execution_preference="FORMAL_TASK",
            attachment_ids=[41, 41, 43],
        ),
    )

    assert result.status == "completed"
    capability_request = captured["request"]
    assert capability_request.org_id == admin.org_id
    assert capability_request.user_id == admin.id
    assert capability_request.account_id == thread.account_id
    assert capability_request.structured_input == {"days": 14, "topic_count": 10}
    assert capability_request.attachment_ids == [41, 43]
    assert [item.id for item in capability_request.attachment_contexts] == [41, 43]
    assert run.request_payload["structured_input"] == {"days": 14, "topic_count": 10}


@pytest.mark.asyncio
async def test_server_trusted_structured_input_reaches_operation_iteration_runtime(
    session,
    admin,
    monkeypatch,
) -> None:
    _account, _thread, turn, run = await _turn_context(
        session,
        admin,
        key="trusted-operation-iteration",
    )
    run.request_payload = {
        "trusted_structured_input": {
            "confirmed_review_artifact_id": 17,
            "cycle_days": 7,
        }
    }
    await session.commit()
    captured = {}

    async def capture_capability_request(*_args, **kwargs):
        captured["request"] = kwargs["capability_request"]
        return TurnExecutionResult(
            mode=TurnExecutionMode.SKILL,
            status="completed",
            response="ok",
        )

    monkeypatch.setattr(
        "app.services.turn_execution._execute_composite_skill",
        capture_capability_request,
    )
    request = CreateConversationTurnRequest(
        client_message_id="trusted-operation-iteration",
        message=turn.user_input,
        requested_skill_code="operation_iteration",
        execution_preference="FORMAL_TASK",
    )

    result = await execute_conversation_turn(session, admin, turn, run, request)

    assert result.status == "completed"
    assert captured["request"].structured_input == {
        "confirmed_review_artifact_id": 17,
        "cycle_days": 7,
        "topic_count": 5,
        "constraints": [],
    }


@pytest.mark.asyncio
async def test_answer_turn_stays_task_free(session, admin, monkeypatch) -> None:
    account, thread, turn, run = await _turn_context(session, admin, key="answer-1")
    turn.user_input = "你能做什么？"
    await session.commit()
    answer_calls: list[dict] = []

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.ANSWER,
            requires_account_context=False,
        )

    async def answer(*_args, **kwargs):
        answer_calls.append(kwargs)
        return "我是运营大脑。你可以问我账号数据、内容策划或运营执行问题。"

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.answer_turn", answer)
    result = await execute_conversation_turn(
        session, admin, turn, run, _request("answer-1", "你能做什么？")
    )

    assert result.mode is TurnExecutionMode.ANSWER
    assert result.task_id is None
    assert result.status == "completed"
    assert result.response == "我是运营大脑。你可以问我账号数据、内容策划或运营执行问题。"
    assert turn.assistant_response == result.response
    assert run.status == "completed"
    assert len(answer_calls) == 1
    answer_call = answer_calls[0]
    assert answer_call["operating_context"] == (
        f"当前平台：抖音；当前账号：{account.nickname}（账号 ID {account.id}）；"
        "当前项目：未选择项目。"
    )
    assert answer_call["history"] == []
    assert answer_call["scope"] == {
        "account_id": account.id,
        "thread_id": thread.id,
        "turn_id": turn.id,
    }
    assert callable(answer_call["stream_observer"])
    for model in (BrainTask, StrategyPlan, AgentInvocation, AgentToolCall):
        assert await session.scalar(select(func.count(model.id))) == 0


@pytest.mark.asyncio
async def test_deterministic_answer_skips_model_classification(session, admin, monkeypatch) -> None:
    """Catches deterministic hits regressing into unnecessary model classification."""

    _account, _thread, turn, run = await _turn_context(session, admin, key="deterministic-greeting")
    turn.user_input = "你好"
    await session.commit()

    async def should_not_classify(*_args, **_kwargs):
        raise AssertionError("deterministic greeting must not classify")

    async def answer(*_args, **_kwargs):
        return "你好，我是运营大脑。"

    monkeypatch.setattr(
        "app.services.turn_execution.brain_intelligence.classify_turn",
        should_not_classify,
    )
    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.answer_turn", answer)

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request("deterministic-greeting", "你好"),
    )

    assert result.mode is TurnExecutionMode.ANSWER
    assert result.response == "你好，我是运营大脑。"


@pytest.mark.asyncio
async def test_low_risk_question_uses_only_the_answer_model(session, admin, monkeypatch) -> None:
    _account, _thread, turn, run = await _turn_context(
        session,
        admin,
        key="single-model-answer",
    )
    message = "短视频运营有哪些常见误区？"
    turn.user_input = message
    await session.commit()
    answer_calls = 0

    async def should_not_classify(*_args, **_kwargs):
        raise AssertionError("low-risk questions must not call the classifier")

    async def answer(*_args, **_kwargs):
        nonlocal answer_calls
        answer_calls += 1
        return "常见误区包括目标不清、只追热点和不做数据复盘。"

    monkeypatch.setattr(
        "app.services.turn_execution.brain_intelligence.classify_turn",
        should_not_classify,
    )
    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.answer_turn", answer)

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request("single-model-answer", message),
    )

    assert result.mode is TurnExecutionMode.ANSWER
    assert result.status == "completed"
    assert answer_calls == 1


@pytest.mark.asyncio
async def test_answer_turn_streams_provider_deltas_before_persisting_final_turn(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="answer-live-stream")
    turn.user_input = "请实时回答"
    await session.commit()
    published: list[tuple[str, dict]] = []
    response_during_delta: list[str | None] = []

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.ANSWER,
            requires_account_context=False,
        )

    async def answer(*_args, stream_observer=None, **_kwargs):
        assert callable(stream_observer)
        await stream_observer(
            {
                "phase": "start",
                "agent_code": "00-decision",
                "model": "test-model",
            }
        )
        for delta in ("真", "实"):
            await stream_observer(
                {
                    "phase": "delta",
                    "agent_code": "00-decision",
                    "model": "test-model",
                    "delta": delta,
                }
            )
        await stream_observer(
            {
                "phase": "done",
                "agent_code": "00-decision",
                "model": "test-model",
                "content": "真实",
            }
        )
        return "真实"

    async def publish(event_type, payload, **_kwargs):
        published.append((event_type, payload))
        if event_type == "brain.runtime.message_delta":
            response_during_delta.append(turn.assistant_response)

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.answer_turn", answer)
    monkeypatch.setattr("app.orchestrator.brain_runtime.publish_realtime_event", publish)

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request("answer-live-stream", "请实时回答"),
    )

    assert result.response == "真实"
    assert response_during_delta == [None, None]
    assert [
        payload["delta"]
        for event_type, payload in published
        if event_type == "brain.runtime.message_delta"
    ] == ["真", "实"]
    assert published[0][0] == "brain.runtime.message_start"
    assert published[-2][0] == "brain.runtime.message_done"
    assert published[-1][0] == "brain.runtime.completed"
    assert (
        await session.scalar(
            select(func.count(Event.id)).where(
                Event.turn_id == turn.id,
                Event.type == "brain.runtime.message_delta",
            )
        )
        == 0
    )


@pytest.mark.asyncio
async def test_clarify_turn_persists_question_without_task(session, admin, monkeypatch) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="clarify-1")

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.CLARIFY,
            requires_account_context=True,
            requires_operation_task=False,
            missing_field="period",
            clarifying_question="你希望查看最近多少天？",
        )

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    result = await execute_conversation_turn(session, admin, turn, run, _request("clarify-1"))

    assert result.mode is TurnExecutionMode.CLARIFY
    assert result.task_id is None
    assert result.response == "你希望查看最近多少天？"
    assert turn.assistant_response == result.response
    assert await session.scalar(select(func.count(BrainTask.id))) == 0


@pytest.mark.asyncio
async def test_query_uses_authorized_account_and_records_one_skill_run(
    session, admin, monkeypatch
) -> None:
    account, thread, turn, run = await _turn_context(session, admin, key="query-1")
    invocations: list[dict] = []

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.QUERY,
            skill_code="account_data_query",
            requires_operation_task=False,
        )

    class Adapter:
        async def invoke(self, name, params, context):
            invocations.append(
                {
                    "name": name,
                    "params": dict(params),
                    "account_id": context.account_id,
                    "task_id": context.task_id,
                }
            )
            return {
                "account_id": context.account_id,
                "period": {
                    "days": params["days"],
                    "start": "2026-06-30",
                    "end": "2026-07-29",
                },
                "metrics": {
                    "play": {
                        "value": 42,
                        "source": "platform_export",
                        "observed_at": "2026-07-18",
                    },
                    "exposure": {"value": None},
                    "content_count": {"value": 3},
                    "follower_count": {"value": 100},
                    "new_followers": {"value": 2},
                    "like_count": {"value": 9},
                    "comment_count": {"value": 4},
                    "cover_click_rate": {"value": None},
                },
                "sources": [
                    {
                        "batch_id": 7,
                        "source_kind": "platform_export",
                        "confirmed_at": "2026-07-20T10:00:00+08:00",
                    }
                ],
                "coverage": {
                    "content_metrics": "available",
                    "audience": "missing",
                },
            }

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.build_runtime_tool_adapter", lambda: Adapter())
    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request("query-1"),
    )

    assert result.mode is TurnExecutionMode.QUERY
    assert result.task_id is None
    assert result.projections[0]["type"] == "account_data"
    assert result.projections[0]["account_id"] == account.id
    assert invocations == [
        {
            "name": "account.data_context",
            "params": {"days": 30},
            "account_id": account.id,
            "task_id": None,
        }
    ]
    skill_run = await session.scalar(select(SkillRun))
    assert skill_run is not None
    assert skill_run.thread_id == thread.id
    assert skill_run.turn_id == turn.id
    assert skill_run.run_id == run.id
    assert skill_run.task_id is None
    assert skill_run.status == "completed"
    assert skill_run.skill_version == 1
    assert skill_run.input_snapshot == {"account_id": account.id, "days": 30}
    assert skill_run.output_snapshot["account_id"] == account.id
    assert "数据周期：2026-06-30 至 2026-07-29（近 30 天）" in result.response
    assert "数据来源：平台导出批次 #7" in result.response
    assert "播放量：42（平台导出，观测于 2026-07-18）" in result.response
    assert "缺失数据：曝光量、封面点击率、受众画像" in result.response
    assert "account_id" not in result.response
    assert "{'" not in result.response
    assert await session.scalar(select(func.count(BrainTask.id))) == 0
    assert await session.scalar(select(func.count(StrategyPlan.id))) == 0
    assert await session.scalar(select(func.count(AgentInvocation.id))) == 0


@pytest.mark.asyncio
async def test_query_explains_pending_import_instead_of_claiming_data_was_read(
    session,
    admin,
    monkeypatch,
) -> None:
    account, _thread, turn, run = await _turn_context(
        session,
        admin,
        key="query-pending-import",
    )

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.QUERY,
            skill_code="account_data_query",
            requires_operation_task=False,
        )

    class Adapter:
        async def invoke(self, _name, _params, context):
            return {
                "account_id": context.account_id,
                "data_status": "pending_import",
                "query_window": {
                    "days": 30,
                    "start": "2026-07-02",
                    "end": "2026-07-31",
                },
                "data_period": None,
                "metrics": {},
                "sources": [],
                "coverage": {},
                "pending_imports": [
                    {
                        "batch_id": 8,
                        "status": "preview_ready",
                        "template_code": "douyin_period_aggregate_v1",
                        "row_count": 1,
                        "period_start": "2026-05-02",
                        "period_end": "2026-07-31",
                    }
                ],
            }

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.build_runtime_tool_adapter", lambda: Adapter())

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request("query-pending-import"),
    )

    assert result.projections[0]["data"]["data_status"] == "pending_import"
    assert "当前账号暂无已正式写入的可分析数据" in result.response
    assert "待确认导入批次 #8" in result.response
    assert "2026-05-02 至 2026-07-31" in result.response
    assert "请先在数据中心完成校验并正式写入" in result.response
    assert "数据周期：" not in result.response
    assert "已读取当前账号的数据" not in result.response
    assert result.projections[0]["account_id"] == account.id


@pytest.mark.asyncio
async def test_completed_query_duplicate_does_not_reclassify_or_reinvoke(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="query-duplicate")
    calls = 0
    tool_calls = 0

    async def classify(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _decision(
            TurnExecutionMode.QUERY,
            skill_code="account_data_query",
            requires_operation_task=False,
        )

    class Adapter:
        async def invoke(self, _name, _params, context):
            nonlocal tool_calls
            tool_calls += 1
            return {"account_id": context.account_id, "metrics": {}}

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.build_runtime_tool_adapter", lambda: Adapter())
    request = _request("query-duplicate")
    first = await execute_conversation_turn(session, admin, turn, run, request)

    async def should_not_classify(*_args, **_kwargs):
        raise AssertionError("terminal duplicate must not classify")

    monkeypatch.setattr(
        "app.services.turn_execution.brain_intelligence.classify_turn",
        should_not_classify,
    )
    repeated = await execute_conversation_turn(session, admin, turn, run, request)

    assert calls == 1
    assert tool_calls == 1
    assert repeated == first
    assert await session.scalar(select(func.count(SkillRun.id))) == 1
    assert await session.scalar(select(func.count(AgentToolCall.id))) == 0


@pytest.mark.asyncio
async def test_query_tool_failure_closes_run_and_skill_without_retry_or_leak(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="query-failure")
    tool_calls = 0

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.QUERY,
            skill_code="account_data_query",
            requires_operation_task=False,
        )

    class Adapter:
        async def invoke(self, *_args, **_kwargs):
            nonlocal tool_calls
            tool_calls += 1
            raise RuntimeError("provider-secret-must-not-leak")

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.build_runtime_tool_adapter", lambda: Adapter())
    request = _request("query-failure")
    first = await execute_conversation_turn(session, admin, turn, run, request)
    repeated = await execute_conversation_turn(session, admin, turn, run, request)

    skill_run = await session.scalar(select(SkillRun))
    assert first == repeated
    assert first.status == "failed"
    assert first.error_code == "QUERY_TOOL_UNAVAILABLE"
    assert first.projections == []
    assert "provider-secret" not in first.response
    assert skill_run is not None
    assert skill_run.status == "failed"
    assert skill_run.error_code == "QUERY_TOOL_UNAVAILABLE"
    assert run.status == "failed"
    assert run.error_detail is None
    assert turn.assistant_response == first.response
    assert tool_calls == 1
    events = list(await session.scalars(select(Event)))
    assert all("provider-secret" not in str(event.payload) for event in events)


@pytest.mark.asyncio
async def test_query_retryable_infrastructure_failure_bubbles_to_the_worker(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="query-retryable")

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.QUERY,
            skill_code="account_data_query",
            requires_operation_task=False,
        )

    class Adapter:
        async def invoke(self, *_args, **_kwargs):
            raise HTTPException(status_code=503, detail="provider-secret")

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.build_runtime_tool_adapter", lambda: Adapter())

    with pytest.raises(HTTPException) as caught:
        await execute_conversation_turn(
            session,
            admin,
            turn,
            run,
            _request("query-retryable"),
        )

    assert caught.value.status_code == 503
    skill_run = await session.scalar(select(SkillRun))
    assert skill_run is not None
    assert skill_run.status == "running"
    assert turn.assistant_response is None
    assert run.status == "claimed"


@pytest.mark.parametrize("tool_account_id", [None, 999999])
@pytest.mark.asyncio
async def test_query_rejects_missing_or_cross_account_tool_result(
    session, admin, monkeypatch, tool_account_id
) -> None:
    account, _thread, turn, run = await _turn_context(
        session, admin, key=f"query-scope-{tool_account_id}"
    )

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.QUERY,
            skill_code="account_data_query",
            requires_operation_task=False,
        )

    class Adapter:
        async def invoke(self, *_args, **_kwargs):
            return {
                "account_id": tool_account_id,
                "secret_raw_data": "must-not-project",
            }

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.build_runtime_tool_adapter", lambda: Adapter())
    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request(f"query-scope-{tool_account_id}"),
    )

    assert result.status == "failed"
    assert result.error_code == "TOOL_RESULT_SCOPE_MISMATCH"
    assert result.projections == []
    assert account.id != tool_account_id
    events = list(await session.scalars(select(Event)))
    assert all("secret_raw_data" not in str(event.payload) for event in events)


@pytest.mark.asyncio
async def test_intelligence_unavailable_is_structured_blocked_not_answer(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="intelligence-down")

    async def classify(*_args, **_kwargs):
        from app.orchestrator.brain_intelligence import IntelligenceUnavailable

        raise IntelligenceUnavailable("raw-provider-failure")

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request("intelligence-down"),
    )

    assert result.status == "blocked"
    assert result.error_code == "INTELLIGENCE_UNAVAILABLE"
    assert "raw-provider" not in result.response
    assert run.status == "blocked"
    assert run.error_detail is None
    assert await session.scalar(select(func.count(BrainTask.id))) == 0


@pytest.mark.asyncio
async def test_unavailable_skill_is_structured_blocked_without_artifact(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="skill-blocked")

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.SKILL,
            skill_code="not_implemented_skill",
        )

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request("skill-blocked"),
    )

    assert result.status == "blocked"
    assert result.error_code == "INTELLIGENCE_UNAVAILABLE"
    assert result.task_id is None
    assert run.status == "blocked"
    assert run.error_code == "INTELLIGENCE_UNAVAILABLE"
    assert await session.scalar(select(func.count(ContentItem.id))) == 0
    assert await session.scalar(select(func.count(BrainTask.id))) == 0


@pytest.mark.asyncio
async def test_unknown_explicit_skill_is_blocked_without_formal_side_effects(
    session,
    admin,
) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="explicit-unknown-skill")

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request(
            "explicit-unknown-skill",
            requested_skill_code="not_registered",
        ),
    )

    assert result.status == "blocked"
    assert result.error_code == "UNKNOWN_SKILL"
    assert result.task_id is None
    assert result.projections == [
        {
            "type": "execution_blocked",
            "skill_code": "not_registered",
            "code": "UNKNOWN_SKILL",
            "recovery_action": "请从当前公开能力目录重新选择。",
        }
    ]
    assert run.status == "blocked"
    for model in (SkillRun, Deliverable, ContentItem, BrainTask):
        assert await session.scalar(select(func.count(model.id))) == 0


@pytest.mark.asyncio
async def test_platform_incompatible_explicit_skill_never_reaches_executor(
    session,
    admin,
    monkeypatch,
) -> None:
    _account, _thread, turn, run = await _turn_context(
        session, admin, key="explicit-platform-incompatible"
    )
    definition = replace(
        skill_registry.get("account_inspection"),
        supported_platforms=frozenset({"xiaohongshu"}),
    )
    monkeypatch.setattr(
        "app.services.turn_execution.skill_registry",
        SkillRegistry([definition]),
        raising=False,
    )

    async def must_not_execute(*_args, **_kwargs):
        raise AssertionError("platform-incompatible Skill must not execute")

    monkeypatch.setattr(
        "app.services.turn_execution.skill_runtime.execute",
        must_not_execute,
    )
    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request(
            "explicit-platform-incompatible",
            requested_skill_code="account_inspection",
        ),
    )

    assert result.status == "blocked"
    assert result.error_code == "UNSUPPORTED_PLATFORM"
    assert result.task_id is None
    for model in (SkillRun, Deliverable, ContentItem, BrainTask):
        assert await session.scalar(select(func.count(model.id))) == 0


@pytest.mark.asyncio
async def test_unpublished_explicit_skill_is_blocked_without_skill_run(
    session,
    admin,
    monkeypatch,
) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="explicit-unpublished")
    public_definition = skill_registry.get("account_inspection")
    private_definition = replace(
        public_definition,
        code="internal_shadow_skill",
    )
    monkeypatch.setattr(
        "app.services.turn_execution.skill_registry",
        SkillRegistry([public_definition, private_definition]),
        raising=False,
    )

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request(
            "explicit-unpublished",
            requested_skill_code="internal_shadow_skill",
        ),
    )

    assert result.status == "blocked"
    assert result.error_code == "UNPUBLISHED_SKILL"
    assert result.task_id is None
    for model in (SkillRun, Deliverable, ContentItem, BrainTask):
        assert await session.scalar(select(func.count(model.id))) == 0


@pytest.mark.parametrize(
    "classified_mode",
    [
        TurnExecutionMode.SKILL,
        TurnExecutionMode.TASK,
        TurnExecutionMode.ACTION,
    ],
)
@pytest.mark.asyncio
async def test_discuss_only_prevents_workflow_execution(
    session, admin, monkeypatch, classified_mode
) -> None:
    key = f"discuss-{classified_mode.value}"
    _account, _thread, turn, run = await _turn_context(session, admin, key=key)
    started = 0

    async def classify(*_args, **_kwargs):
        values = {}
        if classified_mode is TurnExecutionMode.SKILL:
            values["skill_code"] = "account_inspection"
        return _decision(classified_mode, **values)

    async def should_not_start(*_args, **_kwargs):
        nonlocal started
        started += 1
        raise AssertionError("DISCUSS_ONLY must not start routed work")

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.runtime_graph.start_routed", should_not_start)
    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request(key, execution_preference="DISCUSS_ONLY"),
    )

    assert result.status == "completed"
    assert result.task_id is None
    assert "未执行" in result.response
    assert started == 0
    assert await session.scalar(select(func.count(BrainTask.id))) == 0
    assert await session.scalar(select(func.count(SkillRun.id))) == 0


@pytest.mark.asyncio
async def test_formal_task_forces_non_clarify_route_into_task(session, admin, monkeypatch) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="formal-task")
    routed_modes: list[TurnExecutionMode] = []

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.ANSWER,
            requires_account_context=False,
        )

    async def start_routed(_session, task, **kwargs):
        routed_modes.append(kwargs["route_decision"].mode)
        task.status = BrainTaskStatus.COMPLETED
        await _session.commit()
        return task

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.runtime_graph.start_routed", start_routed)
    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request("formal-task", execution_preference="FORMAL_TASK"),
        execution_owner="operation-worker",
    )

    assert result.mode is TurnExecutionMode.TASK
    assert result.task_id is not None
    assert routed_modes == [TurnExecutionMode.TASK]


@pytest.mark.asyncio
async def test_migrated_operation_never_enters_legacy_operation_task(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run = await _turn_context(
        session, admin, key="typed-topic-route"
    )
    message = "给我做7天5个选题"
    turn.user_input = message
    await session.commit()
    captured: list[str] = []

    async def execute_skill(*_args, **kwargs):
        captured.append(kwargs["decision"].skill_code)
        return TurnExecutionResult(
            mode=TurnExecutionMode.SKILL,
            status="completed",
            response="typed skill",
        )

    async def forbidden_legacy(*_args, **_kwargs):
        raise AssertionError("migrated operation must not enter legacy task graph")

    monkeypatch.setattr(
        "app.services.turn_execution._execute_composite_skill", execute_skill
    )
    monkeypatch.setattr(
        "app.services.turn_execution._execute_operation_task", forbidden_legacy
    )

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request("typed-topic-route", message=message),
    )

    assert result.mode is TurnExecutionMode.SKILL
    assert captured == ["topic_planning"]


@pytest.mark.asyncio
async def test_legacy_runtime_rejects_migrated_operation_intent() -> None:
    decision = _decision(
        TurnExecutionMode.TASK,
        intent="topic_planning",
    )

    with pytest.raises(ValueError, match="MIGRATED_OPERATION_REQUIRES_TYPED_SKILL"):
        await runtime_graph.start_routed(
            None,
            SimpleNamespace(),
            route_decision=decision,
        )


@pytest.mark.parametrize(
    "mode",
    [TurnExecutionMode.TASK, TurnExecutionMode.ACTION],
)
@pytest.mark.asyncio
async def test_strategy_task_creates_exactly_one_task_and_uses_routed_runtime(
    session, admin, monkeypatch, mode
) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="task-1")
    started: list[tuple[int, int]] = []

    async def classify(*_args, **_kwargs):
        return _decision(mode)

    async def start_routed(_session, task, **kwargs):
        started.append((task.id, kwargs["agent_run_id"]))
        task.status = BrainTaskStatus.COMPLETED
        task.progress = 100
        task.current_focus = "done"
        await _session.commit()
        return task

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.runtime_graph.start_routed", start_routed)
    request = _request("task-1")
    first = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        request,
        execution_owner="operation-worker",
    )
    repeated = await execute_conversation_turn(session, admin, turn, run, request)

    assert first.task_id is not None
    assert repeated == first
    assert run.task_id == first.task_id
    assert started == [(first.task_id, run.id)]
    assert await session.scalar(select(func.count(BrainTask.id))) == 1


class _OperationWriteParams(BaseModel):
    value: str


@pytest.mark.asyncio
async def test_operation_route_propagates_owner_into_real_account_write(
    session, admin, monkeypatch
) -> None:
    account, _thread, turn, run = await _turn_context(
        session, admin, key="operation-owner-write"
    )
    owner = "operation-write-worker"
    run.status = "running"
    run.phase = "running"
    run.lease_owner = owner
    run.leased_until = datetime.now(UTC) + timedelta(minutes=5)
    await session.commit()
    calls = 0

    async def classify(*_args, **_kwargs):
        return _decision(TurnExecutionMode.TASK)

    async def handler(
        params: _OperationWriteParams,
        _context: ToolExecutionContext,
    ) -> dict:
        nonlocal calls
        calls += 1
        return {"value": params.value}

    executor = DurableToolExecutor(
        ToolAdapter(
            [
                ToolSpec(
                    name="provider.operation_upsert",
                    handler=handler,
                    params_model=_OperationWriteParams,
                    side_effect_level="idempotent_write",
                )
            ]
        ),
        _allow_test_account_lane_fallback=True,
    )

    async def start_routed(_session, task, **kwargs):
        outcome = await executor.execute(
            task=task,
            user=admin,
            request=RuntimeToolCall(
                tool_code="provider.operation_upsert",
                arguments={"value": "persisted"},
                purpose="persist operation output",
                idempotency_key="operation-owner-write",
            ),
            account_id=account.id,
            run_id=kwargs["agent_run_id"],
            execution_owner=kwargs["execution_owner"],
        )
        assert outcome.status == "success"
        task.status = BrainTaskStatus.COMPLETED
        task.progress = 100
        await _session.commit()
        return task

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.runtime_graph.start_routed", start_routed)

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request("operation-owner-write"),
        execution_owner=owner,
    )

    assert result.status == "completed"
    assert calls == 1
    tool_call = await session.scalar(
        select(AgentToolCall).where(
            AgentToolCall.idempotency_key == "operation-owner-write"
        )
    )
    assert tool_call is not None
    assert tool_call.status == "success"


@pytest.mark.asyncio
async def test_operation_route_without_formal_owner_is_blocked_before_runtime(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run = await _turn_context(
        session, admin, key="operation-owner-required"
    )

    async def classify(*_args, **_kwargs):
        return _decision(TurnExecutionMode.TASK)

    async def should_not_start(*_args, **_kwargs):
        raise AssertionError("an unleased operation must not enter the runtime")

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.runtime_graph.start_routed", should_not_start)

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request("operation-owner-required"),
    )

    assert result.status == "blocked"
    assert result.error_code == "EXECUTION_OWNER_REQUIRED"
    assert result.task_id is None


@pytest.mark.asyncio
async def test_operation_start_failure_closes_task_run_and_turn_without_replay(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="task-failure")
    starts = 0

    async def classify(*_args, **_kwargs):
        return _decision(TurnExecutionMode.TASK)

    async def start_routed(*_args, **_kwargs):
        nonlocal starts
        starts += 1
        raise RuntimeError("runtime-secret")

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.runtime_graph.start_routed", start_routed)
    request = _request("task-failure")
    first = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        request,
        execution_owner="operation-worker",
    )
    repeated = await execute_conversation_turn(session, admin, turn, run, request)
    task = await session.get(BrainTask, first.task_id)

    assert first == repeated
    assert first.status == "failed"
    assert first.error_code == "OPERATION_RUNTIME_FAILED"
    assert "runtime-secret" not in first.response
    assert run.status == "failed"
    assert run.error_detail is None
    assert task is not None
    assert task.status == BrainTaskStatus.FAILED
    assert turn.assistant_response == first.response
    assert starts == 1
    events = list(await session.scalars(select(Event)))
    assert all("runtime-secret" not in str(event.payload) for event in events)


@pytest.mark.asyncio
async def test_operation_retryable_infrastructure_failure_bubbles_to_the_worker(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="task-retryable")

    async def classify(*_args, **_kwargs):
        return _decision(TurnExecutionMode.TASK)

    async def start_routed(*_args, **_kwargs):
        raise HTTPException(status_code=503, detail="runtime-provider-secret")

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.runtime_graph.start_routed", start_routed)

    with pytest.raises(HTTPException) as caught:
        await execute_conversation_turn(
            session,
            admin,
            turn,
            run,
            _request("task-retryable"),
            execution_owner="operation-worker",
        )

    assert caught.value.status_code == 503
    await session.refresh(run)
    await session.refresh(turn)
    task = await session.get(BrainTask, run.task_id)
    assert task is not None
    assert task.status == BrainTaskStatus.RUNNING
    assert run.status == "claimed"
    assert turn.assistant_response is None


@pytest.mark.parametrize(
    ("runtime_state", "expected_run_status", "expected_error"),
    [
        ("waiting_permission", "waiting_permission", None),
        ("waiting_decision", "waiting_decision", None),
        ("waiting_user", "waiting_user", None),
        ("failed", "failed", "OPERATION_RUNTIME_FAILED"),
        ("stopped", "stopped", "OPERATION_STOPPED"),
    ],
)
@pytest.mark.asyncio
async def test_operation_runtime_state_is_persisted_without_reexecution(
    session,
    admin,
    monkeypatch,
    runtime_state,
    expected_run_status,
    expected_error,
) -> None:
    key = f"task-state-{runtime_state}"
    _account, _thread, turn, run = await _turn_context(session, admin, key=key)
    starts = 0

    async def classify(*_args, **_kwargs):
        return _decision(TurnExecutionMode.TASK)

    async def start_routed(_session, task, **_kwargs):
        nonlocal starts
        starts += 1
        return task

    async def status(*_args, **_kwargs):
        return runtime_state

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.runtime_graph.start_routed", start_routed)
    monkeypatch.setattr("app.services.turn_execution.runtime_status", status)
    request = _request(key)
    first = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        request,
        execution_owner="operation-worker",
    )
    repeated = await execute_conversation_turn(session, admin, turn, run, request)
    task = await session.get(BrainTask, first.task_id)

    assert first == repeated
    assert first.status == runtime_state
    assert first.error_code == expected_error
    assert run.status == expected_run_status
    assert starts == 1
    assert task is not None
    if runtime_state in {"failed", "stopped"}:
        assert task.status == BrainTaskStatus.FAILED
    else:
        assert task.status == BrainTaskStatus.PENDING_CONFIRMATION


@pytest.mark.asyncio
async def test_task_free_events_have_turn_lineage_and_publish_after_commit(
    session, admin, monkeypatch
) -> None:
    account, thread, turn, run = await _turn_context(session, admin, key="lineage-1")
    published: list[int] = []

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.ANSWER,
            requires_account_context=False,
        )

    async def answer(*_args, **_kwargs):
        return "这是一个可追踪的流式回复。"

    async def publish(_event_type, _payload, **kwargs):
        event_id = kwargs["event_id"]
        row = await session.get(Event, event_id)
        await session.refresh(turn)
        assert row is not None
        assert turn.assistant_response
        published.append(event_id)

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.answer_turn", answer)
    monkeypatch.setattr("app.orchestrator.brain_runtime.publish_realtime_event", publish)
    await execute_conversation_turn(session, admin, turn, run, _request("lineage-1"))

    events = list(
        await session.scalars(
            select(Event).where(Event.type.like("brain.runtime.%")).order_by(Event.id)
        )
    )
    assert set(published) == {event.id for event in events}
    assert len(published) == len(events)
    assert events
    for event in events:
        payload = event.payload
        assert payload["org_id"] == admin.org_id
        assert payload["account_id"] == account.id
        assert payload["thread_id"] == thread.id
        assert payload["turn_id"] == turn.id
        assert payload["run_id"] == run.id
        assert payload["client_message_id"] == "lineage-1"
        assert payload["task_id"] is None
        assert event.thread_id == thread.id
        assert event.turn_id == turn.id
        assert event.run_id == run.id
