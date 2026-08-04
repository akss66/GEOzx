"""Transaction-neutral RunRevision service tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models import (
    Account,
    AgentRun,
    BrainTask,
    ConversationThread,
    ConversationTurn,
    RunRevision,
    SkillRun,
)
from app.models.enums import AccountStatus, BrainTaskStatus, BrainTaskType, Platform
from app.orchestrator.checkpoint_graph_contracts import require_checkpoint_graph_contract
from app.orchestrator.runtime_scope import RuntimeScope
from app.orchestrator.step_dependencies import ConstraintPath, build_invalidation_plan
from app.schemas.run_revision import NoRevisionRequired, RevisionResolution
from app.services.run_revisions import (
    RevisionStateConflict,
    complete_revision,
    create_revision_record,
    fall_back_to_full_recompute,
    mark_revision_running,
    require_manual_reconciliation,
)


async def _lineage(session, admin, suffix: str = "service") -> SimpleNamespace:
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname=f"revision-{suffix}",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    await session.flush()
    thread = ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title=f"revision-{suffix}",
    )
    session.add(thread)
    await session.flush()
    source_turn = ConversationTurn(
        thread_id=thread.id,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id=f"source-{suffix}",
        user_input="source",
    )
    session.add(source_turn)
    await session.flush()
    revision_turn = ConversationTurn(
        thread_id=thread.id,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id=f"revision-{suffix}",
        user_input="supplement",
        target_turn_id=source_turn.id,
        steering_mode="supplement",
    )
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title=f"revision-{suffix}",
        type=BrainTaskType.CONTENT_CREATION,
        status=BrainTaskStatus.RUNNING,
    )
    session.add_all([revision_turn, task])
    await session.flush()
    source_run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        task_id=task.id,
        thread_id=thread.id,
        turn_id=source_turn.id,
        client_message_id=f"source-{suffix}",
        status="completed",
        phase="completed",
        request_payload={},
        result_payload={},
    )
    revision_run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        task_id=task.id,
        thread_id=thread.id,
        turn_id=revision_turn.id,
        client_message_id=f"revision-{suffix}",
        status="waiting_predecessor",
        phase="waiting_predecessor",
        request_payload={},
        result_payload={},
    )
    session.add_all([source_run, revision_run])
    await session.flush()
    source_skill = SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=source_turn.id,
        run_id=source_run.id,
        task_id=task.id,
        idempotency_key=f"source-skill-{suffix}",
        skill_code="operation_iteration",
        skill_version=1,
        status="completed",
        input_snapshot={},
        input_hash="a" * 64,
        output_snapshot={},
    )
    revision_skill = SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=revision_turn.id,
        run_id=revision_run.id,
        task_id=task.id,
        idempotency_key=f"revision-skill-{suffix}",
        skill_code="operation_iteration",
        skill_version=1,
        status="running",
        input_snapshot={},
        input_hash="a" * 64,
        output_snapshot={},
    )
    session.add_all([source_skill, revision_skill])
    await session.flush()
    common = dict(
        org_id=admin.org_id,
        user_id=admin.id,
        account_id=account.id,
        thread_id=thread.id,
        task_id=task.id,
    )
    return SimpleNamespace(
        source=RuntimeScope(
            **common,
            turn_id=source_turn.id,
            run_id=source_run.id,
            skill_run_id=source_skill.id,
        ),
        revision=RuntimeScope(
            **common,
            turn_id=revision_turn.id,
            run_id=revision_run.id,
            skill_run_id=revision_skill.id,
        ),
    )


def _resolution(mode: str) -> RevisionResolution:
    contract = require_checkpoint_graph_contract("operation_iteration", 1)
    order = tuple(step.key for step in contract.steps)
    if mode == "partial":
        return RevisionResolution(
            mode="partial",
            reason=None,
            execute_steps=order[3:],
            reused_steps=order[:3],
            source_checkpoint_ids=(101, 102, 103),
            blocking_receipt_ids=(),
            plan_hash="b" * 64,
        )
    return RevisionResolution(
        mode=mode,
        reason=(
            "external_write_ambiguous" if mode == "manual_reconciliation" else "checkpoint_missing"
        ),
        execute_steps=() if mode == "manual_reconciliation" else order,
        reused_steps=(),
        source_checkpoint_ids=(),
        blocking_receipt_ids=(91,) if mode == "manual_reconciliation" else (),
        plan_hash="c" * 64,
    )


@pytest.mark.parametrize("mode", ["partial", "full_recompute", "manual_reconciliation"])
async def test_create_revision_record_persists_strict_plan_without_commit(
    session, admin, monkeypatch, mode
) -> None:
    scopes = await _lineage(session, admin, suffix=mode)
    invalidation = build_invalidation_plan("operation_iteration", {ConstraintPath.OFFER_TERMS})

    async def _forbid_commit():
        raise AssertionError("service must not commit")

    monkeypatch.setattr(session, "commit", _forbid_commit)
    revision = await create_revision_record(
        session,
        source_scope=scopes.source,
        revision_scope=scopes.revision,
        invalidation=invalidation,
        resolution=_resolution(mode),
    )

    assert revision.id is not None
    assert revision.mode == mode
    assert revision.status == "planned"
    assert revision.changed_constraints == {"offer_terms": {"operation": "changed"}}


async def test_empty_diff_does_not_create_revision(session, admin) -> None:
    scopes = await _lineage(session, admin, suffix="empty")

    result = await create_revision_record(
        session,
        source_scope=scopes.source,
        revision_scope=scopes.revision,
        invalidation=build_invalidation_plan("operation_iteration", set()),
    )

    assert isinstance(result, NoRevisionRequired)
    assert await session.scalar(select(func.count(RunRevision.id))) == 0


async def test_revision_write_rejects_unvalidated_resolution_dict(session, admin) -> None:
    scopes = await _lineage(session, admin, suffix="dto-bypass")
    with pytest.raises(TypeError, match="RevisionResolution DTO"):
        await create_revision_record(
            session,
            source_scope=scopes.source,
            revision_scope=scopes.revision,
            invalidation=build_invalidation_plan(
                "operation_iteration", {ConstraintPath.OFFER_TERMS}
            ),
            resolution={"mode": "partial", "affected_steps": ["secret"]},
        )
    assert await session.scalar(select(func.count(RunRevision.id))) == 0


async def test_transitions_are_allowed_idempotent_and_transaction_neutral(
    session, admin, monkeypatch
) -> None:
    scopes = await _lineage(session, admin, suffix="transition")
    revision = await create_revision_record(
        session,
        source_scope=scopes.source,
        revision_scope=scopes.revision,
        invalidation=build_invalidation_plan("operation_iteration", {ConstraintPath.OFFER_TERMS}),
        resolution=_resolution("partial"),
    )

    async def _forbid_commit():
        raise AssertionError("service must not commit")

    monkeypatch.setattr(session, "commit", _forbid_commit)
    running = await mark_revision_running(session, revision_id=revision.id)
    replay = await mark_revision_running(session, revision_id=revision.id)
    completed = await complete_revision(session, revision_id=revision.id)
    completed_replay = await complete_revision(session, revision_id=revision.id)

    assert running.id == replay.id == completed.id == completed_replay.id
    assert completed.status == "completed"
    assert completed.started_at is not None
    assert completed.finished_at is not None
    with pytest.raises(RevisionStateConflict) as error:
        await require_manual_reconciliation(
            session, revision_id=revision.id, reason="external_write_ambiguous"
        )
    assert error.value.code == "REVISION_STATE_CONFLICT"


async def test_full_fallback_is_rejected_after_revision_starts(session, admin) -> None:
    scopes = await _lineage(session, admin, suffix="late-fallback")
    revision = await create_revision_record(
        session,
        source_scope=scopes.source,
        revision_scope=scopes.revision,
        invalidation=build_invalidation_plan(
            "operation_iteration", {ConstraintPath.OFFER_TERMS}
        ),
        resolution=_resolution("partial"),
    )
    await mark_revision_running(session, revision_id=revision.id)

    with pytest.raises(RevisionStateConflict, match="cannot fall back"):
        await fall_back_to_full_recompute(
            session, revision_id=revision.id, reason="checkpoint_missing"
        )


async def test_fallback_and_manual_reconciliation_recompute_plan_hash(session, admin) -> None:
    first = await _lineage(session, admin, suffix="fallback")
    second = await _lineage(session, admin, suffix="manual")
    invalidation = build_invalidation_plan("operation_iteration", {ConstraintPath.OFFER_TERMS})
    fallback = await create_revision_record(
        session,
        source_scope=first.source,
        revision_scope=first.revision,
        invalidation=invalidation,
        resolution=_resolution("partial"),
    )
    manual = await create_revision_record(
        session,
        source_scope=second.source,
        revision_scope=second.revision,
        invalidation=invalidation,
        resolution=_resolution("partial"),
    )

    old_hash = fallback.plan_hash
    await fall_back_to_full_recompute(
        session, revision_id=fallback.id, reason="checkpoint_output_corrupt"
    )
    await require_manual_reconciliation(
        session, revision_id=manual.id, reason="external_write_ambiguous"
    )

    assert fallback.mode == "full_recompute"
    assert fallback.plan_hash != old_hash
    assert fallback.reused_steps == []
    assert manual.mode == "manual_reconciliation"
    assert manual.manual_reconciliation_reason == "external_write_ambiguous"

    with pytest.raises(ValueError, match="stable fallback reason"):
        await fall_back_to_full_recompute(
            session, revision_id=fallback.id, reason="provider raw secret"
        )
