"""Pure revision resolution plus transaction-neutral RunRevision transitions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentRun,
    BrainTask,
    ConversationThread,
    ConversationTurn,
    RunRevision,
    SkillRun,
)
from app.orchestrator.checkpoint_graph_contracts import (
    CheckpointGraphContract,
    require_checkpoint_graph_contract,
)
from app.orchestrator.runtime_scope import RuntimeScope
from app.orchestrator.step_dependencies import InvalidationPlan, build_invalidation_plan
from app.schemas.run_revision import NoRevisionRequired, RevisionResolution
from app.services.checkpoint_freshness import load_transaction_db_now
from app.services.checkpoint_hashing import revision_plan_hash

CandidateOutcome = Literal["reusable", "full_recompute", "manual_reconciliation"]
ResolutionMode = Literal["partial", "full_recompute", "manual_reconciliation"]

_FULL_RECOMPUTE_REASONS = frozenset(
    {
        "unknown_skill",
        "unknown_constraint",
        "graph_skill_mismatch",
        "dependency_full_recompute",
        "checkpoint_source_skill_missing",
        "checkpoint_missing",
        "checkpoint_contract_mismatch",
        "checkpoint_input_mismatch",
        "checkpoint_output_corrupt",
        "checkpoint_input_projection_missing",
        "checkpoint_source_lineage_mismatch",
        "checkpoint_candidate_ambiguous",
        "artifact_missing",
        "artifact_superseded",
        "artifact_hash_mismatch",
        "evidence_scope_mismatch",
        "freshness_validator_missing",
        "freshness_stamp_missing",
        "freshness_expired",
        "freshness_watermark_changed",
        "dependency_output_missing",
        "checkpoint_read_in_flight",
    }
)
_MANUAL_REASONS = frozenset(
    {
        "external_write_in_flight",
        "external_write_ambiguous",
        "non_idempotent_effect_completed",
        "idempotent_replay_contract_missing",
        "provider_idempotency_key_unstable",
        "approved_artifact_changed",
        "compensation_required",
        "source_checkpoint_manual",
    }
)


def _validate_reason(mode: ResolutionMode, reason: str | None) -> None:
    if mode == "partial":
        if reason is not None:
            raise ValueError("partial plan cannot persist a fallback reason")
        return
    allowed = _FULL_RECOMPUTE_REASONS if mode == "full_recompute" else _MANUAL_REASONS
    if reason not in allowed and not (
        mode == "full_recompute" and reason is not None and reason.startswith("invalid_graph:")
    ):
        raise ValueError(f"stable fallback reason required for {mode}")


@dataclass(frozen=True)
class CheckpointCandidateVerdict:
    step_key: str
    checkpoint_id: int
    source_run_id: int
    source_turn_id: int
    source_skill_run_id: int
    outcome: CandidateOutcome
    reason: str | None = None
    blocking_receipt_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        for value in (
            self.checkpoint_id,
            self.source_run_id,
            self.source_turn_id,
            self.source_skill_run_id,
        ):
            if type(value) is not int or value <= 0:
                raise ValueError("Candidate lineage identifiers must be positive integers")
        if self.outcome != "reusable" and not self.reason:
            raise ValueError("Non-reusable candidate requires a stable reason")


class RevisionStateConflict(RuntimeError):
    code = "REVISION_STATE_CONFLICT"


class RevisionScopeConflict(RuntimeError):
    code = "REVISION_SCOPE_CONFLICT"


def _resolution(
    *,
    mode: ResolutionMode,
    reason: str | None,
    execute_steps: tuple[str, ...],
    reused_steps: tuple[str, ...],
    source_checkpoint_ids: tuple[int, ...] = (),
    blocking_receipt_ids: tuple[int, ...] = (),
) -> RevisionResolution:
    _validate_reason(mode, reason)
    payload = {
        "mode": mode,
        "reason": reason,
        "execute_steps": list(execute_steps),
        "reused_steps": list(reused_steps),
        "source_checkpoint_ids": list(source_checkpoint_ids),
        "blocking_receipt_ids": list(blocking_receipt_ids),
    }
    return RevisionResolution(
        mode=mode,
        reason=reason,
        execute_steps=execute_steps,
        reused_steps=reused_steps,
        source_checkpoint_ids=source_checkpoint_ids,
        blocking_receipt_ids=blocking_receipt_ids,
        plan_hash=revision_plan_hash(payload),
    )


def resolve_revision_policy(
    *,
    invalidation: InvalidationPlan,
    contract: CheckpointGraphContract,
    expected_source_run_id: int,
    expected_source_turn_id: int,
    expected_source_skill_run_id: int,
    candidates: Sequence[CheckpointCandidateVerdict],
) -> RevisionResolution | NoRevisionRequired:
    if not invalidation.changed_constraints:
        return NoRevisionRequired()
    order = tuple(step.key for step in contract.steps)
    manual = tuple(
        candidate
        for candidate in candidates
        if candidate.outcome == "manual_reconciliation" and candidate.step_key in order
    )
    if manual:
        first = min(manual, key=lambda item: order.index(item.step_key))
        receipt_ids = tuple(
            sorted({receipt for item in manual for receipt in item.blocking_receipt_ids})
        )
        return _resolution(
            mode="manual_reconciliation",
            reason=first.reason,
            execute_steps=(),
            reused_steps=(),
            blocking_receipt_ids=receipt_ids,
        )

    candidate_by_step: dict[str, CheckpointCandidateVerdict] = {}
    for candidate in candidates:
        if candidate.step_key not in order:
            return _resolution(
                mode="full_recompute",
                reason="checkpoint_contract_mismatch",
                execute_steps=order,
                reused_steps=(),
            )
        existing = candidate_by_step.get(candidate.step_key)
        if existing is not None and existing != candidate:
            return _resolution(
                mode="full_recompute",
                reason="checkpoint_candidate_ambiguous",
                execute_steps=order,
                reused_steps=(),
            )
        candidate_by_step[candidate.step_key] = candidate

    if invalidation.mode == "full_recompute":
        return _resolution(
            mode="full_recompute",
            reason=invalidation.fallback_reason or "dependency_full_recompute",
            execute_steps=order,
            reused_steps=(),
        )

    execute = _canonical_execute_steps(invalidation=invalidation, contract=contract)

    reusable_steps: list[str] = []
    source_checkpoint_ids: list[int] = []
    for step in contract.steps:
        if step.key in execute:
            continue
        candidate_for_step = candidate_by_step.get(step.key)
        if candidate_for_step is None:
            return _resolution(
                mode="full_recompute",
                reason="checkpoint_missing",
                execute_steps=order,
                reused_steps=(),
            )
        if (
            candidate_for_step.source_run_id != expected_source_run_id
            or candidate_for_step.source_turn_id != expected_source_turn_id
            or candidate_for_step.source_skill_run_id != expected_source_skill_run_id
        ):
            return _resolution(
                mode="full_recompute",
                reason="checkpoint_source_lineage_mismatch",
                execute_steps=order,
                reused_steps=(),
            )
        if candidate_for_step.outcome == "full_recompute":
            return _resolution(
                mode="full_recompute",
                reason=candidate_for_step.reason,
                execute_steps=order,
                reused_steps=(),
            )
        reusable_steps.append(step.key)
        source_checkpoint_ids.append(candidate_for_step.checkpoint_id)

    ordered_execute = tuple(key for key in order if key in execute)
    return _resolution(
        mode="partial",
        reason=None,
        execute_steps=ordered_execute,
        reused_steps=tuple(reusable_steps),
        source_checkpoint_ids=tuple(source_checkpoint_ids),
    )


def _canonical_execute_steps(
    *, invalidation: InvalidationPlan, contract: CheckpointGraphContract
) -> set[str]:
    execute = set(invalidation.affected_steps)
    execute.update(step.key for step in contract.steps if step.reuse_policy == "never")
    producer = {output: step.key for step in contract.steps for output in step.produces_outputs}
    changed = True
    while changed:
        changed = False
        for step in contract.steps:
            if step.key in execute:
                continue
            if any(producer.get(output) in execute for output in step.consumes_outputs):
                execute.add(step.key)
                changed = True
    return execute


def _persisted_plan_hash(revision: RunRevision) -> str:
    return revision_plan_hash(
        {
            "mode": revision.mode,
            "dependency_graph_version": revision.dependency_graph_version,
            "earliest_affected_step": revision.earliest_affected_step,
            "changed_constraints": revision.changed_constraints,
            "direct_affected_steps": revision.direct_affected_steps,
            "affected_steps": revision.affected_steps,
            "reused_steps": revision.reused_steps,
            "fallback_reason": revision.fallback_reason,
            "manual_reconciliation_reason": revision.manual_reconciliation_reason,
        }
    )


async def _validate_lineage(
    session: AsyncSession,
    *,
    source_scope: RuntimeScope,
    revision_scope: RuntimeScope,
) -> None:
    if (
        source_scope.org_id != revision_scope.org_id
        or source_scope.user_id != revision_scope.user_id
        or source_scope.account_id != revision_scope.account_id
        or source_scope.thread_id != revision_scope.thread_id
        or source_scope.task_id != revision_scope.task_id
        or source_scope.run_id == revision_scope.run_id
        or source_scope.turn_id == revision_scope.turn_id
    ):
        raise RevisionScopeConflict("Revision and source scopes do not share exact lineage")
    source_run = await session.get(AgentRun, source_scope.run_id)
    revision_run = await session.get(AgentRun, revision_scope.run_id)
    source_turn = await session.get(ConversationTurn, source_scope.turn_id)
    revision_turn = await session.get(ConversationTurn, revision_scope.turn_id)
    thread = await session.get(ConversationThread, source_scope.thread_id)
    task = await session.get(BrainTask, source_scope.task_id)
    if (
        source_run is None
        or revision_run is None
        or source_turn is None
        or revision_turn is None
        or thread is None
        or task is None
        or source_run.org_id != source_scope.org_id
        or source_run.requested_by_id != source_scope.user_id
        or source_run.thread_id != source_scope.thread_id
        or source_run.turn_id != source_scope.turn_id
        or source_run.task_id != source_scope.task_id
        or revision_run.org_id != revision_scope.org_id
        or revision_run.requested_by_id != revision_scope.user_id
        or revision_run.thread_id != revision_scope.thread_id
        or revision_run.turn_id != revision_scope.turn_id
        or revision_run.task_id != revision_scope.task_id
        or source_turn.thread_id != source_scope.thread_id
        or revision_turn.thread_id != revision_scope.thread_id
        or revision_turn.target_turn_id != source_scope.turn_id
        or thread.org_id != source_scope.org_id
        or thread.account_id != source_scope.account_id
        or task.org_id != source_scope.org_id
    ):
        raise RevisionScopeConflict("Persisted revision lineage does not match runtime scope")
    for scope in (source_scope, revision_scope):
        if scope.skill_run_id is None:
            continue
        skill_run = await session.get(SkillRun, scope.skill_run_id)
        if (
            skill_run is None
            or skill_run.org_id != scope.org_id
            or skill_run.thread_id != scope.thread_id
            or skill_run.turn_id != scope.turn_id
            or skill_run.run_id != scope.run_id
            or skill_run.task_id != scope.task_id
        ):
            raise RevisionScopeConflict("Persisted SkillRun lineage does not match")


async def create_revision_record(
    session: AsyncSession,
    *,
    source_scope: RuntimeScope,
    revision_scope: RuntimeScope,
    invalidation: InvalidationPlan,
    resolution: RevisionResolution | None = None,
) -> RunRevision | NoRevisionRequired:
    if not isinstance(source_scope, RuntimeScope) or not isinstance(revision_scope, RuntimeScope):
        raise TypeError("source_scope and revision_scope must be RuntimeScope values")
    if not isinstance(invalidation, InvalidationPlan):
        raise TypeError("invalidation must come from the dependency planner")
    if not invalidation.changed_constraints:
        return NoRevisionRequired()
    if resolution is not None and not isinstance(resolution, RevisionResolution):
        raise TypeError("resolution must be a validated RevisionResolution DTO")
    if resolution is not None:
        raise RevisionStateConflict("Revision resolution is server-owned")
    canonical_invalidation = build_invalidation_plan(
        invalidation.skill_code, set(invalidation.changed_constraints)
    )
    if invalidation != canonical_invalidation:
        raise RevisionStateConflict("Invalidation does not match the dependency planner")
    await _validate_lineage(session, source_scope=source_scope, revision_scope=revision_scope)
    existing = await session.scalar(
        select(RunRevision)
        .where(RunRevision.revision_run_id == revision_scope.run_id)
        .with_for_update()
    )
    contract = require_checkpoint_graph_contract(invalidation.skill_code, 1)
    order = tuple(step.key for step in contract.steps)
    mode: ResolutionMode = invalidation.mode
    reason = invalidation.fallback_reason
    execute_steps = (
        order
        if mode == "full_recompute"
        else tuple(
            step.key
            for step in contract.steps
            if step.key in _canonical_execute_steps(invalidation=invalidation, contract=contract)
        )
    )
    resolution = _resolution(
        mode=mode,
        reason=reason,
        execute_steps=execute_steps,
        reused_steps=(),
    )
    _validate_reason(resolution.mode, resolution.reason)
    if (
        invalidation.graph_version != contract.graph_version
        or len(set(resolution.execute_steps)) != len(resolution.execute_steps)
        or len(set(resolution.reused_steps)) != len(resolution.reused_steps)
        or set(resolution.execute_steps).intersection(resolution.reused_steps)
        or not set(
            resolution.execute_steps,
        )
        .union(resolution.reused_steps)
        .issubset(order)
    ):
        raise RevisionStateConflict("Revision resolution does not match graph contract")
    earliest = (
        resolution.execute_steps[0]
        if resolution.mode == "partial"
        else (resolution.execute_steps[0] if resolution.execute_steps else None)
    )
    changed_constraints = {
        key: {"operation": "changed"} for key in invalidation.changed_constraints
    }
    values = {
        "org_id": revision_scope.org_id,
        "account_id": revision_scope.account_id,
        "thread_id": revision_scope.thread_id,
        "task_id": revision_scope.task_id,
        "source_turn_id": source_scope.turn_id,
        "source_run_id": source_scope.run_id,
        "source_skill_run_id": source_scope.skill_run_id,
        "revision_turn_id": revision_scope.turn_id,
        "revision_run_id": revision_scope.run_id,
        "revision_skill_run_id": revision_scope.skill_run_id,
        "mode": resolution.mode,
        "status": "planned",
        "dependency_graph_version": invalidation.graph_version,
        "earliest_affected_step": earliest,
        "changed_constraints": changed_constraints,
        "direct_affected_steps": list(invalidation.direct_steps),
        "affected_steps": list(resolution.execute_steps),
        "reused_steps": list(resolution.reused_steps),
        "fallback_reason": resolution.reason if resolution.mode == "full_recompute" else None,
        "manual_reconciliation_reason": resolution.reason
        if resolution.mode == "manual_reconciliation"
        else None,
    }
    if existing is not None:
        if all(getattr(existing, key) == value for key, value in values.items()):
            return existing
        raise RevisionStateConflict("Revision run already has a different durable plan")
    revision = RunRevision(**values, plan_hash="0" * 64)
    revision.plan_hash = _persisted_plan_hash(revision)
    session.add(revision)
    await session.flush()
    return revision


async def _locked_revision(session: AsyncSession, revision_id: int) -> RunRevision:
    revision = await session.scalar(
        select(RunRevision).where(RunRevision.id == revision_id).with_for_update()
    )
    if revision is None:
        raise RevisionStateConflict("Revision does not exist")
    return revision


async def mark_revision_running(session: AsyncSession, *, revision_id: int) -> RunRevision:
    revision = await _locked_revision(session, revision_id)
    if revision.status == "running":
        return revision
    if revision.status not in {"planned", "waiting_predecessor"}:
        raise RevisionStateConflict("Revision cannot transition to running")
    revision.status = "running"
    revision.started_at = await load_transaction_db_now(session)
    revision.finished_at = None
    await session.flush()
    return revision


async def fall_back_to_full_recompute(
    session: AsyncSession, *, revision_id: int, reason: str
) -> RunRevision:
    _validate_reason("full_recompute", reason)
    revision = await _locked_revision(session, revision_id)
    if revision.mode == "full_recompute" and revision.fallback_reason == reason:
        return revision
    if (
        revision.status not in {"planned", "waiting_predecessor"}
        or revision.mode == "manual_reconciliation"
    ):
        raise RevisionStateConflict("Revision cannot fall back from its current state")
    contract = require_checkpoint_graph_contract("operation_iteration", 1)
    order = [step.key for step in contract.steps]
    revision.mode = "full_recompute"
    revision.earliest_affected_step = order[0] if order else None
    revision.affected_steps = order
    revision.reused_steps = []
    revision.fallback_reason = reason
    revision.manual_reconciliation_reason = None
    revision.plan_hash = _persisted_plan_hash(revision)
    await session.flush()
    return revision


async def require_manual_reconciliation(
    session: AsyncSession, *, revision_id: int, reason: str
) -> RunRevision:
    _validate_reason("manual_reconciliation", reason)
    revision = await _locked_revision(session, revision_id)
    if revision.mode == "manual_reconciliation" and revision.manual_reconciliation_reason == reason:
        return revision
    if revision.status in {"completed", "failed", "cancelled"}:
        raise RevisionStateConflict("Terminal revision cannot be replaced by manual reconciliation")
    revision.mode = "manual_reconciliation"
    revision.earliest_affected_step = None
    revision.affected_steps = []
    revision.reused_steps = []
    revision.fallback_reason = None
    revision.manual_reconciliation_reason = reason
    revision.fork_checkpoint_id = None
    revision.plan_hash = _persisted_plan_hash(revision)
    await session.flush()
    return revision


async def complete_revision(session: AsyncSession, *, revision_id: int) -> RunRevision:
    revision = await _locked_revision(session, revision_id)
    if revision.status == "completed":
        return revision
    if revision.status != "running":
        raise RevisionStateConflict("Only a running revision can complete")
    revision.status = "completed"
    revision.finished_at = await load_transaction_db_now(session)
    await session.flush()
    return revision


__all__ = [
    "CheckpointCandidateVerdict",
    "RevisionResolution",
    "RevisionScopeConflict",
    "RevisionStateConflict",
    "complete_revision",
    "create_revision_record",
    "fall_back_to_full_recompute",
    "mark_revision_running",
    "require_manual_reconciliation",
    "resolve_revision_policy",
]
