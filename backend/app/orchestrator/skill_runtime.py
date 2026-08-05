"""Bounded, durable execution runtime for business-facing Skills."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.core.approval_audit import add_approval_requested
from app.core.runtime_failures import FailureDisposition, classify_runtime_failure
from app.models import (
    AgentInvocation,
    AgentQualityScore,
    AgentRun,
    AgentToolCall,
    BrainTask,
    ContentItem,
    ConversationThread,
    ConversationTurn,
    Deliverable,
    SkillRun,
    TaskBrief,
    ToolExecutionAttempt,
    User,
)
from app.models.enums import (
    AgentCode,
    AgentInvocationStatus,
    BrainTaskStatus,
    BrainTaskType,
    ContentStage,
    ContentStatus,
    DeliverableStatus,
    DeliverableType,
)
from app.orchestrator.agent_harness import AgentHarnessError, agent_harness
from app.orchestrator.ai_coo_critic import (
    CriticDisposition,
    ai_coo_critic_service,
)
from app.orchestrator.brain_intelligence import IntelligenceUnavailable, brain_intelligence
from app.orchestrator.checkpoint_graph_contracts import CheckpointGraphContract
from app.orchestrator.composite_skill_runtime import composite_skill_runtime
from app.orchestrator.operation_lineage import (
    OperationLineageRef,
    resolve_internal_lineage_artifacts,
    resolve_operation_revision_bridge,
)
from app.orchestrator.operation_quality import (
    ArtifactQuality,
    evaluate_calendar_quality,
    evaluate_script_quality,
    evaluate_topic_quality,
    evaluate_visual_quality,
)
from app.orchestrator.runtime_scope import RuntimeScope
from app.orchestrator.runtime_tools import build_runtime_tool_adapter
from app.orchestrator.skill_tool_plan import SkillToolPlanError, build_skill_tool_plan
from app.orchestrator.skills.account_data_analysis import (
    AccountDataAnalysisAnswer,
    AccountDataAnalysisCriticOutcome,
)
from app.orchestrator.skills.account_inspection import (
    AccountInspectionCriticOutcome,
    AccountInspectionMetric,
    AccountInspectionReport,
)
from app.orchestrator.skills.account_positioning import AccountPositioningReport
from app.orchestrator.skills.content_calendar_planning import (
    CalendarSlot,
    ContentCalendarPlanningReport,
)
from app.orchestrator.skills.content_publishing import ContentPublishingReceipt
from app.orchestrator.skills.engagement_review import EngagementReviewReport
from app.orchestrator.skills.operating_tasks import (
    FilmingScript,
    OperationArtifactRef,
    OperationQualityBundle,
    PerformanceReviewReport,
    PublishingPreparationReport,
    ScriptGenerationReport,
    TopicPlanItem,
    TopicPlanningReport,
    WeeklyOperationPackage,
)
from app.orchestrator.skills.operation_iteration import OperationIterationPlan
from app.orchestrator.skills.registry import skill_registry
from app.orchestrator.skills.visual_brief_generation import (
    VisualBriefGenerationReport,
    VisualProductionItem,
)
from app.orchestrator.tool_executor import DurableToolExecutor
from app.schemas.brain import RuntimeToolCall
from app.schemas.capability_request import CapabilityRequest
from app.schemas.skills import SkillDefinition
from app.services.account_metric_analysis import AccountMetricAnalysis
from app.services.agent_runs import acquire_agent_run, heartbeat_agent_run, utc_now
from app.services.composite_skill_runs import pause_composite_parent_for_artifacts
from app.services.runtime_deliverables import write_runtime_deliverable
from app.services.runtime_locking import (
    RuntimeRootLock,
    discover_runtime_skill_lock_ids,
    extend_runtime_root_lock,
    lock_runtime_root_scope,
    require_runtime_root_lock,
)
from app.services.runtime_state import TERMINAL_STATUSES, RuntimeStateScope, close_runtime_state
from app.services.turn_events import TurnEventScope, append_turn_event

_ACCOUNT_INSPECTION = "account_inspection"
_ACCOUNT_DATA_ANALYSIS = "account_data_analysis"
_MAX_CRITIC_IMPROVEMENTS = 2
DataSufficiency = Literal["insufficient", "partial", "sufficient"]
log = logging.getLogger("dyflow.skill_runtime")
_ACTIVE_SKILL_STAGES: ContextVar[tuple[tuple[str, int], ...]] = ContextVar(
    "active_skill_stages",
    default=(),
)
_NESTED_PARENT_SKILL_RUN_ID: ContextVar[int | None] = ContextVar(
    "nested_parent_skill_run_id",
    default=None,
)
_OPERATION_ITERATION_NATIVE_BOUNDARIES = frozenset({"prepare_deliverable"})


@dataclass(frozen=True)
class RevisionExecutorBoundaryMap:
    native_boundaries: frozenset[str]
    logical_boundaries: dict[str, str | None]

    @property
    def requires_full_recompute(self) -> bool:
        return any(boundary is None for boundary in self.logical_boundaries.values())


@dataclass(frozen=True)
class _ServerSkillContext:
    """Internal-only child context; never populated from structured client input."""

    preloaded_tool_results: dict[str, dict[str, Any]]
    tool_audit_refs: dict[str, dict[str, int]]
    lineage_refs: tuple[OperationLineageRef, ...] = ()
    revision_id: int | None = None
    revision_parent_skill_run_id: int | None = None


def resolve_revision_executor_boundaries(
    contract: CheckpointGraphContract,
) -> RevisionExecutorBoundaryMap:
    """Resolve declared logical owners only against real runtime boundaries."""

    logical: dict[str, str | None] = {}
    for step in contract.steps:
        owner, separator, boundary = step.executor_boundary_key.partition(":")
        resolved: str | None = None
        if separator and owner == "child_skill" and step.executor_owner == "child_skill":
            try:
                skill_registry.get(boundary)
            except KeyError:
                pass
            else:
                resolved = step.executor_boundary_key
        elif (
            separator
            and owner == "native_runtime"
            and step.executor_owner == "native_runtime"
            and boundary in _OPERATION_ITERATION_NATIVE_BOUNDARIES
        ):
            resolved = step.executor_boundary_key
        logical[step.key] = resolved
    return RevisionExecutorBoundaryMap(
        native_boundaries=_OPERATION_ITERATION_NATIVE_BOUNDARIES,
        logical_boundaries=logical,
    )


class _SkillStageFailure(Exception):
    def __init__(self, step_code: str, attempt: int, cause: Exception) -> None:
        super().__init__(str(cause))
        self.step_code = step_code
        self.attempt = attempt
        self.cause = cause


async def _append_skill_step_event(
    session: AsyncSession,
    *,
    scope: RuntimeScope,
    step_code: str,
    attempt: int,
    state: str,
    error_code: str | None = None,
) -> None:
    payload: dict[str, object] = {
        "step": step_code,
        "step_id": f"{step_code}:attempt:{attempt}",
        "status": state,
        "metadata": {"attempt": attempt},
    }
    if error_code is not None:
        payload["error_code"] = error_code
    await append_turn_event(
        session,
        TurnEventScope(
            org_id=scope.org_id,
            account_id=scope.account_id,
            thread_id=scope.thread_id,
            turn_id=scope.turn_id,
            run_id=scope.run_id,
            skill_run_id=scope.skill_run_id,
        ),
        f"step.{state}",
        payload,
        f"step:{step_code}:attempt:{attempt}:{state}",
    )


async def _start_skill_stage(
    session: AsyncSession,
    *,
    scope: RuntimeScope,
    step_code: str,
    attempt: int,
) -> None:
    await _append_skill_step_event(
        session,
        scope=scope,
        step_code=step_code,
        attempt=attempt,
        state="started",
    )
    await session.commit()
    _ACTIVE_SKILL_STAGES.set((*_ACTIVE_SKILL_STAGES.get(), (step_code, attempt)))


async def _complete_skill_stage(
    session: AsyncSession,
    *,
    scope: RuntimeScope,
    step_code: str,
    attempt: int,
    commit: bool,
) -> None:
    await _append_skill_step_event(
        session,
        scope=scope,
        step_code=step_code,
        attempt=attempt,
        state="completed",
    )
    if not commit:
        return
    await session.commit()
    _release_skill_stage(step_code=step_code, attempt=attempt)


def _release_skill_stage(*, step_code: str, attempt: int) -> None:
    stages = _ACTIVE_SKILL_STAGES.get()
    if stages and stages[-1] == (step_code, attempt):
        _ACTIVE_SKILL_STAGES.set(stages[:-1])


async def _fail_skill_stage(
    session: AsyncSession,
    *,
    scope: RuntimeScope,
    step_code: str,
    attempt: int,
    error_code: str,
) -> None:
    await _append_skill_step_event(
        session,
        scope=scope,
        step_code=step_code,
        attempt=attempt,
        state="failed",
        error_code=error_code,
    )
    stages = _ACTIVE_SKILL_STAGES.get()
    if stages and stages[-1] == (step_code, attempt):
        _ACTIVE_SKILL_STAGES.set(stages[:-1])


async def run_bounded_stage(
    operations: list[Any],
    *,
    limit: int = 3,
) -> list[Any]:
    """Run one expert stage concurrently without sharing execution state."""

    semaphore = asyncio.Semaphore(max(1, min(3, limit)))

    async def run(operation: Any) -> Any:
        async with semaphore:
            return await operation()

    results = await asyncio.gather(
        *(run(operation) for operation in operations),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return results


def skill_input_hash(snapshot: dict[str, Any]) -> str:
    """Return the stable SHA-256 identity of one fully frozen Skill input."""

    normalized = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _validated_skill_input(
    *,
    definition: SkillDefinition,
    structured_input: dict[str, Any],
    fallback_days: int | None,
) -> Any:
    allowed_fields = definition.input_model.model_fields
    input_payload = {key: value for key, value in structured_input.items() if key in allowed_fields}
    if fallback_days is not None and "days" in allowed_fields and "days" not in input_payload:
        input_payload["days"] = fallback_days
    return definition.input_model.model_validate(input_payload)


def _capability_attachment_snapshot(
    capability_request: CapabilityRequest,
) -> dict[str, Any]:
    contexts = [item.model_dump(mode="json") for item in capability_request.attachment_contexts]
    if not contexts:
        return {}
    return {
        "attachment_ids": list(capability_request.attachment_ids),
        "attachment_contexts": contexts,
    }


def _capability_constraint_snapshot(
    capability_request: CapabilityRequest,
) -> dict[str, Any]:
    constraints = [item.strip() for item in capability_request.constraints if item.strip()]
    if not constraints:
        return {}
    return {"_server_request_constraints": constraints}


def _server_skill_context_snapshot(
    context: _ServerSkillContext | None,
) -> dict[str, Any]:
    if context is None:
        return {}
    server_context: dict[str, Any] = {
        "preloaded_tool_results": context.preloaded_tool_results,
        "tool_audit_refs": context.tool_audit_refs,
    }
    if context.lineage_refs:
        server_context["lineage_refs"] = [asdict(ref) for ref in context.lineage_refs]
    if context.revision_id is not None:
        server_context["revision_id"] = context.revision_id
        server_context["revision_parent_skill_run_id"] = context.revision_parent_skill_run_id
    return {"_server_context": server_context}


def _server_skill_context_from_snapshot(value: object) -> _ServerSkillContext | None:
    if not isinstance(value, dict):
        return None
    preloaded = value.get("preloaded_tool_results")
    audit_refs = value.get("tool_audit_refs")
    raw_lineage_refs = value.get("lineage_refs", [])
    if not isinstance(preloaded, dict) or not isinstance(audit_refs, dict):
        return None
    if not isinstance(raw_lineage_refs, list) or any(
        not isinstance(item, dict) for item in raw_lineage_refs
    ):
        return None
    try:
        lineage_refs = tuple(OperationLineageRef(**item) for item in raw_lineage_refs)
    except (TypeError, ValueError):
        return None
    revision_id = value.get("revision_id")
    revision_parent_skill_run_id = value.get("revision_parent_skill_run_id")
    if (revision_id is None) != (revision_parent_skill_run_id is None) or any(
        type(item) is not int or item <= 0
        for item in (revision_id, revision_parent_skill_run_id)
        if item is not None
    ):
        return None
    return _ServerSkillContext(
        preloaded_tool_results={
            str(code): dict(result)
            for code, result in preloaded.items()
            if isinstance(result, dict)
        },
        tool_audit_refs={
            str(code): {
                str(key): int(identifier)
                for key, identifier in ref.items()
                if isinstance(identifier, int)
            }
            for code, ref in audit_refs.items()
            if isinstance(ref, dict)
        },
        lineage_refs=lineage_refs,
        revision_id=revision_id,
        revision_parent_skill_run_id=revision_parent_skill_run_id,
    )


@dataclass(frozen=True)
class SkillExecutionResult:
    status: str
    skill_run_id: int
    task_id: int | None
    artifact_id: int | None
    artifact_type: str
    report: dict[str, Any]
    response: str
    error_code: str | None = None


@dataclass(frozen=True)
class _CriticResult:
    passed: bool
    score: int
    issues: list[str]
    suggestions: list[str]


@dataclass(frozen=True)
class _ExpertResult:
    invocation: AgentInvocation
    output: dict[str, Any]


class _ToolScopeMismatch(PermissionError):
    pass


class _SkillLeaseLost(RuntimeError):
    pass


class SkillRecoveryConflict(RuntimeError):
    """A persisted Skill execution cannot be safely resumed."""


def resolve_frozen_skill_definition(
    skill_run: SkillRun,
    *,
    registry: Any = skill_registry,
) -> SkillDefinition:
    if skill_input_hash(dict(skill_run.input_snapshot or {})) != skill_run.input_hash:
        raise SkillRecoveryConflict("SKILL_INPUT_INTEGRITY_MISMATCH")
    try:
        return registry.get(skill_run.skill_code, version=skill_run.skill_version)
    except KeyError as exc:
        raise SkillRecoveryConflict("SKILL_VERSION_UNAVAILABLE") from exc


class SkillRuntime:
    """Execute one frozen Skill graph without entering the strategy runtime."""

    def __init__(
        self,
        *,
        tool_executor: Any | None = None,
        harness: Any | None = None,
        critic: Any | None = None,
    ) -> None:
        self._tool_executor = tool_executor
        self._harness = harness or agent_harness
        self._critic = critic

    async def execute(
        self,
        session: AsyncSession,
        *,
        user: User,
        thread: ConversationThread,
        turn: ConversationTurn,
        run: AgentRun,
        skill_code: str,
        capability_request: CapabilityRequest | None = None,
        days: int = 30,
        lease_owner: str | None = None,
        resume_skill_run: SkillRun | None = None,
        parent_skill_run_id: int | None = None,
        server_context: _ServerSkillContext | None = None,
    ) -> SkillExecutionResult:
        self._require_scope(user, thread, turn, run)
        if capability_request is not None:
            self._require_capability_request_scope(
                user=user,
                thread=thread,
                turn=turn,
                run=run,
                capability_request=capability_request,
            )
        run_id = run.id
        persisted_candidates = (
            []
            if resume_skill_run is not None
            else list(
                await session.scalars(
                    select(SkillRun).where(
                        SkillRun.run_id == run_id,
                        SkillRun.skill_code == skill_code,
                    )
                )
            )
        )
        if len(persisted_candidates) > 1:
            raise SkillRecoveryConflict("SKILL_RECOVERY_AMBIGUOUS")
        recovery_candidate = resume_skill_run or (
            persisted_candidates[0] if persisted_candidates else None
        )
        definition = (
            resolve_frozen_skill_definition(recovery_candidate)
            if recovery_candidate is not None
            else skill_registry.get(skill_code)
        )
        if recovery_candidate is not None:
            frozen_snapshot = dict(recovery_candidate.input_snapshot or {})
            model_input = {
                key: value
                for key, value in frozen_snapshot.items()
                if key in definition.input_model.model_fields
            }
            frozen_input = definition.input_model.model_validate(model_input)
            if capability_request is not None:
                requested_input = _validated_skill_input(
                    definition=definition,
                    structured_input=capability_request.structured_input,
                    fallback_days=None,
                )
                requested_snapshot = {
                    "account_id": thread.account_id,
                    **requested_input.model_dump(mode="json"),
                    **_capability_attachment_snapshot(capability_request),
                    **_capability_constraint_snapshot(capability_request),
                    **_server_skill_context_snapshot(server_context),
                }
                if requested_snapshot != frozen_snapshot:
                    raise SkillRecoveryConflict("SKILL_RECOVERY_INPUT_CONFLICT")
        else:
            frozen_input = _validated_skill_input(
                definition=definition,
                structured_input=(
                    capability_request.structured_input if capability_request is not None else {}
                ),
                fallback_days=(None if capability_request is not None else days),
            )
            frozen_snapshot = {
                "account_id": thread.account_id,
                **frozen_input.model_dump(mode="json"),
                **(
                    _capability_attachment_snapshot(capability_request)
                    if capability_request is not None
                    else {}
                ),
                **(
                    _capability_constraint_snapshot(capability_request)
                    if capability_request is not None
                    else {}
                ),
                **_server_skill_context_snapshot(server_context),
            }
        idempotency_key = f"skill:{definition.code}"
        lease_owner = lease_owner or f"skill-run:{run_id}:{uuid4().hex}"
        existing = recovery_candidate
        if existing is None:
            existing = await session.scalar(
                select(SkillRun).where(
                    SkillRun.run_id == run_id,
                    SkillRun.idempotency_key == idempotency_key,
                )
            )
        else:
            if (
                existing.org_id != user.org_id
                or existing.run_id != run_id
                or existing.thread_id != thread.id
                or existing.turn_id != turn.id
                or existing.skill_code != definition.code
            ):
                raise SkillRecoveryConflict("SKILL_RECOVERY_SCOPE_CONFLICT")
            if (
                existing.skill_version != definition.version
                or existing.input_hash != skill_input_hash(frozen_snapshot)
                or dict(existing.input_snapshot or {}) != frozen_snapshot
            ):
                raise SkillRecoveryConflict("SKILL_RECOVERY_WINNER_CONFLICT")
        if existing is not None and existing.status in {
            "blocked",
            "completed",
            "failed",
            "stopped",
            "waiting_permission",
            "waiting_user",
            "needs_review",
        }:
            return self._existing_result(existing)
        recovering = False
        if existing is not None and existing.status == "running":
            recovering = True
            claimed = (
                run
                if run.status == "running" and run.lease_owner == lease_owner
                else await acquire_agent_run(
                    session,
                    run_id,
                    worker_id=lease_owner,
                    lease_seconds=settings.agent_run_lease_seconds,
                )
            )
            if claimed is None:
                await session.refresh(existing)
                return self._existing_result(existing)
            run = claimed

        creation_lock: RuntimeRootLock | None = None
        if existing is None:
            with session.no_autoflush:
                discovered_task = await session.get(BrainTask, run.task_id) if run.task_id else None
                discovered_skills = list(
                    await session.scalars(
                        select(SkillRun).where(SkillRun.run_id == run_id).order_by(SkillRun.id)
                    )
                )
                root_candidates = [
                    item
                    for item in discovered_skills
                    if type(dict(item.output_snapshot or {}).get("composite_parent_skill_run_id"))
                    is not int
                ]
                root_candidate = next(
                    (item for item in root_candidates if item.skill_code == "operation_iteration"),
                    root_candidates[0] if root_candidates else None,
                )
                root_id = root_candidate.id if root_candidate is not None else None
                child_ids = tuple(item.id for item in discovered_skills if item.id != root_id)
                creation_lock = await lock_runtime_root_scope(
                    session,
                    run_id=run_id,
                    expected_turn_id=turn.id,
                    expected_task_id=run.task_id,
                    expected_content_item_id=(
                        discovered_task.content_item_id if discovered_task is not None else None
                    ),
                    root_skill_run_id=root_id,
                    child_skill_run_ids=child_ids,
                    validate_child_parent=False,
                )
                contender = await session.scalar(
                    select(SkillRun).where(
                        SkillRun.run_id == run_id,
                        SkillRun.idempotency_key == idempotency_key,
                    )
                )
            if contender is not None:
                return self._existing_result(contender)
            locked_run = await session.scalar(
                select(AgentRun)
                .where(AgentRun.id == run_id)
                .execution_options(populate_existing=True)
            )
            if locked_run is None or locked_run.status in TERMINAL_STATUSES:
                raise SkillRecoveryConflict("SKILL_CREATE_RUN_TERMINAL")
            run = locked_run

        task, content = await self._compatibility_task(
            session,
            user=user,
            thread=thread,
            turn=turn,
            run=run,
            skill_code=definition.code,
            artifact_type=definition.artifact_type or definition.code,
        )
        skill_run = existing
        if skill_run is None:
            skill_run = SkillRun(
                org_id=user.org_id,
                thread_id=thread.id,
                turn_id=turn.id,
                run_id=run_id,
                task_id=task.id,
                idempotency_key=idempotency_key,
                skill_code=definition.code,
                skill_version=definition.version,
                status="running",
                input_snapshot={
                    **frozen_snapshot,
                },
                input_hash=skill_input_hash(frozen_snapshot),
                output_snapshot=(
                    {"composite_parent_skill_run_id": parent_skill_run_id}
                    if parent_skill_run_id is not None
                    else {}
                ),
            )
            session.add(skill_run)
            now = utc_now()
            run.attempt += 1
            run.lease_owner = lease_owner
            run.leased_until = now + timedelta(seconds=max(1, settings.agent_run_lease_seconds))
            run.heartbeat_at = now
            run.started_at = run.started_at or now
            run.next_retry_at = None
            try:
                if creation_lock is None:
                    raise SkillRecoveryConflict("SKILL_CREATE_ROOT_LOCK_MISSING")
                creation_lock = await extend_runtime_root_lock(
                    session,
                    creation_lock,
                    task=task,
                    content=content,
                    skill_run=skill_run,
                    expected_content_account_id=thread.account_id,
                )
                await close_runtime_state(
                    session,
                    scope=RuntimeStateScope(
                        run_id=run.id,
                        org_id=user.org_id,
                        thread_id=thread.id,
                        turn_id=turn.id,
                        skill_run_id=skill_run.id,
                        task_id=task.id,
                        account_id=thread.account_id,
                        project_id=thread.project_id,
                        content_item_id=task.content_item_id,
                        skill_output_snapshot={},
                        nested_skill=parent_skill_run_id is not None,
                    ),
                    status="running",
                    prelocked=creation_lock,
                    message=f"{definition.name}正在执行。",
                )
                await session.refresh(skill_run)
            except IntegrityError as exc:
                await session.rollback()
                skill_run = await session.scalar(
                    select(SkillRun).where(
                        SkillRun.run_id == run_id,
                        SkillRun.idempotency_key == idempotency_key,
                    )
                )
                if skill_run is None:
                    raise
                if (
                    skill_run.skill_code != definition.code
                    or skill_run.skill_version != definition.version
                    or skill_run.input_hash != skill_input_hash(frozen_snapshot)
                    or dict(skill_run.input_snapshot or {}) != frozen_snapshot
                ):
                    raise SkillRecoveryConflict("SKILL_RECOVERY_WINNER_CONFLICT") from exc
                return self._existing_result(skill_run)
        elif skill_run.task_id != task.id:
            raise PermissionError("SkillRun task ownership does not match")

        runtime_scope = await RuntimeScope.from_conversation(
            session,
            user=user,
            thread=thread,
            turn=turn,
            run=run,
        )
        runtime_scope = await runtime_scope.bind_task(session, task)
        runtime_scope = await runtime_scope.bind_skill(session, skill_run)
        skill_run_id = skill_run.id
        task_id = task.id
        thread_id = thread.id
        turn_id = turn.id
        if recovering and await self._interrupt_ambiguous_side_effects(
            session,
            run=run,
            turn=turn,
            skill_run=skill_run,
            task=task,
        ):
            return self._existing_result(skill_run)
        stage_context_token = _ACTIVE_SKILL_STAGES.set(())
        nested_parent_token = _NESTED_PARENT_SKILL_RUN_ID.set(parent_skill_run_id)
        try:
            if definition.code == _ACCOUNT_DATA_ANALYSIS:
                return await self._execute_account_data_analysis(
                    session,
                    user=user,
                    thread=thread,
                    turn=turn,
                    run=run,
                    task=task,
                    content=content,
                    skill_run=skill_run,
                    scope=runtime_scope,
                    definition=definition,
                    frozen_input=dict(skill_run.input_snapshot or {}),
                    lease_owner=lease_owner,
                )
            if definition.code == _ACCOUNT_INSPECTION:
                return await self._execute_account_inspection(
                    session,
                    user=user,
                    thread=thread,
                    turn=turn,
                    run=run,
                    task=task,
                    content=content,
                    skill_run=skill_run,
                    scope=runtime_scope,
                    definition=definition,
                    days=frozen_input.days,
                    attachment_contexts=list(
                        (skill_run.input_snapshot or {}).get("attachment_contexts") or []
                    ),
                    lease_owner=lease_owner,
                )
            if definition.code == "content_publishing":
                return await self._execute_content_publishing(
                    session,
                    user=user,
                    thread=thread,
                    turn=turn,
                    run=run,
                    task=task,
                    skill_run=skill_run,
                    scope=runtime_scope,
                    definition=definition,
                    frozen_input=dict(skill_run.input_snapshot or {}),
                    lease_owner=lease_owner,
                )
            if definition.code == "operation_iteration":
                return await self._execute_operation_iteration(
                    session,
                    user=user,
                    thread=thread,
                    turn=turn,
                    run=run,
                    task=task,
                    content=content,
                    skill_run=skill_run,
                    scope=runtime_scope,
                    frozen_input=dict(skill_run.input_snapshot or {}),
                    lease_owner=lease_owner,
                )
            return await self._execute_operating_skill(
                session,
                user=user,
                thread=thread,
                turn=turn,
                run=run,
                task=task,
                content=content,
                skill_run=skill_run,
                scope=runtime_scope,
                definition=definition,
                frozen_input=dict(skill_run.input_snapshot or {}),
                lease_owner=lease_owner,
            )

        except _SkillLeaseLost:
            await session.rollback()
            persisted = await session.get(SkillRun, skill_run_id)
            if persisted is None:
                raise
            return self._existing_result(persisted)
        except Exception as exc:
            failure = exc.cause if isinstance(exc, _SkillStageFailure) else exc
            retryable = classify_runtime_failure(failure) is FailureDisposition.RETRYABLE
            active_stages = _ACTIVE_SKILL_STAGES.get()
            failed_stage = (
                (exc.step_code, exc.attempt)
                if isinstance(exc, _SkillStageFailure)
                else active_stages[-1]
                if active_stages
                else None
            )
            await session.rollback()
            if failed_stage is not None:
                await _fail_skill_stage(
                    session,
                    scope=runtime_scope,
                    step_code=failed_stage[0],
                    attempt=failed_stage[1],
                    error_code=type(failure).__name__,
                )
            if retryable:
                await session.commit()
                if failure is exc:
                    raise
                raise failure from exc
            log.exception(
                "Skill execution failed",
                extra={
                    "skill_code": definition.code,
                    "skill_run_id": skill_run_id,
                    "task_id": task_id,
                    "run_id": run_id,
                },
            )
            persisted = await session.get(SkillRun, skill_run_id)
            persisted_task = await session.get(BrainTask, task_id)
            persisted_run = await session.get(AgentRun, run_id)
            persisted_turn = await session.get(ConversationTurn, turn_id)
            persisted_thread = await session.get(ConversationThread, thread_id)
            scope_mismatch = isinstance(failure, _ToolScopeMismatch)
            terminal_status = "blocked" if scope_mismatch else "failed"
            error_code = "TOOL_RESULT_SCOPE_MISMATCH" if scope_mismatch else type(failure).__name__
            response = (
                "工具返回的数据不属于当前账号，账号体检已停止。"
                if scope_mismatch
                else "账号体检执行失败，请稍后重试。"
            )
            if persisted is not None:
                output_snapshot = {
                    "status": terminal_status,
                    "task_id": task_id,
                    "artifact_id": None,
                    "artifact_type": definition.artifact_type or "account_inspection_report",
                    "report": {},
                    "error_code": error_code,
                    "response": response,
                }
                if (
                    persisted_task is None
                    or persisted_run is None
                    or persisted_turn is None
                    or persisted_thread is None
                ):
                    raise RuntimeError("Skill execution scope disappeared") from failure
                await self._close_skill_state(
                    session,
                    thread=persisted_thread,
                    turn=persisted_turn,
                    run=persisted_run,
                    task=persisted_task,
                    skill_run=persisted,
                    status=terminal_status,
                    response=response,
                    output_snapshot=output_snapshot,
                    error_code=error_code,
                )
            return SkillExecutionResult(
                status=terminal_status,
                skill_run_id=skill_run_id,
                task_id=task_id,
                artifact_id=None,
                artifact_type=definition.artifact_type or "account_inspection_report",
                report={},
                response=response,
                error_code=error_code,
            )
        finally:
            _NESTED_PARENT_SKILL_RUN_ID.reset(nested_parent_token)
            _ACTIVE_SKILL_STAGES.reset(stage_context_token)

    async def _execute_content_publishing(
        self,
        session: AsyncSession,
        *,
        user: User,
        thread: ConversationThread,
        turn: ConversationTurn,
        run: AgentRun,
        task: BrainTask,
        skill_run: SkillRun,
        scope: RuntimeScope,
        definition: SkillDefinition,
        frozen_input: dict[str, Any],
        lease_owner: str,
    ) -> SkillExecutionResult:
        """Execute the single approved side effect without an expert detour."""

        artifact_id = int(frozen_input["approved_publish_artifact_id"])
        sources = await _confirmed_source_artifacts(
            session,
            account_id=thread.account_id,
            artifact_ids=[artifact_id],
        )
        source = sources[0]
        attempt = max(1, run.attempt)
        await _start_skill_stage(
            session,
            scope=scope,
            step_code="publish_content",
            attempt=attempt,
        )
        await self._heartbeat(session, run=run, lease_owner=lease_owner)
        tool_executor = self._tool_executor or DurableToolExecutor(build_runtime_tool_adapter())
        outcome = await tool_executor.execute(
            task=task,
            user=user,
            request=RuntimeToolCall(
                tool_code="platform.content_publish",
                arguments={
                    "approved_publish_artifact_id": artifact_id,
                    "source_artifact_version": int(source["version"]),
                    "scheduled_at": frozen_input.get("scheduled_at"),
                    "visibility": str(frozen_input.get("visibility") or "public"),
                    "allow_comment": bool(frozen_input.get("allow_comment", True)),
                },
                purpose="执行已审批发布包并获取平台回执",
                idempotency_key=f"{skill_run.id}:platform.content_publish",
            ),
            project_id=thread.project_id,
            agent_code=AgentCode.DECISION.value,
            scope=scope,
            execution_owner=lease_owner,
        )
        await self._heartbeat(session, run=run, lease_owner=lease_owner)
        if outcome.status != "success" or outcome.result is None:
            await _fail_skill_stage(
                session,
                scope=scope,
                step_code="publish_content",
                attempt=attempt,
                error_code="TOOL_EXECUTION_FAILED",
            )
            return await self._pause_for_tool(
                session,
                thread=thread,
                turn=turn,
                run=run,
                skill_run=skill_run,
                task=task,
                status=outcome.status,
                skill_name=definition.name,
                artifact_type=definition.artifact_type or definition.code,
            )
        self._require_tool_scope(outcome.result, thread.account_id)
        receipt = ContentPublishingReceipt.model_validate(
            {"artifact_type": "platform_publish_receipt", **outcome.result}
        ).model_dump(mode="json")
        blocked = receipt["status"] == "blocked"
        response = (
            "当前账号尚未接通可用的平台发布通道，未执行发布。"
            if blocked
            else (
                "平台已确认发布成功。"
                if receipt["status"] == "published"
                else "发布包已进入官方平台通道，正在等待平台确认；当前不视为已发布。"
            )
        )
        output = {
            "status": "blocked" if blocked else "completed",
            "task_id": task.id,
            "artifact_id": None,
            "artifact_type": "platform_publish_receipt",
            "report": receipt,
            "response": response,
            "error_code": receipt.get("reason") if blocked else None,
        }
        await _complete_skill_stage(
            session,
            scope=scope,
            step_code="publish_content",
            attempt=attempt,
            commit=False,
        )
        await self._close_skill_state(
            session,
            thread=thread,
            turn=turn,
            run=run,
            task=task,
            skill_run=skill_run,
            status=output["status"],
            response=response,
            output_snapshot=output,
            error_code=output["error_code"],
        )
        return self._existing_result(skill_run)

    async def _execute_operation_iteration(
        self,
        session: AsyncSession,
        *,
        user: User,
        thread: ConversationThread,
        turn: ConversationTurn,
        run: AgentRun,
        task: BrainTask,
        content: ContentItem,
        skill_run: SkillRun,
        scope: RuntimeScope,
        frozen_input: dict[str, Any],
        lease_owner: str,
    ) -> SkillExecutionResult:
        """Persist a child-Skill DAG without impersonating its specialists."""

        review_value = frozen_input.get("confirmed_review_artifact_id")
        positioning_id = frozen_input.get("positioning_artifact_id")
        attempt = max(1, run.attempt)
        revision_bridge = await resolve_operation_revision_bridge(
            session,
            scope=scope,
            current_parent_skill_run_id=skill_run.id,
        )
        source_refs: list[dict[str, Any]]
        fresh_server_context = (
            _server_skill_context_from_snapshot(revision_bridge.source_server_context)
            if revision_bridge is not None
            else None
        )
        if revision_bridge is not None and fresh_server_context is None:
            raise SkillRecoveryConflict("OPERATION_REVISION_CONTEXT_INVALID")
        if revision_bridge is not None and fresh_server_context is not None:
            fresh_server_context = _ServerSkillContext(
                preloaded_tool_results=fresh_server_context.preloaded_tool_results,
                tool_audit_refs=fresh_server_context.tool_audit_refs,
                lineage_refs=fresh_server_context.lineage_refs,
                revision_id=revision_bridge.revision_id,
                revision_parent_skill_run_id=skill_run.id,
            )
        if review_value is not None:
            review_id = int(review_value)
            artifact_ids = [review_id]
            if positioning_id is not None:
                artifact_ids.append(int(positioning_id))
            sources = await _confirmed_source_artifacts(
                session,
                account_id=thread.account_id,
                artifact_ids=artifact_ids,
            )
            by_id = {int(item["artifact_id"]): item for item in sources}
            if by_id[review_id]["artifact_type"] != DeliverableType.REVIEW_REPORT.value:
                raise PermissionError("OPERATION_ITERATION_REVIEW_ARTIFACT_INVALID")
            if positioning_id is not None and (
                by_id[int(positioning_id)]["artifact_type"]
                != DeliverableType.POSITIONING_STRATEGY.value
            ):
                raise PermissionError("OPERATION_ITERATION_POSITIONING_ARTIFACT_INVALID")
            source_refs = [
                {
                    "artifact_id": item["artifact_id"],
                    "artifact_type": item["artifact_type"],
                    "version": item["version"],
                }
                for item in sources
            ]
        else:
            if revision_bridge is None:
                fresh_server_context = _server_skill_context_from_snapshot(
                    dict(skill_run.output_snapshot or {}).get("_server_context")
                )
            if fresh_server_context is None:
                await _start_skill_stage(
                    session,
                    scope=scope,
                    step_code="read_data",
                    attempt=attempt,
                )
                tool_executor = self._tool_executor or DurableToolExecutor(
                    build_runtime_tool_adapter()
                )
                outcome = await tool_executor.execute(
                    task=task,
                    user=user,
                    request=RuntimeToolCall(
                        tool_code="account.data_context",
                        arguments={"days": int(frozen_input.get("cycle_days") or 7)},
                        purpose="运营迭代：预检账号数据与对标证据",
                        idempotency_key=f"{skill_run.id}:account.data_context",
                    ),
                    project_id=thread.project_id,
                    agent_code=AgentCode.DECISION.value,
                    scope=scope,
                    execution_owner=lease_owner,
                )
                if outcome.status != "success" or outcome.result is None:
                    return await self._pause_for_tool(
                        session,
                        thread=thread,
                        turn=turn,
                        run=run,
                        skill_run=skill_run,
                        task=task,
                        status=outcome.status,
                        skill_name="运营迭代证据预检",
                        artifact_type="operation_execution_plan",
                    )
                self._require_tool_scope(outcome.result, thread.account_id)
                fresh_server_context = _ServerSkillContext(
                    preloaded_tool_results={"account.data_context": dict(outcome.result)},
                    tool_audit_refs={
                        "account.data_context": {
                            "tool_call_id": outcome.tool_call.id,
                            "source_skill_run_id": skill_run.id,
                        }
                    },
                )
                await _complete_skill_stage(
                    session,
                    scope=scope,
                    step_code="read_data",
                    attempt=attempt,
                    commit=True,
                )
            data_context = fresh_server_context.preloaded_tool_results["account.data_context"]
            source_refs, missing_domains = _operation_evidence_sources(data_context)
            if missing_domains:
                report = OperationIterationPlan.model_validate(
                    composite_skill_runtime.build(
                        account_id=thread.account_id,
                        cycle_days=int(frozen_input.get("cycle_days") or 7),
                        topic_count=int(frozen_input.get("topic_count") or 5),
                        script_duration_seconds=(
                            int(frozen_input["script_duration_seconds"])
                            if frozen_input.get("script_duration_seconds") is not None
                            else None
                        ),
                        constraints=list(frozen_input.get("constraints") or []),
                        source_artifacts=source_refs,
                    )
                ).model_dump(mode="json")
                report["interrupt"] = {
                    "kind": "operation_evidence_required",
                    "missing_domains": missing_domains,
                }
                response = (
                    "开始下周内容规划前还缺少可审计的数据："
                    + "、".join(_operation_evidence_domain_label(item) for item in missing_domains)
                    + "。请先补录并确认对应数据后重新发起。"
                )
                output = {
                    "status": "waiting_user",
                    "task_id": task.id,
                    "artifact_id": None,
                    "artifact_type": "operation_execution_plan",
                    "report": report,
                    "response": response,
                    "error_code": "OPERATION_EVIDENCE_REQUIRED",
                    **_server_skill_context_snapshot(fresh_server_context),
                }
                await self._close_skill_state(
                    session,
                    thread=thread,
                    turn=turn,
                    run=run,
                    task=task,
                    skill_run=skill_run,
                    status="waiting_user",
                    response=response,
                    output_snapshot=output,
                    error_code="OPERATION_EVIDENCE_REQUIRED",
                )
                return self._existing_result(skill_run)
            if positioning_id is not None:
                positioning_sources = await _confirmed_source_artifacts(
                    session,
                    account_id=thread.account_id,
                    artifact_ids=[int(positioning_id)],
                )
                positioning = positioning_sources[0]
                if positioning["artifact_type"] != DeliverableType.POSITIONING_STRATEGY.value:
                    raise PermissionError("OPERATION_ITERATION_POSITIONING_ARTIFACT_INVALID")
                source_refs.append(
                    {
                        "artifact_id": positioning["artifact_id"],
                        "artifact_type": positioning["artifact_type"],
                        "version": positioning["version"],
                    }
                )
        await _start_skill_stage(
            session,
            scope=scope,
            step_code="prepare_deliverable",
            attempt=attempt,
        )
        previous_graph = (
            revision_bridge.previous_graph
            if revision_bridge is not None
            else (
                dict(skill_run.output_snapshot or {}).get("report", {}).get("child_skill_graph")
                if isinstance(dict(skill_run.output_snapshot or {}).get("report"), dict)
                else None
            )
        )
        report = OperationIterationPlan.model_validate(
            composite_skill_runtime.build(
                account_id=thread.account_id,
                cycle_days=int(frozen_input.get("cycle_days") or 7),
                topic_count=(
                    int(frozen_input["topic_count"])
                    if frozen_input.get("topic_count") is not None
                    else None
                ),
                script_duration_seconds=(
                    int(frozen_input["script_duration_seconds"])
                    if frozen_input.get("script_duration_seconds") is not None
                    else None
                ),
                source_artifacts=source_refs,
                constraints=list(frozen_input.get("constraints") or []),
                previous_graph=previous_graph,
            )
        ).model_dump(mode="json")
        nodes_by_code = {str(node["skill_code"]): node for node in report["child_skill_graph"]}
        parent_status = "completed"
        interrupt: dict[str, Any] | None = None
        for node in report["child_skill_graph"]:
            skill_code = str(node["skill_code"])
            if node["status"] == "completed":
                continue
            try:
                skill_registry.get(skill_code)
            except KeyError:
                node["status"] = "blocked"
                node["error_code"] = "REQUIRED_CHILD_OWNER_UNAVAILABLE"
                node["terminal_reason"] = "registered_skill_owner_missing"
                parent_status = "blocked"
                break
            dependencies = [nodes_by_code[str(code)] for code in node["depends_on"]]
            if any(item["status"] != "completed" for item in dependencies):
                break
            structured_input = dict(node["input"])
            child_server_context = fresh_server_context if skill_code == "topic_planning" else None
            if dependencies:
                dependency_artifact_ids = [
                    int(item["artifact_id"])
                    for item in dependencies
                    if isinstance(item.get("artifact_id"), int)
                ]
                if len(dependency_artifact_ids) != len(dependencies):
                    node["status"] = "blocked"
                    node["error_code"] = "REQUIRED_CHILD_DEPENDENCY_MISSING"
                    node["terminal_reason"] = "dependency_artifact_missing"
                    parent_status = "blocked"
                    break
                dependency_artifacts = list(
                    await session.execute(
                        select(Deliverable, SkillRun)
                        .join(SkillRun, SkillRun.id == Deliverable.skill_run_id)
                        .where(
                            Deliverable.id.in_(dependency_artifact_ids),
                            Deliverable.content_item_id == content.id,
                        )
                    )
                )
                artifacts_by_id = {
                    artifact.id: (artifact, source_skill_run)
                    for artifact, source_skill_run in dependency_artifacts
                }
                if set(artifacts_by_id) != set(dependency_artifact_ids):
                    node["status"] = "blocked"
                    node["error_code"] = "REQUIRED_CHILD_DEPENDENCY_MISSING"
                    node["terminal_reason"] = "dependency_artifact_scope_mismatch"
                    parent_status = "blocked"
                    break
                weekly_batch = int(frozen_input.get("topic_count") or 5) == 5
                if not weekly_batch and skill_code in {
                    "visual_brief_generation",
                    "content_calendar_planning",
                }:
                    approved_ids = {
                        artifact_id
                        for artifact_id, (artifact, _source_run) in artifacts_by_id.items()
                        if artifact.status == DeliverableStatus.APPROVED
                    }
                    if approved_ids != set(dependency_artifact_ids):
                        should_pause = await pause_composite_parent_for_artifacts(
                            session,
                            parent_skill_run=skill_run,
                            source_artifact_ids=dependency_artifact_ids,
                        )
                        if should_pause:
                            node["status"] = "waiting_user"
                            node["error_code"] = "DEPENDENCY_ARTIFACT_APPROVAL_REQUIRED"
                            node["terminal_reason"] = "dependency_artifact_not_approved"
                            parent_status = "waiting_user"
                            interrupt = {
                                "kind": "artifact_approval_required",
                                "skill_code": skill_code,
                                "source_artifact_ids": dependency_artifact_ids,
                            }
                            break
                if weekly_batch:
                    lineage_refs = tuple(
                        OperationLineageRef(
                            artifact_id=artifact_id,
                            version=artifacts_by_id[artifact_id][0].version,
                            source_skill_run_id=artifacts_by_id[artifact_id][1].id,
                            parent_skill_run_id=int(
                                dict(artifacts_by_id[artifact_id][1].output_snapshot or {}).get(
                                    "composite_parent_skill_run_id"
                                )
                                or skill_run.id
                            ),
                        )
                        for artifact_id in dependency_artifact_ids
                    )
                    child_server_context = _ServerSkillContext(
                        preloaded_tool_results=(
                            dict(fresh_server_context.preloaded_tool_results)
                            if fresh_server_context is not None
                            else {}
                        ),
                        tool_audit_refs=(
                            dict(fresh_server_context.tool_audit_refs)
                            if fresh_server_context is not None
                            else {}
                        ),
                        lineage_refs=lineage_refs,
                        revision_id=(
                            fresh_server_context.revision_id
                            if fresh_server_context is not None
                            else None
                        ),
                        revision_parent_skill_run_id=(
                            fresh_server_context.revision_parent_skill_run_id
                            if fresh_server_context is not None
                            else None
                        ),
                    )
                child_definition = skill_registry.get(skill_code)
                if "source_artifact_ids" in child_definition.input_model.model_fields:
                    structured_input["source_artifact_ids"] = dependency_artifact_ids
                if skill_code == "content_calendar_planning":
                    structured_input["days"] = int(frozen_input.get("cycle_days") or 7)
            if skill_code == "publishing_preparation":
                structured_input["content_item_id"] = content.id
            child_constraints = [
                json.dumps(
                    item,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                for item in node.get("constraints") or []
                if isinstance(item, dict)
            ]
            child_result = await self._execute_child_skill(
                session,
                user=user,
                thread=thread,
                turn=turn,
                run=run,
                parent_skill_run=skill_run,
                skill_code=skill_code,
                capability_request=CapabilityRequest(
                    org_id=user.org_id,
                    user_id=user.id,
                    account_id=thread.account_id,
                    thread_id=thread.id,
                    turn_id=turn.id,
                    run_id=run.id,
                    message=turn.user_input,
                    requested_skill_code=skill_code,
                    execution_preference="FORMAL_TASK",
                    structured_input=structured_input,
                    constraints=child_constraints,
                ),
                lease_owner=lease_owner,
                server_context=child_server_context,
            )
            node["status"] = child_result.status
            node["artifact_id"] = child_result.artifact_id
            node["error_code"] = child_result.error_code
            if child_result.status == "needs_review":
                artifact_ids = (
                    [child_result.artifact_id] if child_result.artifact_id is not None else []
                )
                should_pause = await pause_composite_parent_for_artifacts(
                    session,
                    parent_skill_run=skill_run,
                    source_artifact_ids=artifact_ids,
                )
                refreshed_child = await session.scalar(
                    select(SkillRun)
                    .where(SkillRun.id == child_result.skill_run_id)
                    .execution_options(populate_existing=True)
                )
                if not should_pause and refreshed_child is not None:
                    refreshed_result = self._existing_result(refreshed_child)
                    if refreshed_result.status == "completed":
                        node["status"] = "completed"
                        node["error_code"] = None
                        continue
                parent_status = "waiting_user"
                interrupt = {
                    "kind": "child_skill_paused",
                    "skill_code": skill_code,
                    "child_skill_run_id": child_result.skill_run_id,
                    "source_artifact_ids": artifact_ids,
                }
                break
            if child_result.status != "completed":
                parent_status = child_result.status
                interrupt = {
                    "kind": "child_skill_paused",
                    "skill_code": skill_code,
                    "child_skill_run_id": child_result.skill_run_id,
                    **(
                        {"source_artifact_ids": [child_result.artifact_id]}
                        if child_result.artifact_id is not None
                        else {}
                    ),
                }
                break
        report["required_children_completed"] = all(
            not bool(node["required"]) or node["status"] == "completed"
            for node in report["child_skill_graph"]
        )
        report["interrupt"] = interrupt
        if parent_status == "completed" and not report["required_children_completed"]:
            parent_status = "blocked"
            report["interrupt"] = {
                "kind": "required_child_incomplete",
                "skill_code": next(
                    str(node["skill_code"])
                    for node in report["child_skill_graph"]
                    if bool(node["required"]) and node["status"] != "completed"
                ),
            }
        OperationIterationPlan.model_validate(report)
        deliverable = None
        if parent_status == "completed" and report["required_children_completed"]:
            deliverable = await session.scalar(
                select(Deliverable).where(
                    Deliverable.skill_run_id == skill_run.id,
                    Deliverable.content_item_id == content.id,
                    Deliverable.agent_code == AgentCode.DECISION.value,
                    Deliverable.type == DeliverableType.PUBLISH_CALENDAR,
                )
            )
            if deliverable is None:
                deliverable = await write_runtime_deliverable(
                    session,
                    scope=scope,
                    content=content,
                    agent_code=AgentCode.DECISION.value,
                    deliverable_type=DeliverableType.PUBLISH_CALENDAR,
                    status=DeliverableStatus.PENDING_REVIEW,
                    payload=report,
                    note=(
                        "business_artifact_type=operation_execution_plan; "
                        "final required child graph"
                    ),
                )
        response = (
            "下一运营周期的全部专业成果已生成。"
            if parent_status == "completed"
            else (
                "已准备 5 条拍摄稿和 7 天安排。确认后将创建 5 条手动发布任务。"
                if parent_status == "waiting_permission"
                and isinstance(interrupt, dict)
                and interrupt.get("skill_code") == "publishing_preparation"
                else "运营迭代已安全暂停，等待完成当前子成果的确认或处理。"
            )
        )
        output = {
            "status": parent_status,
            "task_id": task.id,
            "artifact_id": deliverable.id if deliverable is not None else None,
            "artifact_type": "operation_execution_plan",
            "report": report,
            "response": response,
            **_server_skill_context_snapshot(fresh_server_context),
        }
        await _complete_skill_stage(
            session,
            scope=scope,
            step_code="prepare_deliverable",
            attempt=attempt,
            commit=False,
        )
        await self._close_skill_state(
            session,
            thread=thread,
            turn=turn,
            run=run,
            task=task,
            skill_run=skill_run,
            status=parent_status,
            response=response,
            output_snapshot=output,
        )
        return self._existing_result(skill_run)

    async def _execute_child_skill(
        self,
        session: AsyncSession,
        *,
        user: User,
        thread: ConversationThread,
        turn: ConversationTurn,
        run: AgentRun,
        parent_skill_run: SkillRun,
        skill_code: str,
        capability_request: CapabilityRequest,
        lease_owner: str,
        server_context: _ServerSkillContext | None = None,
    ) -> SkillExecutionResult:
        """Execute one child through the complete audited SkillRuntime boundary."""

        result = await self.execute(
            session,
            user=user,
            thread=thread,
            turn=turn,
            run=run,
            skill_code=skill_code,
            capability_request=capability_request,
            lease_owner=lease_owner,
            parent_skill_run_id=parent_skill_run.id,
            server_context=server_context,
        )
        child_skill_run = await session.get(SkillRun, result.skill_run_id)
        if child_skill_run is None:
            raise SkillRecoveryConflict("COMPOSITE_CHILD_SKILL_RUN_MISSING")
        if (
            dict(child_skill_run.output_snapshot or {}).get("composite_parent_skill_run_id")
            != parent_skill_run.id
        ):
            raise SkillRecoveryConflict("COMPOSITE_CHILD_PARENT_LINK_MISMATCH")
        return self._existing_result(child_skill_run)

    async def _execute_account_data_analysis(
        self,
        session: AsyncSession,
        *,
        user: User,
        thread: ConversationThread,
        turn: ConversationTurn,
        run: AgentRun,
        task: BrainTask,
        content: ContentItem,
        skill_run: SkillRun,
        scope: RuntimeScope,
        definition: SkillDefinition,
        frozen_input: dict[str, Any],
        lease_owner: str,
    ) -> SkillExecutionResult:
        attempt = max(1, run.attempt)
        await _start_skill_stage(
            session,
            scope=scope,
            step_code="read_data",
            attempt=attempt,
        )
        await self._heartbeat(session, run=run, lease_owner=lease_owner)
        comparison = str(frozen_input.get("comparison") or "auto")
        metric_codes = [
            str(item) for item in frozen_input.get("requested_metrics") or [] if str(item)
        ] or ["play", "follower_delta", "follower_count", "engagement_rate"]
        tool_executor = self._tool_executor or DurableToolExecutor(build_runtime_tool_adapter())
        try:
            outcome = await tool_executor.execute(
                task=task,
                user=user,
                request=RuntimeToolCall(
                    tool_code="account.metrics_analysis",
                    arguments={
                        "days": int(frozen_input.get("days") or 30),
                        "comparison": ("previous_period" if comparison == "auto" else comparison),
                        "metric_codes": metric_codes,
                        "top_n": int(frozen_input.get("top_n") or 5),
                    },
                    purpose="读取当前账号已确认数据并生成确定性分析事实",
                    idempotency_key=f"{skill_run.id}:account.metrics_analysis",
                ),
                project_id=thread.project_id,
                agent_code=AgentCode.DECISION.value,
                scope=scope,
                execution_owner=lease_owner,
            )
        except Exception as exc:
            raise _SkillStageFailure("read_data", attempt, exc) from exc
        if outcome.status != "success" or outcome.result is None:
            await _fail_skill_stage(
                session,
                scope=scope,
                step_code="read_data",
                attempt=attempt,
                error_code="TOOL_EXECUTION_FAILED",
            )
            return await self._pause_for_tool(
                session,
                thread=thread,
                turn=turn,
                run=run,
                skill_run=skill_run,
                task=task,
                status=outcome.status,
                artifact_type="account_analysis_answer",
            )
        self._require_tool_scope(outcome.result, thread.account_id)
        tool_result = AccountMetricAnalysis.model_validate(outcome.result).model_dump(mode="json")
        if isinstance(outcome.tool_call, AgentToolCall):
            outcome.tool_call.skill_run_id = skill_run.id
            outcome.tool_call.thread_id = thread.id
            outcome.tool_call.turn_id = turn.id
            await session.commit()
        await _complete_skill_stage(
            session,
            scope=scope,
            step_code="read_data",
            attempt=attempt,
            commit=True,
        )

        await _start_skill_stage(
            session,
            scope=scope,
            step_code="specialist_work",
            attempt=attempt,
        )
        question = str(frozen_input.get("question") or turn.user_input).strip()
        if tool_result["answerability"]["status"] == "insufficient":
            report = _build_account_analysis_answer(
                account_id=thread.account_id,
                question=question,
                tool_result=tool_result,
                expert_output={},
                critic=_CriticResult(True, 100, [], []),
                critic_iterations=1,
                participating_experts=[],
            )
            await _complete_skill_stage(
                session,
                scope=scope,
                step_code="specialist_work",
                attempt=attempt,
                commit=True,
            )
            return await self._persist_account_analysis_answer(
                session,
                thread=thread,
                turn=turn,
                run=run,
                task=task,
                content=content,
                skill_run=skill_run,
                scope=scope,
                definition=definition,
                report=report,
                producer=None,
                attempt=attempt,
            )

        expert_result: _ExpertResult | None = None
        try:
            expert_result = (
                await self._execute_expert_stage(
                    session,
                    user=user,
                    task=task,
                    scope=scope,
                    codes=(AgentCode.OPERATOR,),
                    purpose=(
                        "解释账号已确认数据并给出短周期验证建议；不得改写事实、"
                        "伪造证据或把相关性表述为因果。"
                    ),
                    evidence_refs=[
                        f"{item['source_type']}:{item['source_id']}"
                        for item in tool_result["evidence_refs"]
                    ],
                    step_keys={AgentCode.OPERATOR: "account-data-analysis:operator"},
                    upstream={
                        "question": question,
                        "answerability": tool_result["answerability"],
                        "facts": tool_result["facts"],
                        "content_rankings": tool_result["content_rankings"],
                        "data_quality": tool_result["data_quality"],
                        "evidence_refs": tool_result["evidence_refs"],
                    },
                    lease_owner=lease_owner,
                )
            )[0]
        except AgentHarnessError as exc:
            log.warning(
                "Account data analysis expert failed; returning deterministic facts: %s",
                exc,
            )
        await _complete_skill_stage(
            session,
            scope=scope,
            step_code="specialist_work",
            attempt=attempt,
            commit=True,
        )

        if expert_result is None:
            report = _build_account_analysis_answer(
                account_id=thread.account_id,
                question=question,
                tool_result=tool_result,
                expert_output={},
                critic=_CriticResult(
                    False,
                    0,
                    ["运营专家本轮未能完成解释"],
                    ["当前先查看确定性事实，稍后可重新生成解释和建议"],
                ),
                critic_iterations=1,
                participating_experts=[],
            )
            return await self._persist_account_analysis_answer(
                session,
                thread=thread,
                turn=turn,
                run=run,
                task=task,
                content=content,
                skill_run=skill_run,
                scope=scope,
                definition=definition,
                report=report,
                producer=None,
                attempt=attempt,
            )

        report = _build_account_analysis_answer(
            account_id=thread.account_id,
            question=question,
            tool_result=tool_result,
            expert_output=expert_result.output,
            critic=_CriticResult(False, 0, [], []),
            critic_iterations=1,
            participating_experts=[AgentCode.OPERATOR.value],
        )
        validate_account_analysis_grounding(report, tool_result)

        await _start_skill_stage(
            session,
            scope=scope,
            step_code="quality_review",
            attempt=attempt,
        )
        try:
            review = await self._review(
                session,
                user=user,
                task=task,
                invocation=expert_result.invocation,
                deliverable_id=None,
                report=report,
                evidence_refs=list(tool_result["evidence_refs"]),
                iteration=1,
            )
        except IntelligenceUnavailable:
            review = _CriticResult(
                False,
                0,
                ["自动质量审核暂时不可用"],
                ["请人工核对解释和建议"],
            )
        critic_iterations = 1
        if not review.passed:
            try:
                revised = (
                    await self._execute_expert_stage(
                        session,
                        user=user,
                        task=task,
                        scope=scope,
                        codes=(AgentCode.OPERATOR,),
                        purpose="仅按质量意见修订解释和建议，不得修改 Tool 事实与证据。",
                        evidence_refs=[
                            f"{item['source_type']}:{item['source_id']}"
                            for item in tool_result["evidence_refs"]
                        ],
                        step_keys={AgentCode.OPERATOR: "account-data-analysis:critic-revision"},
                        upstream={
                            "question": question,
                            "facts": tool_result["facts"],
                            "evidence_refs": tool_result["evidence_refs"],
                            "previous_answer": report.model_dump(mode="json"),
                            "critic": {
                                "issues": review.issues,
                                "suggestions": review.suggestions,
                            },
                        },
                        lease_owner=lease_owner,
                    )
                )[0]
                expert_result = revised
                report = _build_account_analysis_answer(
                    account_id=thread.account_id,
                    question=question,
                    tool_result=tool_result,
                    expert_output=revised.output,
                    critic=review,
                    critic_iterations=2,
                    participating_experts=[AgentCode.OPERATOR.value],
                )
                validate_account_analysis_grounding(report, tool_result)
                review = await self._review(
                    session,
                    user=user,
                    task=task,
                    invocation=revised.invocation,
                    deliverable_id=None,
                    report=report,
                    evidence_refs=list(tool_result["evidence_refs"]),
                    iteration=2,
                )
                critic_iterations = 2
            except (AgentHarnessError, IntelligenceUnavailable):
                pass
        report = report.model_copy(
            update={
                "critic": AccountDataAnalysisCriticOutcome(
                    passed=review.passed,
                    score=review.score,
                    iterations=critic_iterations,
                    issues=review.issues,
                    suggestions=review.suggestions,
                )
            }
        )
        validate_account_analysis_grounding(report, tool_result)
        await _complete_skill_stage(
            session,
            scope=scope,
            step_code="quality_review",
            attempt=attempt,
            commit=True,
        )
        return await self._persist_account_analysis_answer(
            session,
            thread=thread,
            turn=turn,
            run=run,
            task=task,
            content=content,
            skill_run=skill_run,
            scope=scope,
            definition=definition,
            report=report,
            producer=expert_result.invocation,
            attempt=attempt,
        )

    async def _persist_account_analysis_answer(
        self,
        session: AsyncSession,
        *,
        thread: ConversationThread,
        turn: ConversationTurn,
        run: AgentRun,
        task: BrainTask,
        content: ContentItem,
        skill_run: SkillRun,
        scope: RuntimeScope,
        definition: SkillDefinition,
        report: AccountDataAnalysisAnswer,
        producer: AgentInvocation | None,
        attempt: int,
    ) -> SkillExecutionResult:
        await _start_skill_stage(
            session,
            scope=scope,
            step_code="prepare_deliverable",
            attempt=attempt,
        )
        if producer is not None:
            self._require_formal_producer(
                producer,
                scope=scope,
                definition=definition,
            )
        definition.output_model.model_validate(report.model_dump(mode="json"))
        deliverable = await write_runtime_deliverable(
            session,
            scope=scope,
            content=content,
            agent_code=(
                producer.agent_code.value
                if producer is not None and isinstance(producer.agent_code, AgentCode)
                else AgentCode.DECISION.value
            ),
            deliverable_type=DeliverableType.REVIEW_REPORT,
            status=DeliverableStatus.APPROVED,
            payload=report.model_dump(mode="json"),
            note=(
                "business_artifact_type=account_analysis_answer; "
                "deterministic facts and evidence owned by account.metrics_analysis"
            ),
        )
        skill_run.quality_score = Decimal(str(report.critic.score / 100))
        response = (
            "当前数据不足，我已说明缺口和下一步补数方式。"
            if report.answerability.status == "insufficient"
            else "账号数据分析已完成，已给出结论、依据和下一步建议。"
        )
        output = {
            "status": "completed",
            "task_id": task.id,
            "artifact_id": deliverable.id,
            "artifact_type": "account_analysis_answer",
            "report": report.model_dump(mode="json"),
            "response": response,
        }
        await _complete_skill_stage(
            session,
            scope=scope,
            step_code="prepare_deliverable",
            attempt=attempt,
            commit=False,
        )
        await self._close_skill_state(
            session,
            thread=thread,
            turn=turn,
            run=run,
            task=task,
            skill_run=skill_run,
            status="completed",
            response=response,
            output_snapshot=output,
        )
        return self._existing_result(skill_run)

    async def _execute_account_inspection(
        self,
        session: AsyncSession,
        *,
        user: User,
        thread: ConversationThread,
        turn: ConversationTurn,
        run: AgentRun,
        task: BrainTask,
        content: ContentItem,
        skill_run: SkillRun,
        scope: RuntimeScope,
        definition: SkillDefinition,
        days: int,
        attachment_contexts: list[dict[str, Any]],
        lease_owner: str,
    ) -> SkillExecutionResult:
        attempt = max(1, run.attempt)
        await _start_skill_stage(
            session,
            scope=scope,
            step_code="read_data",
            attempt=attempt,
        )
        tool_executor = self._tool_executor or DurableToolExecutor(build_runtime_tool_adapter())
        tool_results: dict[str, dict[str, Any]] = {}
        for tool_code, arguments in (
            ("account.profile", {}),
            ("account.data_context", {"days": days}),
        ):
            await self._heartbeat(session, run=run, lease_owner=lease_owner)
            try:
                outcome = await tool_executor.execute(
                    task=task,
                    user=user,
                    request=RuntimeToolCall(
                        tool_code=tool_code,
                        arguments=arguments,
                        purpose=f"一键账号体检读取 {tool_code}",
                        idempotency_key=f"{skill_run.id}:{tool_code}",
                    ),
                    project_id=thread.project_id,
                    agent_code=AgentCode.DECISION.value,
                    scope=scope,
                    execution_owner=lease_owner,
                )
            except Exception as exc:
                raise _SkillStageFailure("read_data", attempt, exc) from exc
            await self._heartbeat(session, run=run, lease_owner=lease_owner)
            if outcome.status != "success" or outcome.result is None:
                await _fail_skill_stage(
                    session,
                    scope=scope,
                    step_code="read_data",
                    attempt=attempt,
                    error_code="TOOL_EXECUTION_FAILED",
                )
                return await self._pause_for_tool(
                    session,
                    thread=thread,
                    turn=turn,
                    run=run,
                    skill_run=skill_run,
                    task=task,
                    status=outcome.status,
                )
            self._require_tool_scope(outcome.result, thread.account_id)
            tool_results[tool_code] = dict(outcome.result)
            if isinstance(outcome.tool_call, AgentToolCall):
                outcome.tool_call.skill_run_id = skill_run.id
                outcome.tool_call.thread_id = thread.id
                outcome.tool_call.turn_id = turn.id
                await session.commit()

        await _complete_skill_stage(
            session,
            scope=scope,
            step_code="read_data",
            attempt=attempt,
            commit=True,
        )

        data_context = tool_results["account.data_context"]
        evidence_refs = _evidence_refs(data_context)
        expert_results: list[Any] = []
        upstream_outputs: list[dict[str, Any]] = []
        tool_packet = [{"tool_code": code, "result": value} for code, value in tool_results.items()]
        definition_index = {code: index for index, code in enumerate(definition.expert_codes)}
        await _start_skill_stage(
            session,
            scope=scope,
            step_code="specialist_work",
            attempt=attempt,
        )
        for stage in definition.expert_stages:
            await self._heartbeat(session, run=run, lease_owner=lease_owner)
            stage_results = await self._execute_expert_stage(
                session,
                user=user,
                task=task,
                scope=scope,
                codes=tuple(AgentCode(code) for code in stage),
                purpose="基于所选账号证据完成一键账号体检，不得编造数据。",
                evidence_refs=[_evidence_label(item) for item in evidence_refs],
                step_keys={
                    AgentCode(code): (f"account-inspection:{definition_index[code]}:{code}")
                    for code in stage
                },
                upstream={
                    "tool_results": {"items": tool_packet},
                    "attachment_contexts": attachment_contexts,
                    "expert_outputs": list(upstream_outputs),
                },
                lease_owner=lease_owner,
            )
            await self._heartbeat(session, run=run, lease_owner=lease_owner)
            for result in stage_results:
                expert_results.append(result)
                upstream_outputs.append(
                    {
                        "agent_code": result.invocation.agent_code.value,
                        "summary": result.invocation.output_summary,
                        "payload": dict(result.output),
                    }
                )

        await _complete_skill_stage(
            session,
            scope=scope,
            step_code="specialist_work",
            attempt=attempt,
            commit=True,
        )

        latest_result = expert_results[-1]
        critic_history: list[_CriticResult] = []
        report = _build_report(
            account_id=thread.account_id,
            days=days,
            data_context=data_context,
            expert_results=expert_results,
            evidence_refs=evidence_refs,
            critic=_CriticResult(False, 0, [], []),
            critic_iterations=1,
        )
        await _start_skill_stage(
            session,
            scope=scope,
            step_code="quality_review",
            attempt=attempt,
        )
        for iteration in range(_MAX_CRITIC_IMPROVEMENTS + 1):
            await self._heartbeat(session, run=run, lease_owner=lease_owner)
            try:
                review = await self._review(
                    session,
                    user=user,
                    task=task,
                    invocation=latest_result.invocation,
                    deliverable_id=None,
                    report=report,
                    evidence_refs=evidence_refs,
                    iteration=iteration,
                )
            except IntelligenceUnavailable as exc:
                log.warning(
                    "Account-inspection quality review is unavailable; delivering "
                    "the evidence-backed report for human review: %s",
                    exc,
                )
                report = report.model_copy(
                    update={
                        "critic": AccountInspectionCriticOutcome(
                            passed=False,
                            score=0,
                            iterations=iteration + 1,
                            issues=["自动质量审核暂时不可用"],
                            suggestions=["请人工核对报告中的数据依据与优化建议"],
                        )
                    }
                )
                await _complete_skill_stage(
                    session,
                    scope=scope,
                    step_code="quality_review",
                    attempt=attempt,
                    commit=True,
                )
                await _start_skill_stage(
                    session,
                    scope=scope,
                    step_code="prepare_deliverable",
                    attempt=attempt,
                )
                return await self._complete_for_human_review(
                    session,
                    skill_run=skill_run,
                    task=task,
                    content=content,
                    thread=thread,
                    turn=turn,
                    run=run,
                    report=report,
                    scope=scope,
                    producer=latest_result.invocation,
                    attempt=attempt,
                )
            await self._heartbeat(session, run=run, lease_owner=lease_owner)
            critic_history.append(review)
            report = report.model_copy(
                update={
                    "critic": AccountInspectionCriticOutcome(
                        passed=review.passed,
                        score=review.score,
                        iterations=iteration + 1,
                        issues=review.issues,
                        suggestions=review.suggestions,
                    )
                }
            )
            if review.passed:
                break
            if iteration == _MAX_CRITIC_IMPROVEMENTS:
                await _complete_skill_stage(
                    session,
                    scope=scope,
                    step_code="quality_review",
                    attempt=attempt,
                    commit=True,
                )
                await _start_skill_stage(
                    session,
                    scope=scope,
                    step_code="prepare_deliverable",
                    attempt=attempt,
                )
                return await self._complete_for_human_review(
                    session,
                    skill_run=skill_run,
                    task=task,
                    content=content,
                    thread=thread,
                    turn=turn,
                    run=run,
                    report=report,
                    scope=scope,
                    producer=latest_result.invocation,
                    attempt=attempt,
                )
            await self._heartbeat(session, run=run, lease_owner=lease_owner)
            try:
                latest_result = await self._harness.execute(
                    session,
                    user=user,
                    task=task,
                    code=AgentCode.OPERATOR,
                    purpose="按质量审核意见修订账号体检建议，不得编造数据。",
                    evidence_refs=[_evidence_label(item) for item in evidence_refs],
                    run_id=run.id,
                    skill_run_id=skill_run.id,
                    thread_id=thread.id,
                    turn_id=turn.id,
                    step_key=(f"account-inspection:critic-revision:{AgentCode.OPERATOR.value}"),
                    attempt=iteration + 1,
                    upstream={
                        "tool_results": {"items": tool_packet},
                        "critic": {
                            "issues": review.issues,
                            "suggestions": review.suggestions,
                        },
                    },
                    scope=scope,
                    trace_only=True,
                )
            except AgentHarnessError as exc:
                log.warning(
                    "Account-inspection critic revision failed; delivering the "
                    "last evidence-backed report for human review: %s",
                    exc,
                )
                await _complete_skill_stage(
                    session,
                    scope=scope,
                    step_code="quality_review",
                    attempt=attempt,
                    commit=True,
                )
                await _start_skill_stage(
                    session,
                    scope=scope,
                    step_code="prepare_deliverable",
                    attempt=attempt,
                )
                return await self._complete_for_human_review(
                    session,
                    skill_run=skill_run,
                    task=task,
                    content=content,
                    thread=thread,
                    turn=turn,
                    run=run,
                    report=report,
                    scope=scope,
                    producer=latest_result.invocation,
                    attempt=attempt,
                )
            await self._heartbeat(session, run=run, lease_owner=lease_owner)
            await self._attach_expert_provenance(
                session,
                result=latest_result,
                thread=thread,
                turn=turn,
                run=run,
                skill_run=skill_run,
            )
            expert_results.append(latest_result)
            report = _build_report(
                account_id=thread.account_id,
                days=days,
                data_context=data_context,
                expert_results=expert_results,
                evidence_refs=evidence_refs,
                critic=review,
                critic_iterations=iteration + 1,
            )

        await _complete_skill_stage(
            session,
            scope=scope,
            step_code="quality_review",
            attempt=attempt,
            commit=True,
        )

        await self._heartbeat(session, run=run, lease_owner=lease_owner)
        await _start_skill_stage(
            session,
            scope=scope,
            step_code="prepare_deliverable",
            attempt=attempt,
        )
        self._require_formal_producer(
            latest_result.invocation,
            scope=scope,
            definition=definition,
        )
        definition.output_model.model_validate(report.model_dump(mode="json"))
        final_deliverable = await write_runtime_deliverable(
            session,
            scope=scope,
            content=content,
            agent_code=latest_result.invocation.agent_code.value,
            deliverable_type=DeliverableType.REVIEW_REPORT,
            status=DeliverableStatus.PENDING_REVIEW,
            payload=_review_report_payload(report),
            note=(
                "business_artifact_type=account_inspection_report; "
                f"producer_invocation_id={latest_result.invocation.id}; "
                "generated by account_inspection Skill"
            ),
        )
        quality = await session.scalar(
            select(AgentQualityScore)
            .where(
                AgentQualityScore.skill_run_id == skill_run.id,
                AgentQualityScore.passed.is_(True),
            )
            .order_by(AgentQualityScore.iteration.desc())
        )
        if quality is not None:
            quality.deliverable_id = final_deliverable.id

        skill_run.quality_score = Decimal(str(report.critic.score / 100))
        output = {
            "status": "completed",
            "task_id": task.id,
            "artifact_id": final_deliverable.id,
            "artifact_type": "account_inspection_report",
            "report": report.model_dump(mode="json"),
            "response": "账号体检已完成，正式体检报告已生成。",
        }
        await _complete_skill_stage(
            session,
            scope=scope,
            step_code="prepare_deliverable",
            attempt=attempt,
            commit=False,
        )
        await SkillRuntime._close_skill_state(
            session,
            thread=thread,
            turn=turn,
            run=run,
            task=task,
            skill_run=skill_run,
            status="completed",
            response=output["response"],
            output_snapshot=output,
        )
        return self._existing_result(skill_run)

    async def _execute_operating_skill(
        self,
        session: AsyncSession,
        *,
        user: User,
        thread: ConversationThread,
        turn: ConversationTurn,
        run: AgentRun,
        task: BrainTask,
        content: ContentItem,
        skill_run: SkillRun,
        scope: RuntimeScope,
        definition: SkillDefinition,
        frozen_input: dict[str, Any],
        lease_owner: str,
    ) -> SkillExecutionResult:
        """Run one bounded specialist graph and persist its business artifact."""

        attempt = max(1, run.attempt)
        tool_executor = self._tool_executor or DurableToolExecutor(build_runtime_tool_adapter())
        tool_results: dict[str, dict[str, Any]] = {}
        server_context = _server_skill_context_from_snapshot(frozen_input.get("_server_context"))
        if server_context is not None:
            for tool_code, result in server_context.preloaded_tool_results.items():
                audit_ref = server_context.tool_audit_refs.get(tool_code) or {}
                tool_call_id = audit_ref.get("tool_call_id")
                source_skill_run_id = audit_ref.get("source_skill_run_id")
                tool_call = (
                    await session.get(AgentToolCall, tool_call_id)
                    if isinstance(tool_call_id, int)
                    else None
                )
                cross_run_audit_valid = False
                if (
                    tool_call is not None
                    and tool_call.turn_id != turn.id
                    and server_context.revision_id is not None
                    and server_context.revision_parent_skill_run_id is not None
                ):
                    revision_bridge = await resolve_operation_revision_bridge(
                        session,
                        scope=scope,
                        current_parent_skill_run_id=(server_context.revision_parent_skill_run_id),
                    )
                    cross_run_audit_valid = (
                        revision_bridge is not None
                        and revision_bridge.revision_id == server_context.revision_id
                        and revision_bridge.source_turn_id == tool_call.turn_id
                        and revision_bridge.source_parent_skill_run_id == source_skill_run_id
                    )
                if (
                    tool_call is None
                    or tool_call.status != "success"
                    or tool_call.tool_code != tool_code
                    or tool_call.skill_run_id != source_skill_run_id
                    or tool_call.task_id != task.id
                    or tool_call.thread_id != thread.id
                    or (tool_call.turn_id != turn.id and not cross_run_audit_valid)
                    or dict((tool_call.meta or {}).get("result") or {}) != result
                ):
                    raise SkillRecoveryConflict("PRELOADED_TOOL_AUDIT_MISMATCH")
                self._require_tool_scope(result, thread.account_id)
                tool_results[tool_code] = dict(result)
        tool_plan = build_skill_tool_plan(definition)

        async def execute_tool(
            tool_code: str,
            *,
            step_code: str,
        ) -> SkillExecutionResult | None:
            arguments = _operating_tool_arguments(
                tool_code=tool_code,
                frozen_input=frozen_input,
                content=content,
                user_input=turn.user_input,
            )
            await self._heartbeat(session, run=run, lease_owner=lease_owner)
            try:
                outcome = await tool_executor.execute(
                    task=task,
                    user=user,
                    request=RuntimeToolCall(
                        tool_code=tool_code,
                        arguments=arguments,
                        purpose=f"{definition.name}: {tool_code}",
                        idempotency_key=f"{skill_run.id}:{tool_code}",
                    ),
                    project_id=thread.project_id,
                    agent_code=AgentCode.DECISION.value,
                    scope=scope,
                    execution_owner=lease_owner,
                )
            except Exception as exc:
                raise _SkillStageFailure(step_code, attempt, exc) from exc
            if outcome.status != "success" or outcome.result is None:
                await _fail_skill_stage(
                    session,
                    scope=scope,
                    step_code=step_code,
                    attempt=attempt,
                    error_code="TOOL_EXECUTION_FAILED",
                )
                return await self._pause_for_tool(
                    session,
                    thread=thread,
                    turn=turn,
                    run=run,
                    skill_run=skill_run,
                    task=task,
                    status=outcome.status,
                    skill_name=definition.name,
                    artifact_type=definition.artifact_type or definition.code,
                )
            self._require_tool_scope(outcome.result, thread.account_id)
            tool_results[tool_code] = dict(outcome.result)
            if isinstance(outcome.tool_call, AgentToolCall):
                outcome.tool_call.skill_run_id = skill_run.id
                outcome.tool_call.thread_id = thread.id
                outcome.tool_call.turn_id = turn.id
                await session.commit()
            return None

        read_steps = [step for step in tool_plan if step.phase == "read"]
        if read_steps:
            await _start_skill_stage(
                session,
                scope=scope,
                step_code="read_data",
                attempt=attempt,
            )
            for step in read_steps:
                if step.tool_code in tool_results:
                    continue
                paused = await execute_tool(step.tool_code, step_code="read_data")
                if paused is not None:
                    return paused
            await _complete_skill_stage(
                session,
                scope=scope,
                step_code="read_data",
                attempt=attempt,
                commit=True,
            )

        if definition.code == "engagement_review":
            engagement_context = tool_results.get("account.engagement_context", {})
            if not engagement_context.get("comment_samples"):
                report = EngagementReviewReport(
                    account_id=thread.account_id,
                    period=dict(engagement_context.get("period") or {}),
                    status="needs_input",
                    evidence_refs=_evidence_refs(engagement_context),
                    missing_data=[
                        "缺少当前账号可核验的评论正文，聚合评论量不能用于推断常见问题或情绪。"
                    ],
                ).model_dump(mode="json")
                output = {
                    "status": "waiting_user",
                    "task_id": task.id,
                    "artifact_id": None,
                    "artifact_type": "engagement_review",
                    "report": report,
                    "response": "已读取互动汇总，但缺少评论正文。请同步评论明细后再做互动复盘。",
                    "error_code": "ENGAGEMENT_SAMPLES_REQUIRED",
                }
                await self._close_skill_state(
                    session,
                    thread=thread,
                    turn=turn,
                    run=run,
                    task=task,
                    skill_run=skill_run,
                    status="waiting_user",
                    response=output["response"],
                    output_snapshot=output,
                    error_code=output["error_code"],
                )
                return self._existing_result(skill_run)

        expert_results: list[Any] = []
        upstream_outputs: list[dict[str, Any]] = []
        evidence_context = (
            tool_results.get("account.engagement_context", {})
            if definition.code == "engagement_review"
            else tool_results.get("account.data_context", {})
        )
        evidence_refs = _evidence_refs(evidence_context)
        requested_source_ids = list(frozen_input.get("source_artifact_ids") or [])
        if server_context is not None and server_context.lineage_refs:
            if not requested_source_ids:
                requested_source_ids = [ref.artifact_id for ref in server_context.lineage_refs]
            parent_skill_run_id = dict(skill_run.output_snapshot or {}).get(
                "composite_parent_skill_run_id"
            )
            if type(parent_skill_run_id) is not int:
                raise SkillRecoveryConflict("OPERATION_LINEAGE_PARENT_MISSING")
            source_artifacts = await resolve_internal_lineage_artifacts(
                session,
                refs=list(server_context.lineage_refs),
                expected_parent_skill_run_id=parent_skill_run_id,
                expected_source_artifact_ids=requested_source_ids,
                scope=scope,
            )
        else:
            source_artifacts = await _confirmed_source_artifacts(
                session,
                account_id=thread.account_id,
                artifact_ids=requested_source_ids,
            )
        evidence_refs.extend(
            {
                "artifact_id": item["artifact_id"],
                "artifact_type": item["artifact_type"],
                "version": item["version"],
            }
            for item in source_artifacts
        )
        definition_index = {code: index for index, code in enumerate(definition.expert_codes)}
        await _start_skill_stage(
            session,
            scope=scope,
            step_code="specialist_work",
            attempt=attempt,
        )
        for stage in definition.expert_stages:
            await self._heartbeat(session, run=run, lease_owner=lease_owner)
            stage_results = await self._execute_expert_stage(
                session,
                user=user,
                task=task,
                scope=scope,
                codes=tuple(AgentCode(code) for code in stage),
                purpose=(
                    f"完成“{definition.name}”：{turn.user_input}。"
                    + (
                        "必须遵守结构化要求："
                        + "；".join(frozen_input["_server_request_constraints"])
                        + "。"
                        if frozen_input.get("_server_request_constraints")
                        else ""
                    )
                    + "必须遵守当前账号范围，不得编造数据或声称已经发布。"
                ),
                evidence_refs=[_evidence_label(item) for item in evidence_refs],
                step_keys={
                    AgentCode(code): f"{definition.code}:{definition_index[code]}:{code}"
                    for code in stage
                },
                upstream={
                    "structured_input": dict(frozen_input),
                    "tool_results": {
                        "items": [
                            {"tool_code": key, "result": value}
                            for key, value in tool_results.items()
                        ]
                    },
                    "attachment_contexts": list(frozen_input.get("attachment_contexts") or []),
                    "source_artifacts": source_artifacts,
                    "expert_outputs": list(upstream_outputs),
                },
                lease_owner=lease_owner,
            )
            await self._heartbeat(session, run=run, lease_owner=lease_owner)
            for result in stage_results:
                expert_results.append(result)
                upstream_outputs.append(
                    {
                        "agent_code": result.invocation.agent_code.value,
                        "summary": result.invocation.output_summary,
                        "payload": dict(result.output),
                    }
                )

        await _complete_skill_stage(
            session,
            scope=scope,
            step_code="specialist_work",
            attempt=attempt,
            commit=True,
        )

        await _start_skill_stage(
            session,
            scope=scope,
            step_code="prepare_deliverable",
            attempt=attempt,
        )

        for step in tool_plan:
            if step.phase == "side_effect":
                raise SkillToolPlanError(f"SKILL_SIDE_EFFECT_REQUIRES_APPROVAL:{step.tool_code}")
            if step.phase != "prepare":
                continue
            paused = await execute_tool(
                step.tool_code,
                step_code="prepare_deliverable",
            )
            if paused is not None:
                return paused

        operation_mode = (
            type(dict(skill_run.output_snapshot or {}).get("composite_parent_skill_run_id")) is int
        )
        report, deliverable_type, deliverable_payload = _build_operating_report(
            definition=definition,
            account_id=thread.account_id,
            platform=str(tool_results.get("account.profile", {}).get("platform") or "douyin"),
            user_input=turn.user_input,
            frozen_input=frozen_input,
            tool_results=tool_results,
            expert_results=expert_results,
            evidence_refs=evidence_refs,
            source_artifacts=source_artifacts,
            operation_mode=operation_mode,
            execution_date=(skill_run.created_at or utc_now())
            .astimezone(ZoneInfo("Asia/Shanghai"))
            .date(),
        )
        definition.output_model.model_validate(report)
        self._require_formal_producer(
            expert_results[-1].invocation,
            scope=scope,
            definition=definition,
        )
        quality_payload = report.get("quality")
        if (
            operation_mode
            and definition.code
            in {
                "topic_planning",
                "script_generation",
                "visual_brief_generation",
                "content_calendar_planning",
            }
            and isinstance(quality_payload, dict)
            and quality_payload.get("status") != "passed"
        ):
            return await self._complete_operating_skill_for_human_review(
                session,
                content=content,
                thread=thread,
                turn=turn,
                run=run,
                task=task,
                skill_run=skill_run,
                scope=scope,
                definition=definition,
                report=report,
                deliverable_type=deliverable_type,
                deliverable_payload=deliverable_payload,
                producer=expert_results[-1].invocation,
                review_reason="结构化质量检查未通过。",
                attempt=attempt,
            )
        if definition.critic_policy == "required":
            await _start_skill_stage(
                session,
                scope=scope,
                step_code="quality_review",
                attempt=attempt,
            )
            if self._critic is None:
                await _complete_skill_stage(
                    session,
                    scope=scope,
                    step_code="quality_review",
                    attempt=attempt,
                    commit=True,
                )
                return await self._complete_operating_skill_for_human_review(
                    session,
                    content=content,
                    thread=thread,
                    turn=turn,
                    run=run,
                    task=task,
                    skill_run=skill_run,
                    scope=scope,
                    definition=definition,
                    report=report,
                    deliverable_type=deliverable_type,
                    deliverable_payload=deliverable_payload,
                    producer=expert_results[-1].invocation,
                    review_reason="No safe Critic adapter is configured.",
                    attempt=attempt,
                )
            review = await self._critic.review(
                session=session,
                task=task,
                invocation=expert_results[-1].invocation,
                report=report,
                evidence_refs=evidence_refs,
                iteration=0,
            )
            await _complete_skill_stage(
                session,
                scope=scope,
                step_code="quality_review",
                attempt=attempt,
                commit=True,
            )
            if not bool(review.passed):
                return await self._complete_operating_skill_for_human_review(
                    session,
                    content=content,
                    thread=thread,
                    turn=turn,
                    run=run,
                    task=task,
                    skill_run=skill_run,
                    scope=scope,
                    definition=definition,
                    report=report,
                    deliverable_type=deliverable_type,
                    deliverable_payload=deliverable_payload,
                    producer=expert_results[-1].invocation,
                    review_reason="Critic review requires human confirmation.",
                    quality_score=int(review.score),
                    attempt=attempt,
                )
        deliverable = await write_runtime_deliverable(
            session,
            scope=scope,
            content=content,
            agent_code=expert_results[-1].invocation.agent_code.value,
            deliverable_type=deliverable_type,
            status=DeliverableStatus.PENDING_REVIEW,
            payload=deliverable_payload,
            note=(
                f"producer_invocation_id={expert_results[-1].invocation.id}; "
                f"generated by {definition.code} Skill"
            ),
        )
        content.status = ContentStatus.DRAFT
        if definition.approval_policy == "before_finish":
            approval_call = await session.scalar(
                select(AgentToolCall)
                .where(
                    AgentToolCall.skill_run_id == skill_run.id,
                    AgentToolCall.status == "success",
                )
                .order_by(AgentToolCall.id.desc())
            )
            if approval_call is None:
                raise SkillRecoveryConflict("SKILL_FINISH_APPROVAL_TOOL_MISSING")
            approval_call.status = "waiting_approval"
            approval_call.permission_mode = "confirm"
            approval_call.requires_human_confirmation = True
            approval_call.meta = {
                **(approval_call.meta or {}),
                "approval_stage": "before_finish",
                "artifact_id": deliverable.id,
            }
            if definition.code == "publishing_preparation":
                deliverable.payload = {
                    **dict(deliverable.payload or {}),
                    "approval_tool_call_id": approval_call.id,
                }
            await add_approval_requested(
                session,
                org_id=task.org_id,
                project_id=thread.project_id,
                content_item_id=task.content_item_id,
                approval_kind="skill_finish",
                source_id=approval_call.id,
                title=f"{definition.name}待确认",
                body=(
                    "确认这份 7 天安排并创建 5 条手动发布任务。"
                    if definition.code == "publishing_preparation"
                    else "发布准备包已经生成，确认后本次 Skill 才会完成。"
                ),
            )
            waiting_response = (
                "已准备 5 条拍摄稿和 7 天安排。确认后将创建 5 条手动发布任务。"
                if definition.code == "publishing_preparation" and operation_mode
                else f"{definition.name}已生成，等待你确认后完成。"
            )
            output = {
                "status": "waiting_permission",
                "task_id": task.id,
                "artifact_id": deliverable.id,
                "artifact_type": definition.artifact_type or definition.code,
                "report": report,
                "response": waiting_response,
                "approval_tool_call_id": approval_call.id,
            }
            await _complete_skill_stage(
                session,
                scope=scope,
                step_code="prepare_deliverable",
                attempt=attempt,
                commit=False,
            )
            await self._close_skill_state(
                session,
                thread=thread,
                turn=turn,
                run=run,
                task=task,
                skill_run=skill_run,
                status="waiting_permission",
                response=output["response"],
                output_snapshot=output,
            )
            return self._existing_result(skill_run)
        output = {
            "status": "completed",
            "task_id": task.id,
            "artifact_id": deliverable.id,
            "artifact_type": definition.artifact_type or definition.code,
            "report": report,
            "response": f"{definition.name} completed.",
        }
        await _complete_skill_stage(
            session,
            scope=scope,
            step_code="prepare_deliverable",
            attempt=attempt,
            commit=False,
        )
        await self._close_skill_state(
            session,
            thread=thread,
            turn=turn,
            run=run,
            task=task,
            skill_run=skill_run,
            status="completed",
            response=output["response"],
            output_snapshot=output,
        )
        return self._existing_result(skill_run)

    async def _complete_operating_skill_for_human_review(
        self,
        session: AsyncSession,
        *,
        content: ContentItem,
        thread: ConversationThread,
        turn: ConversationTurn,
        run: AgentRun,
        task: BrainTask,
        skill_run: SkillRun,
        scope: RuntimeScope,
        definition: SkillDefinition,
        report: dict[str, Any],
        deliverable_type: DeliverableType,
        deliverable_payload: dict[str, Any],
        producer: AgentInvocation,
        review_reason: str,
        attempt: int,
        quality_score: int | None = None,
    ) -> SkillExecutionResult:
        deliverable = await write_runtime_deliverable(
            session,
            scope=scope,
            content=content,
            agent_code=producer.agent_code.value,
            deliverable_type=deliverable_type,
            status=DeliverableStatus.PENDING_REVIEW,
            payload=deliverable_payload,
            note=(
                f"producer_invocation_id={producer.id}; "
                f"generated by {definition.code} Skill; "
                f"quality review pending: {review_reason}"
            ),
        )
        content.status = ContentStatus.DRAFT
        if quality_score is not None:
            skill_run.quality_score = Decimal(str(quality_score / 100))
        await session.flush()
        output = {
            "status": "needs_review",
            "task_id": task.id,
            "artifact_id": deliverable.id,
            "artifact_type": definition.artifact_type or definition.code,
            "report": report,
            "response": f"{definition.name} completed and is pending human review.",
            "review_reason": review_reason,
        }
        await _complete_skill_stage(
            session,
            scope=scope,
            step_code="prepare_deliverable",
            attempt=attempt,
            commit=False,
        )
        await SkillRuntime._close_skill_state(
            session,
            thread=thread,
            turn=turn,
            run=run,
            task=task,
            skill_run=skill_run,
            status="completed",
            response=output["response"],
            output_snapshot=output,
            skill_status="needs_review",
        )
        return SkillRuntime._existing_result(skill_run)

    async def _execute_expert_stage(
        self,
        session: AsyncSession,
        *,
        user: User,
        task: BrainTask,
        scope: RuntimeScope,
        codes: tuple[AgentCode, ...],
        purpose: str,
        evidence_refs: list[str],
        step_keys: dict[AgentCode, str],
        upstream: dict[str, Any],
        lease_owner: str,
    ) -> list[_ExpertResult]:
        frozen_upstream = json.loads(json.dumps(upstream))
        if self._harness is not agent_harness:
            results: list[_ExpertResult] = []
            for code in codes:
                result = await self._harness.execute(
                    session,
                    user=user,
                    task=task,
                    code=code,
                    purpose=purpose,
                    evidence_refs=evidence_refs,
                    run_id=scope.run_id,
                    skill_run_id=scope.skill_run_id,
                    thread_id=scope.thread_id,
                    turn_id=scope.turn_id,
                    step_key=step_keys[code],
                    attempt=0,
                    upstream=json.loads(json.dumps(frozen_upstream)),
                    scope=scope,
                    trace_only=True,
                    execution_owner=lease_owner,
                )
                results.append(
                    _ExpertResult(
                        invocation=result.invocation,
                        output=dict(result.output or {}),
                    )
                )
            return results

        session_factory = async_sessionmaker(session.bind, expire_on_commit=False)
        operations = [
            (
                lambda code=code: self._harness.execute_trace_isolated(
                    scope=scope,
                    code=code,
                    purpose=purpose,
                    evidence_refs=evidence_refs,
                    step_key=step_keys[code],
                    attempt=0,
                    upstream=json.loads(json.dumps(frozen_upstream)),
                    execution_owner=lease_owner,
                    session_factory=session_factory,
                )
            )
            for code in codes
        ]
        trace_results = await run_bounded_stage(operations, limit=3)
        results = []
        for trace in trace_results:
            invocation = await session.get(AgentInvocation, trace.invocation_id)
            if invocation is None:
                raise SkillRecoveryConflict("SKILL_INVOCATION_TRACE_MISSING")
            results.append(_ExpertResult(invocation=invocation, output=dict(trace.output)))
        return results

    async def _review(
        self,
        session: AsyncSession,
        *,
        user: User,
        task: BrainTask,
        invocation: Any,
        deliverable_id: int | None,
        report: AccountInspectionReport | AccountDataAnalysisAnswer,
        evidence_refs: list[dict[str, Any]],
        iteration: int,
    ) -> _CriticResult:
        if self._critic is not None:
            result = await self._critic.review(
                session=session,
                task=task,
                invocation=invocation,
                report=report.model_dump(mode="json"),
                evidence_refs=evidence_refs,
                iteration=iteration,
            )
            return _CriticResult(
                passed=bool(result.passed),
                score=int(result.score),
                issues=list(result.issues),
                suggestions=list(result.suggestions),
            )

        model_review = await brain_intelligence.review_expert_output(
            session,
            user.org_id,
            goal=task.title,
            expert_code=AgentCode.CONTENT_DIRECTOR.value,
            expert_name="内容策略专家",
            deliverable=report.model_dump(mode="json"),
            situation={},
            strategy={},
            evidence_refs=evidence_refs,
            iteration=iteration,
        )
        recorded = await ai_coo_critic_service.record(
            session,
            task=task,
            invocation=invocation,
            deliverable_id=deliverable_id,
            evaluation=model_review.evaluation,
            iteration=iteration,
            evidence_refs=evidence_refs,
            prompt_id=model_review.prompt.spec.id,
            prompt_version=model_review.prompt.spec.version,
            prompt_hash=model_review.prompt.content_hash,
            critic_model=model_review.model,
        )
        score = recorded.score
        score.thread_id = invocation.thread_id
        score.turn_id = invocation.turn_id
        score.run_id = invocation.run_id
        score.skill_run_id = invocation.skill_run_id
        await session.commit()
        return _CriticResult(
            passed=recorded.disposition is CriticDisposition.PASS,
            score=int(score.score),
            issues=list(score.issues or []),
            suggestions=list(score.suggestions or []),
        )

    @staticmethod
    async def _heartbeat(
        session: AsyncSession,
        *,
        run: AgentRun,
        lease_owner: str,
    ) -> None:
        renewed = await heartbeat_agent_run(
            session,
            run.id,
            worker_id=lease_owner,
            lease_seconds=settings.agent_run_lease_seconds,
        )
        if not renewed:
            raise _SkillLeaseLost("Skill execution lease ownership changed")

    @staticmethod
    async def _interrupt_ambiguous_side_effects(
        session: AsyncSession,
        *,
        run: AgentRun,
        turn: ConversationTurn,
        skill_run: SkillRun,
        task: BrainTask,
    ) -> bool:
        with session.no_autoflush:
            discovered_tool_ids = tuple(
                await session.scalars(
                    select(AgentToolCall.id)
                    .where(
                        AgentToolCall.skill_run_id == skill_run.id,
                        AgentToolCall.status.in_({"planned", "running", "ambiguous"}),
                    )
                    .order_by(AgentToolCall.id)
                )
            )
            discovered_invocation_ids = tuple(
                await session.scalars(
                    select(AgentInvocation.id)
                    .where(
                        AgentInvocation.skill_run_id == skill_run.id,
                        AgentInvocation.status.in_(
                            {
                                AgentInvocationStatus.QUEUED,
                                AgentInvocationStatus.RUNNING,
                            }
                        ),
                    )
                    .order_by(AgentInvocation.id)
                )
            )
            discovered_attempt_ids = tuple(
                await session.scalars(
                    select(ToolExecutionAttempt.id)
                    .where(
                        ToolExecutionAttempt.tool_call_id.in_(discovered_tool_ids),
                        ToolExecutionAttempt.status == "dispatched",
                    )
                    .order_by(ToolExecutionAttempt.id)
                )
            )
            root_skill_id, child_skill_ids = await discover_runtime_skill_lock_ids(
                session, skill_run.id
            )
        runtime_lock = await lock_runtime_root_scope(
            session,
            run_id=run.id,
            expected_turn_id=turn.id,
            expected_task_id=task.id,
            expected_content_item_id=task.content_item_id,
            root_skill_run_id=root_skill_id,
            child_skill_run_ids=child_skill_ids,
            invocation_ids=discovered_invocation_ids,
            tool_call_ids=discovered_tool_ids,
            attempt_ids=discovered_attempt_ids,
        )
        require_runtime_root_lock(
            session,
            runtime_lock,
            run_id=run.id,
            invocation_ids=discovered_invocation_ids,
            tool_call_ids=discovered_tool_ids,
            attempt_ids=discovered_attempt_ids,
        )
        ambiguous_writes = list(
            await session.scalars(
                select(AgentToolCall)
                .outerjoin(
                    ToolExecutionAttempt,
                    ToolExecutionAttempt.tool_call_id == AgentToolCall.id,
                )
                .where(
                    AgentToolCall.skill_run_id == skill_run.id,
                    AgentToolCall.side_effect_level != "read",
                    or_(
                        AgentToolCall.status == "ambiguous",
                        and_(
                            AgentToolCall.status == "running",
                            ToolExecutionAttempt.status == "dispatched",
                        ),
                    ),
                )
                .distinct()
                .execution_options(populate_existing=True)
            )
        )
        if ambiguous_writes:
            now = datetime.now(UTC)
            error_code = "TOOL_RESULT_AMBIGUOUS"
            ambiguous_ids = [tool_call.id for tool_call in ambiguous_writes]
            for tool_call in ambiguous_writes:
                tool_call.status = "ambiguous"
                tool_call.error = error_code
                tool_call.finished_at = now
            dispatched_attempts = list(
                await session.scalars(
                    select(ToolExecutionAttempt)
                    .where(
                        ToolExecutionAttempt.tool_call_id.in_(ambiguous_ids),
                        ToolExecutionAttempt.status == "dispatched",
                    )
                    .execution_options(populate_existing=True)
                )
            )
            for attempt in dispatched_attempts:
                attempt.status = "ambiguous"
                attempt.error = error_code
                attempt.finished_at = now
            response = (
                "外部写入已经发出，但平台结果暂时无法确认。"
                "为避免重复操作，本次执行已停止，请人工核对平台状态后再决定下一步。"
            )
            output_snapshot = {
                "status": "stopped",
                "task_id": task.id,
                "artifact_id": None,
                "artifact_type": "account_inspection_report",
                "report": {},
                "response": response,
                "error_code": error_code,
            }
            await SkillRuntime._close_skill_state(
                session,
                thread=await session.get(ConversationThread, turn.thread_id),
                turn=turn,
                run=run,
                task=task,
                skill_run=skill_run,
                status="stopped",
                response=response,
                output_snapshot=output_snapshot,
                error_code=error_code,
                prelocked=runtime_lock,
            )
            return True

        tool_calls = list(
            await session.scalars(
                select(AgentToolCall)
                .where(
                    AgentToolCall.skill_run_id == skill_run.id,
                    AgentToolCall.status.in_({"planned", "running"}),
                )
                .execution_options(populate_existing=True)
            )
        )
        invocations = list(
            await session.scalars(
                select(AgentInvocation)
                .where(
                    AgentInvocation.skill_run_id == skill_run.id,
                    AgentInvocation.status.in_(
                        {
                            AgentInvocationStatus.QUEUED,
                            AgentInvocationStatus.RUNNING,
                        }
                    ),
                )
                .execution_options(populate_existing=True)
            )
        )
        if not tool_calls and not invocations:
            return False

        now = datetime.now(UTC)
        error_code = "SKILL_EXECUTION_INTERRUPTED"
        response = (
            "账号体检执行被中断。为避免重复调用状态不明的工具或专家，"
            "本次执行已安全关闭，请重新发起一次新的体检。"
        )
        for tool_call in tool_calls:
            tool_call.status = "failed"
            tool_call.error = error_code
            tool_call.finished_at = now
        for invocation in invocations:
            invocation.status = AgentInvocationStatus.FAILED
            invocation.failure_reason = error_code
            invocation.finished_at = now
        output_snapshot = {
            "status": "failed",
            "task_id": task.id,
            "artifact_id": None,
            "artifact_type": "account_inspection_report",
            "report": {},
            "response": response,
            "error_code": error_code,
        }
        run.heartbeat_at = now
        await SkillRuntime._close_skill_state(
            session,
            thread=await session.get(ConversationThread, turn.thread_id),
            turn=turn,
            run=run,
            task=task,
            skill_run=skill_run,
            status="failed",
            response=response,
            output_snapshot=output_snapshot,
            error_code=error_code,
            prelocked=runtime_lock,
        )
        return True

    @staticmethod
    async def _attach_expert_provenance(
        session: AsyncSession,
        *,
        result: Any,
        thread: ConversationThread,
        turn: ConversationTurn,
        run: AgentRun,
        skill_run: SkillRun,
    ) -> None:
        invocation = result.invocation
        for name, value in (
            ("skill_run_id", skill_run.id),
            ("thread_id", thread.id),
            ("turn_id", turn.id),
            ("run_id", run.id),
        ):
            if hasattr(invocation, name):
                setattr(invocation, name, value)
        await session.commit()

    @staticmethod
    def _require_formal_producer(
        invocation: AgentInvocation,
        *,
        scope: RuntimeScope,
        definition: SkillDefinition,
    ) -> None:
        agent_code = (
            invocation.agent_code.value
            if isinstance(invocation.agent_code, AgentCode)
            else str(invocation.agent_code)
        )
        if (
            invocation.status is not AgentInvocationStatus.DONE
            or agent_code == AgentCode.DECISION.value
            or agent_code not in definition.expert_codes
            or invocation.run_id != scope.run_id
            or invocation.skill_run_id != scope.skill_run_id
            or invocation.thread_id != scope.thread_id
            or invocation.turn_id != scope.turn_id
        ):
            raise SkillRecoveryConflict("SKILL_FORMAL_PRODUCER_INVALID")

    @staticmethod
    async def _complete_for_human_review(
        session: AsyncSession,
        *,
        skill_run: SkillRun,
        task: BrainTask,
        content: ContentItem,
        thread: ConversationThread,
        turn: ConversationTurn,
        run: AgentRun,
        report: AccountInspectionReport,
        scope: RuntimeScope,
        producer: AgentInvocation,
        attempt: int,
    ) -> SkillExecutionResult:
        deliverable = await write_runtime_deliverable(
            session,
            scope=scope,
            content=content,
            agent_code=producer.agent_code.value,
            deliverable_type=DeliverableType.REVIEW_REPORT,
            status=DeliverableStatus.PENDING_REVIEW,
            payload=_review_report_payload(report),
            note=(
                "business_artifact_type=account_inspection_report; "
                f"producer_invocation_id={producer.id}; "
                "critic below auto-pass threshold; requires human review"
            ),
        )
        quality = await session.scalar(
            select(AgentQualityScore)
            .where(AgentQualityScore.skill_run_id == skill_run.id)
            .order_by(AgentQualityScore.iteration.desc())
        )
        if quality is not None:
            quality.deliverable_id = deliverable.id

        skill_run.quality_score = Decimal(str(report.critic.score / 100))
        output_snapshot = {
            "status": "needs_review",
            "task_id": task.id,
            "artifact_id": deliverable.id,
            "artifact_type": "account_inspection_report",
            "report": report.model_dump(mode="json"),
            "response": (
                f"账号体检报告已生成，质量审核 {report.critic.score} 分，"
                "未达到自动通过标准，请人工确认后采用。"
            ),
        }
        await _complete_skill_stage(
            session,
            scope=scope,
            step_code="prepare_deliverable",
            attempt=attempt,
            commit=False,
        )
        await SkillRuntime._close_skill_state(
            session,
            thread=thread,
            turn=turn,
            run=run,
            task=task,
            skill_run=skill_run,
            status="completed",
            response=output_snapshot["response"],
            output_snapshot=output_snapshot,
            skill_status="needs_review",
        )
        return SkillRuntime._existing_result(skill_run)

    @staticmethod
    async def _pause_for_tool(
        session: AsyncSession,
        *,
        thread: ConversationThread,
        turn: ConversationTurn,
        run: AgentRun,
        skill_run: SkillRun,
        task: BrainTask,
        status: str,
        skill_name: str = "账号体检",
        artifact_type: str = "account_inspection_report",
    ) -> SkillExecutionResult:
        paused_status = {
            "waiting_approval": "waiting_permission",
            "ambiguous": "stopped",
        }.get(status, "failed")
        error_code = (
            "TOOL_PERMISSION_REQUIRED"
            if paused_status == "waiting_permission"
            else (
                "TOOL_RESULT_AMBIGUOUS" if paused_status == "stopped" else "TOOL_EXECUTION_FAILED"
            )
        )
        response = (
            f"{skill_name}正在等待工具授权。"
            if paused_status.startswith("waiting")
            else f"{skill_name}工具执行失败。"
        )
        if paused_status == "stopped":
            response = (
                "外部写入结果暂时无法确认。为避免重复操作，本次执行已停止，"
                "请人工核对平台状态后再决定下一步。"
            )
        output_snapshot = {
            "status": paused_status,
            "task_id": task.id,
            "artifact_id": None,
            "artifact_type": artifact_type,
            "report": {},
            "response": response,
            "error_code": error_code,
        }
        await SkillRuntime._close_skill_state(
            session,
            thread=thread,
            turn=turn,
            run=run,
            task=task,
            skill_run=skill_run,
            status=paused_status,
            response=response,
            output_snapshot=output_snapshot,
            error_code=error_code,
        )
        return SkillRuntime._existing_result(skill_run)

    @staticmethod
    async def _close_skill_state(
        session: AsyncSession,
        *,
        thread: ConversationThread | None,
        turn: ConversationTurn,
        run: AgentRun,
        task: BrainTask,
        skill_run: SkillRun,
        status: str,
        response: str,
        output_snapshot: dict[str, Any],
        error_code: str | None = None,
        skill_status: str | None = None,
        prelocked: RuntimeRootLock | None = None,
    ) -> None:
        if thread is None:
            raise RuntimeError("Skill execution ConversationThread disappeared")
        artifact_id = output_snapshot.get("artifact_id")
        projections = (
            [
                {
                    "type": "artifact",
                    "artifact_id": artifact_id,
                    "artifact_type": output_snapshot.get("artifact_type"),
                    "skill_run_id": skill_run.id,
                    "account_id": thread.account_id,
                    "report": output_snapshot.get("report") or {},
                }
            ]
            if isinstance(artifact_id, int)
            else (
                [
                    {
                        "type": "execution_blocked",
                        "artifact_type": output_snapshot.get("artifact_type"),
                        "skill_run_id": skill_run.id,
                        "account_id": thread.account_id,
                        "code": error_code,
                    }
                ]
                if error_code is not None
                else []
            )
        )
        await close_runtime_state(
            session,
            scope=RuntimeStateScope(
                run_id=run.id,
                org_id=turn.org_id,
                thread_id=thread.id,
                turn_id=turn.id,
                skill_run_id=skill_run.id,
                task_id=task.id,
                account_id=thread.account_id,
                project_id=thread.project_id,
                content_item_id=task.content_item_id,
                result_payload={
                    "mode": "skill",
                    "status": status,
                    "response": response,
                    "task_id": task.id,
                    "projections": projections,
                    "error_code": error_code,
                },
                skill_output_snapshot=output_snapshot,
                skill_status_override=skill_status,
                nested_skill=_NESTED_PARENT_SKILL_RUN_ID.get() is not None,
            ),
            status=status,
            message=response,
            error_code=error_code,
            prelocked=prelocked,
        )
        stages = _ACTIVE_SKILL_STAGES.get()
        if stages:
            _release_skill_stage(step_code=stages[-1][0], attempt=stages[-1][1])

    @staticmethod
    async def _compatibility_task(
        session: AsyncSession,
        *,
        user: User,
        thread: ConversationThread,
        turn: ConversationTurn,
        run: AgentRun,
        skill_code: str,
        artifact_type: str,
    ) -> tuple[BrainTask, ContentItem]:
        task = await session.get(BrainTask, run.task_id) if run.task_id else None
        if task is None:
            skill_name = skill_registry.get(skill_code).name
            task_type = {
                "account_inspection": BrainTaskType.ACCOUNT_DIAGNOSIS,
                "account_data_analysis": BrainTaskType.REVIEW_OPTIMIZATION,
                "performance_review": BrainTaskType.REVIEW_OPTIMIZATION,
            }.get(skill_code, BrainTaskType.CONTENT_CREATION)
            content = ContentItem(
                project_id=thread.project_id,
                created_by_id=user.id,
                account_id=thread.account_id,
                title=f"{skill_name}：{turn.user_input[:240]}",
                current_stage=ContentStage.OPERATION,
                status=ContentStatus.IN_PROGRESS,
            )
            session.add(content)
            task = BrainTask(
                org_id=user.org_id,
                created_by_id=user.id,
                title=turn.user_input[:300],
                type=task_type,
                status=BrainTaskStatus.RUNNING,
                progress=0,
                current_focus=f"正在执行{skill_name}。",
                runtime_mode="skill",
            )
            task.brief = TaskBrief(
                goal=turn.user_input,
                project_id=thread.project_id,
                account_ids=[thread.account_id],
                platforms=[],
                cycle="current_turn",
                content_goal=turn.user_input,
                risk_constraints=[],
                expected_outputs=[artifact_type],
                confirmation_actions=[],
            )
            session.add(task)
            return task, content
        if task.org_id != user.org_id or task.content_item_id is None:
            raise PermissionError("existing compatibility task is unavailable")
        content_item_id = task.content_item_id
        persisted_content = await session.get(ContentItem, content_item_id)
        if persisted_content is None or persisted_content.account_id != thread.account_id:
            raise PermissionError("compatibility task account scope does not match")
        return task, persisted_content

    @staticmethod
    def _require_scope(
        user: User,
        thread: ConversationThread,
        turn: ConversationTurn,
        run: AgentRun,
    ) -> None:
        if (
            thread.org_id != user.org_id
            or thread.created_by_id != user.id
            or turn.org_id != user.org_id
            or turn.created_by_id != user.id
            or turn.thread_id != thread.id
            or run.org_id != user.org_id
            or run.requested_by_id != user.id
            or run.thread_id != thread.id
            or run.turn_id != turn.id
        ):
            raise PermissionError("Skill execution ownership does not match")

    @staticmethod
    def _require_capability_request_scope(
        *,
        user: User,
        thread: ConversationThread,
        turn: ConversationTurn,
        run: AgentRun,
        capability_request: CapabilityRequest,
    ) -> None:
        if (
            capability_request.org_id != user.org_id
            or capability_request.user_id != user.id
            or capability_request.account_id != thread.account_id
            or capability_request.thread_id != thread.id
            or capability_request.turn_id != turn.id
            or capability_request.run_id != run.id
            or capability_request.message != turn.user_input
        ):
            raise PermissionError("CapabilityRequest scope does not match Skill execution")

    @staticmethod
    def _require_tool_scope(result: dict[str, Any], account_id: int) -> None:
        if result.get("account_id") != account_id:
            raise _ToolScopeMismatch("tool result account scope does not match")

    @staticmethod
    def _existing_result(skill_run: SkillRun) -> SkillExecutionResult:
        output = dict(skill_run.output_snapshot or {})
        response = str(output.get("response") or "")
        if not response and skill_run.status == "running":
            response = "账号体检正在执行中，请稍候。"
        return SkillExecutionResult(
            status=str(output.get("status") or skill_run.status),
            skill_run_id=skill_run.id,
            task_id=skill_run.task_id,
            artifact_id=output.get("artifact_id"),
            artifact_type=str(output.get("artifact_type") or "account_inspection_report"),
            report=dict(output.get("report") or {}),
            response=response,
            error_code=output.get("error_code") or skill_run.error_code,
        )


def _operating_tool_arguments(
    *,
    tool_code: str,
    frozen_input: dict[str, Any],
    content: ContentItem,
    user_input: str,
) -> dict[str, Any]:
    if tool_code == "account.profile":
        return {}
    if tool_code in {"account.data_context", "account.metrics_summary"}:
        return {"days": int(frozen_input.get("days") or 30)}
    if tool_code == "account.engagement_context":
        return {
            "days": int(frozen_input.get("days") or 30),
            "content_item_ids": list(frozen_input.get("content_item_ids") or []),
            "response_scope": str(frozen_input.get("response_scope") or "all"),
        }
    if tool_code == "publish_package_prepare":
        return {
            "content_item_id": int(frozen_input.get("content_item_id") or content.id),
            "title": user_input[:300],
        }
    if tool_code == "platform.content_publish":
        return {
            "approved_publish_artifact_id": int(frozen_input["approved_publish_artifact_id"]),
            "source_artifact_version": int(frozen_input["source_artifact_version"]),
            "scheduled_at": frozen_input.get("scheduled_at"),
            "visibility": str(frozen_input.get("visibility") or "public"),
            "allow_comment": bool(frozen_input.get("allow_comment", True)),
        }
    raise SkillToolPlanError(f"SKILL_TOOL_ARGUMENTS_UNAVAILABLE:{tool_code}")


async def _confirmed_source_artifacts(
    session: AsyncSession,
    *,
    account_id: int,
    artifact_ids: list[int],
) -> list[dict[str, Any]]:
    """Resolve immutable same-account source artifacts and fail closed on any mismatch."""

    unique_ids = list(dict.fromkeys(int(item) for item in artifact_ids))
    if not unique_ids:
        return []
    rows = list(
        await session.execute(
            select(Deliverable, ContentItem.account_id)
            .join(ContentItem, ContentItem.id == Deliverable.content_item_id)
            .where(Deliverable.id.in_(unique_ids))
        )
    )
    by_id = {
        deliverable.id: (deliverable, source_account_id) for deliverable, source_account_id in rows
    }
    if set(by_id) != set(unique_ids):
        raise PermissionError("SOURCE_ARTIFACT_NOT_FOUND")
    resolved: list[dict[str, Any]] = []
    for artifact_id in unique_ids:
        deliverable, source_account_id = by_id[artifact_id]
        if source_account_id != account_id:
            raise PermissionError("SOURCE_ARTIFACT_SCOPE_MISMATCH")
        if deliverable.status is not DeliverableStatus.APPROVED:
            raise PermissionError("SOURCE_ARTIFACT_NOT_APPROVED")
        resolved.append(
            {
                "artifact_id": deliverable.id,
                "artifact_type": deliverable.type.value,
                "version": deliverable.version,
                "payload": dict(deliverable.payload or {}),
            }
        )
    return resolved


def _build_operating_report(
    *,
    definition: SkillDefinition,
    account_id: int,
    platform: str,
    user_input: str,
    frozen_input: dict[str, Any],
    tool_results: dict[str, dict[str, Any]],
    expert_results: list[Any],
    evidence_refs: list[dict[str, Any]],
    source_artifacts: list[dict[str, Any]],
    operation_mode: bool,
    execution_date: date,
) -> tuple[dict[str, Any], DeliverableType, dict[str, Any]]:
    outputs = [dict(item.output or {}) for item in expert_results]
    participants = [str(item.invocation.agent_code) for item in expert_results]
    outputs_by_agent = {
        str(item.invocation.agent_code): dict(item.output or {}) for item in expert_results
    }
    preferred_agent = (
        AgentCode.POSITIONING.value
        if definition.code == "account_positioning"
        else (
            AgentCode.ART_DIRECTOR.value
            if definition.code == "visual_brief_generation"
            else (
                AgentCode.OPERATOR.value
                if definition.code
                in {
                    "performance_review",
                    "publishing_preparation",
                    "content_calendar_planning",
                }
                else (
                    AgentCode.CUSTOMER_SERVICE.value
                    if definition.code == "engagement_review"
                    else AgentCode.CONTENT_DIRECTOR.value
                )
            )
        )
    )
    latest = outputs_by_agent.get(preferred_agent, outputs[-1] if outputs else {})

    def source_payload(artifact_type: str) -> dict[str, Any]:
        return next(
            (
                dict(item.get("payload") or {})
                for item in source_artifacts
                if item.get("artifact_type") == artifact_type
            ),
            {},
        )

    if operation_mode and definition.code == "topic_planning":
        raw_topics = latest.get("topics")
        candidates = raw_topics if isinstance(raw_topics, list) else []
        topics = [
            TopicPlanItem.model_validate(
                {
                    **(dict(item) if isinstance(item, dict) else {"title": str(item)}),
                    "topic_id": (
                        str(item.get("topic_id") or f"topic-{index:02d}")
                        if isinstance(item, dict)
                        else f"topic-{index:02d}"
                    ),
                }
            )
            for index, item in enumerate(candidates, start=1)
        ]
        quality = evaluate_topic_quality(
            topics,
            expected_count=int(frozen_input.get("topic_count") or 5),
        )
        report = TopicPlanningReport(
            account_id=account_id,
            period=f"未来 {int(frozen_input.get('days') or 7)} 天",
            theme=str(latest.get("theme") or user_input[:80]),
            topics=topics,
            posting_notes=_string_list(latest.get("posting_notes")),
            quality=quality,
            evidence_refs=evidence_refs,
            participating_experts=participants,
        )
        data = report.model_dump(mode="json")
        return data, DeliverableType.TOPIC_PLAN, data

    if operation_mode and definition.code == "script_generation":
        topic_payload = source_payload(DeliverableType.TOPIC_PLAN.value)
        expected_topic_ids = [
            str(item.get("topic_id") or "")
            for item in topic_payload.get("topics") or []
            if isinstance(item, dict)
        ]
        raw_scripts = latest.get("scripts")
        candidates = raw_scripts if isinstance(raw_scripts, list) else []
        duration_seconds = int(frozen_input.get("duration_seconds") or 60)
        scripts = [
            FilmingScript.model_validate(
                {
                    **(dict(item) if isinstance(item, dict) else {}),
                    "script_id": (
                        str(item.get("script_id") or f"script-{index:02d}")
                        if isinstance(item, dict)
                        else f"script-{index:02d}"
                    ),
                    "duration_seconds": (
                        item.get("duration_seconds", duration_seconds)
                        if isinstance(item, dict)
                        else duration_seconds
                    ),
                }
            )
            for index, item in enumerate(candidates, start=1)
        ]
        required_constraints: dict[str, list[str]] = {}
        for raw_constraint in frozen_input.get("_server_request_constraints") or []:
            try:
                constraint = json.loads(raw_constraint)
            except (TypeError, json.JSONDecodeError):
                continue
            if constraint.get("constraint_type") != "OFFER_TERMS":
                continue
            requirement = str(constraint.get("raw_requirement") or "").strip()
            indexes = dict(constraint.get("target_scope") or {}).get("item_indexes") or []
            for index in indexes:
                if requirement and type(index) is int and 1 <= index <= len(expected_topic_ids):
                    required_constraints.setdefault(expected_topic_ids[index - 1], []).append(
                        requirement
                    )
        quality = evaluate_script_quality(
            scripts,
            expected_topic_ids=expected_topic_ids,
            required_constraints=required_constraints,
        )
        first = scripts[0] if scripts else None
        report = ScriptGenerationReport(
            account_id=account_id,
            title=first.title if first is not None else "",
            hook=first.hook if first is not None else "",
            scenes=first.shot_list if first is not None else [],
            duration_seconds=(first.duration_seconds if first is not None else duration_seconds),
            presentation_format=frozen_input.get("presentation_format", "storyboard"),
            bgm_suggestion=latest.get("bgm_suggestion"),
            scripts=scripts,
            quality=quality,
            evidence_refs=evidence_refs,
            participating_experts=participants,
        )
        data = report.model_dump(mode="json")
        return data, DeliverableType.VIDEO_SCRIPT, data

    if operation_mode and definition.code == "visual_brief_generation":
        script_payload = source_payload(DeliverableType.VIDEO_SCRIPT.value)
        expected_script_ids = [
            str(item.get("script_id") or "")
            for item in script_payload.get("scripts") or []
            if isinstance(item, dict)
        ]
        raw_visuals = latest.get("visuals")
        candidates = raw_visuals if isinstance(raw_visuals, list) else []
        visuals = [
            VisualProductionItem.model_validate(
                {
                    **(dict(item) if isinstance(item, dict) else {}),
                    "visual_id": (
                        str(item.get("visual_id") or f"visual-{index:02d}")
                        if isinstance(item, dict)
                        else f"visual-{index:02d}"
                    ),
                }
            )
            for index, item in enumerate(candidates, start=1)
        ]
        quality = evaluate_visual_quality(
            visuals,
            expected_script_ids=expected_script_ids,
        )
        first = visuals[0] if visuals else None
        report = VisualBriefGenerationReport(
            account_id=account_id,
            source_artifact_ids=[int(item["artifact_id"]) for item in source_artifacts],
            cover_copy=first.cover_copy if first is not None else "",
            composition=first.composition if first is not None else "",
            shot_list=first.shot_list if first is not None else [],
            asset_checklist=first.asset_checklist if first is not None else [],
            platform_constraints=(first.platform_constraints if first is not None else []),
            visuals=visuals,
            quality=quality,
            evidence_refs=evidence_refs,
            participating_experts=participants,
        )
        data = report.model_dump(mode="json")
        return data, DeliverableType.ART_PROMPT, data

    if operation_mode and definition.code == "content_calendar_planning":
        visual_payload = source_payload(DeliverableType.ART_PROMPT.value)
        visuals = [
            VisualProductionItem.model_validate(item)
            for item in visual_payload.get("visuals") or []
            if isinstance(item, dict)
        ]
        zone = ZoneInfo("Asia/Shanghai")
        slots: list[CalendarSlot] = []
        for index in range(7):
            slot_date = execution_date + timedelta(days=index)
            visual = visuals[index] if index < min(5, len(visuals)) else None
            if visual is None:
                slots.append(
                    CalendarSlot(
                        slot_id=f"slot-{index + 1:02d}",
                        date=slot_date,
                        slot_type="review_buffer",
                        title="复盘与机动安排",
                        owner="运营",
                        readiness="buffer",
                    )
                )
            else:
                slots.append(
                    CalendarSlot(
                        slot_id=f"slot-{index + 1:02d}",
                        date=slot_date,
                        slot_type="publish",
                        title=visual.cover_copy or visual.script_id,
                        owner="运营",
                        readiness="ready",
                        topic_id=visual.topic_id,
                        script_id=visual.script_id,
                        scheduled_at=datetime.combine(
                            slot_date,
                            time(hour=10),
                            tzinfo=zone,
                        ),
                    )
                )
        expected_script_ids = [item.script_id for item in visuals]
        quality = evaluate_calendar_quality(
            slots,
            expected_script_ids=expected_script_ids,
        )
        report = ContentCalendarPlanningReport(
            account_id=account_id,
            source_artifact_ids=[int(item["artifact_id"]) for item in source_artifacts],
            days=7,
            items=[item.model_dump(mode="json") for item in slots],
            slots=slots,
            quality=quality,
            evidence_refs=evidence_refs,
            participating_experts=participants,
        )
        data = report.model_dump(mode="json")
        return data, DeliverableType.PUBLISH_CALENDAR, data

    if definition.code == "account_positioning":
        audience = _string_list(latest.get("audience"))
        pillars = _string_list(latest.get("content_pillars"))
        boundaries = _string_list(latest.get("boundaries"))
        report = AccountPositioningReport(
            account_id=account_id,
            positioning_statement=str(
                latest.get("positioning_statement")
                or "围绕明确受众的真实问题，持续提供可验证、可执行的专业内容。"
            ),
            audience=audience or [str(frozen_input.get("target_audience") or "目标受众待验证")],
            content_pillars=pillars or ["专业知识", "真实案例", "常见问题"],
            tone=str(latest.get("tone") or "专业、清晰、克制"),
            boundaries=boundaries
            or _string_list(frozen_input.get("differentiation_constraints"))
            or ["不虚构案例或数据", "不作无法验证的效果承诺"],
            evidence_refs=evidence_refs,
            participating_experts=participants,
        )
        data = report.model_dump(mode="json")
        return (
            data,
            DeliverableType.POSITIONING_STRATEGY,
            {
                "positioning_statement": data["positioning_statement"],
                "audience": data["audience"],
                "content_pillars": data["content_pillars"],
                "tone": data["tone"],
                "boundaries": data["boundaries"],
                "evidence_refs": data["evidence_refs"],
            },
        )

    if definition.code == "visual_brief_generation":
        visual = VisualProductionItem(
            visual_id="visual-01",
            script_id=str(latest.get("script_id") or "script-01"),
            topic_id=str(latest.get("topic_id") or "topic-01"),
            cover_copy=str(latest.get("cover_copy") or user_input[:80]),
            composition=str(latest.get("composition") or "主体清晰，关键信息位于画面安全区"),
            shot_list=_string_list(latest.get("shot_list")) or ["开场", "主体", "结尾"],
            asset_checklist=_string_list(latest.get("asset_checklist")) or ["主体素材"],
            platform_constraints=_string_list(latest.get("platform_constraints"))
            or ["竖屏 9:16", "字幕保留安全区"],
        )
        report = VisualBriefGenerationReport(
            account_id=account_id,
            source_artifact_ids=[int(item) for item in frozen_input["source_artifact_ids"]],
            cover_copy=visual.cover_copy,
            composition=visual.composition,
            shot_list=visual.shot_list,
            asset_checklist=visual.asset_checklist,
            platform_constraints=visual.platform_constraints,
            visuals=[visual],
            quality=evaluate_visual_quality(
                [visual],
                expected_script_ids=[visual.script_id],
                source_fields_present=all(
                    (
                        str(latest.get("cover_copy") or "").strip(),
                        str(latest.get("composition") or "").strip(),
                        bool(_string_list(latest.get("shot_list"))),
                        bool(_string_list(latest.get("asset_checklist"))),
                        bool(_string_list(latest.get("platform_constraints"))),
                    )
                ),
            ),
            evidence_refs=evidence_refs,
            participating_experts=participants,
        )
        data = report.model_dump(mode="json")
        return data, DeliverableType.ART_PROMPT, data

    if definition.code == "content_calendar_planning":
        items = latest.get("items")
        if not isinstance(items, list) or not items:
            items = [
                {
                    "date": "待确认",
                    "title": user_input[:120],
                    "owner": "运营",
                    "readiness": "needs_input",
                    "dependencies": list(frozen_input["source_artifact_ids"]),
                }
            ]
        today = datetime.now(UTC).date()
        slots = [
            CalendarSlot(
                slot_id=f"slot-{index:02d}",
                date=(
                    str(item.get("date"))
                    if str(item.get("date") or "") != "待确认"
                    else (today + timedelta(days=index - 1)).isoformat()
                ),
                slot_type="publish",
                title=str(item.get("title") or user_input[:120]),
                owner=str(item.get("owner") or "运营"),
                readiness=("ready" if str(item.get("readiness")) == "ready" else "review"),
                topic_id=str(item.get("topic_id") or f"topic-{index:02d}"),
                script_id=str(item.get("script_id") or f"script-{index:02d}"),
                scheduled_at=item.get("scheduled_at"),
            )
            for index, item in enumerate(items, start=1)
        ]
        report = ContentCalendarPlanningReport(
            account_id=account_id,
            source_artifact_ids=[int(item) for item in frozen_input["source_artifact_ids"]],
            days=int(frozen_input.get("days") or 7),
            items=[dict(item) for item in items],
            slots=slots,
            quality=evaluate_calendar_quality(
                slots,
                expected_script_ids=[str(item.script_id) for item in slots],
            ),
            evidence_refs=evidence_refs,
            participating_experts=participants,
        )
        data = report.model_dump(mode="json")
        return data, DeliverableType.PUBLISH_CALENDAR, data

    if definition.code == "script_generation":
        scenes = [str(item).strip() for item in latest.get("scenes", []) if str(item).strip()]
        while len(scenes) < 3:
            scenes.append(
                "主体：用具体案例解释核心观点。"
                if len(scenes) == 1
                else "结尾：总结价值并给出明确互动引导。"
            )
        duration_seconds = int(
            frozen_input.get("duration_seconds") or latest.get("duration_seconds") or 60
        )
        script = FilmingScript(
            script_id=str(latest.get("script_id") or "script-01"),
            topic_id=str(latest.get("topic_id") or "topic-01"),
            title=str(latest.get("title") or user_input[:80]),
            hook=str(latest.get("hook") or "先说结论：这件事最容易踩的坑在这里。"),
            voiceover=str(latest.get("voiceover") or "。".join(scenes)),
            shot_list=_string_list(latest.get("shot_list")) or scenes,
            duration_seconds=duration_seconds,
            cta=str(latest.get("cta") or "留言说说你最关心的问题。"),
            constraints_hit=_string_list(latest.get("constraints_hit")),
        )
        report = ScriptGenerationReport(
            account_id=account_id,
            title=script.title,
            hook=script.hook,
            scenes=scenes,
            duration_seconds=duration_seconds,
            presentation_format=frozen_input.get("presentation_format", "storyboard"),
            bgm_suggestion=latest.get("bgm_suggestion"),
            scripts=[script],
            quality=evaluate_script_quality(
                [script],
                expected_topic_ids=[script.topic_id],
                required_constraints={},
                source_fields_present=all(
                    (
                        str(latest.get("title") or "").strip(),
                        str(latest.get("hook") or "").strip(),
                        str(latest.get("voiceover") or "").strip()
                        or bool(_string_list(latest.get("scenes"))),
                        bool(
                            _string_list(latest.get("shot_list"))
                            or _string_list(latest.get("scenes"))
                        ),
                        str(latest.get("cta") or "").strip(),
                    )
                ),
            ),
            evidence_refs=evidence_refs,
            participating_experts=participants,
        )
        data = report.model_dump(mode="json")
        return (
            data,
            DeliverableType.VIDEO_SCRIPT,
            {
                key: data[key]
                for key in (
                    "title",
                    "hook",
                    "scenes",
                    "duration_seconds",
                    "presentation_format",
                    "bgm_suggestion",
                    "scripts",
                    "quality",
                    "evidence_refs",
                    "participating_experts",
                )
            },
        )

    if definition.code == "topic_planning":
        raw_topics = latest.get("topics")
        if not isinstance(raw_topics, list) or not raw_topics:
            raw_topics = latest.get("scenes")
        source_topics = (
            [item for item in raw_topics if str(item).strip()]
            if isinstance(raw_topics, list)
            else []
        )
        if not source_topics:
            source_topics = [user_input]
        topic_count = int(frozen_input.get("topic_count") or 5)
        topics: list[dict[str, Any]] = []
        for index in range(topic_count):
            source = source_topics[index % len(source_topics)]
            if isinstance(source, dict):
                item = dict(source)
                item.setdefault("topic_id", f"topic-{index + 1:02d}")
                item.setdefault("title", f"选题 {index + 1}")
                item.setdefault("angle", str(latest.get("hook") or "结合账号受众给出具体价值"))
                item.setdefault("format", "short_video")
            else:
                item = {
                    "topic_id": f"topic-{index + 1:02d}",
                    "title": str(source),
                    "angle": str(latest.get("hook") or "结合账号受众给出具体价值"),
                    "format": "short_video",
                }
            topics.append(item)
        parsed_topics = [TopicPlanItem.model_validate(item) for item in topics]
        report = TopicPlanningReport(
            account_id=account_id,
            period=f"未来 {int(frozen_input.get('days') or 7)} 天",
            theme=str(latest.get("theme") or latest.get("title") or user_input[:80]),
            topics=parsed_topics,
            posting_notes=[
                str(item) for item in latest.get("posting_notes", []) if str(item).strip()
            ]
            or ["先小批量发布并根据完播、互动和咨询反馈调整后续选题。"],
            quality=evaluate_topic_quality(parsed_topics, expected_count=topic_count),
            evidence_refs=evidence_refs,
            participating_experts=participants,
        )
        data = report.model_dump(mode="json")
        return (
            data,
            DeliverableType.TOPIC_PLAN,
            {
                "theme": data["theme"],
                "topics": data["topics"],
                "posting_notes": data["posting_notes"],
                "quality": data["quality"],
                "evidence_refs": data["evidence_refs"],
                "participating_experts": data["participating_experts"],
            },
        )

    if definition.code == "performance_review":
        data_context = tool_results.get("account.data_context", {})
        metrics = data_context.get("metrics")
        metrics = dict(metrics) if isinstance(metrics, dict) else {}
        coverage = data_context.get("coverage")
        has_data = bool(metrics) or (
            isinstance(coverage, dict) and any(value == "available" for value in coverage.values())
        )
        highlights = _string_list(latest.get("highlights"))
        issues = _string_list(latest.get("issues"))
        suggestions = _string_list(latest.get("optimization_suggestions"))
        report = PerformanceReviewReport(
            account_id=account_id,
            period=dict(data_context.get("period") or {"days": 30}),
            data_sufficiency="sufficient" if has_data else "insufficient",
            summary=str(
                latest.get("summary")
                or (
                    "已基于当前账号数据完成复盘。"
                    if has_data
                    else "当前数据不足，暂时无法形成可靠的表现判断。"
                )
            ),
            key_metrics=metrics,
            highlights=highlights or ["当前周期暂无可确认的突出表现。"],
            issues=issues or ["需要补充完整内容、互动和粉丝指标。"],
            optimization_suggestions=suggestions
            or ["完成数据同步后，再按内容逐条比较完播、互动和转化表现。"],
            evidence_refs=evidence_refs,
            participating_experts=participants,
        )
        data = report.model_dump(mode="json")
        return (
            data,
            DeliverableType.REVIEW_REPORT,
            {
                "period": _period_label(data["period"]),
                "summary": data["summary"],
                "key_metrics": data["key_metrics"] or {"data_status": "insufficient"},
                "highlights": data["highlights"],
                "issues": data["issues"],
                "optimization_suggestions": data["optimization_suggestions"],
            },
        )

    if (
        operation_mode
        and definition.code == "publishing_preparation"
        and len(source_artifacts) == 4
    ):
        topic_payload = source_payload(DeliverableType.TOPIC_PLAN.value)
        script_payload = source_payload(DeliverableType.VIDEO_SCRIPT.value)
        visual_payload = source_payload(DeliverableType.ART_PROMPT.value)
        calendar_payload = source_payload(DeliverableType.PUBLISH_CALENDAR.value)
        topics = [
            TopicPlanItem.model_validate(item)
            for item in topic_payload.get("topics") or []
            if isinstance(item, dict)
        ]
        scripts = [
            FilmingScript.model_validate(item)
            for item in script_payload.get("scripts") or []
            if isinstance(item, dict)
        ]
        visuals = [
            VisualProductionItem.model_validate(item)
            for item in visual_payload.get("visuals") or []
            if isinstance(item, dict)
        ]
        slots = [
            CalendarSlot.model_validate(item)
            for item in calendar_payload.get("slots") or []
            if isinstance(item, dict)
        ]
        quality = OperationQualityBundle(
            topics=ArtifactQuality.model_validate(topic_payload.get("quality")),
            scripts=ArtifactQuality.model_validate(script_payload.get("quality")),
            visuals=ArtifactQuality.model_validate(visual_payload.get("quality")),
            calendar=ArtifactQuality.model_validate(calendar_payload.get("quality")),
        )
        unique_evidence: dict[str, dict[str, Any]] = {}
        for payload in (topic_payload, script_payload, visual_payload, calendar_payload):
            for item in payload.get("evidence_refs") or []:
                if isinstance(item, dict):
                    key = json.dumps(item, ensure_ascii=False, sort_keys=True)
                    unique_evidence[key] = dict(item)
        experts = list(
            dict.fromkeys(
                participant
                for payload in (
                    topic_payload,
                    script_payload,
                    visual_payload,
                    calendar_payload,
                )
                for participant in payload.get("participating_experts") or []
                if isinstance(participant, str) and participant
            )
        )
        package = WeeklyOperationPackage(
            source_artifacts=[
                OperationArtifactRef(
                    artifact_id=int(item["artifact_id"]),
                    artifact_type=str(item["artifact_type"]),
                    version=int(item["version"]),
                )
                for item in source_artifacts
            ],
            evidence_refs=list(unique_evidence.values()),
            topics=topics,
            scripts=scripts,
            visuals=visuals,
            calendar_slots=slots,
            quality=quality,
            participating_experts=experts,
            manual_publish_checklist=[
                "逐条确认标题、封面、口播和素材完整。",
                "按 7 天安排人工发布五条内容；两个缓冲日用于复盘或调整。",
                "本流程只创建手动发布任务，不会向平台自动发布。",
            ],
            next_steps=[
                {"code": "start_filming", "label": "按 5 条拍摄稿开始拍摄"},
                {
                    "code": "confirm_manual_schedule",
                    "label": "确认 7 天安排并创建手动发布任务",
                },
            ],
        )
        first_date = slots[0].date.isoformat() if slots else "待确认"
        last_date = slots[-1].date.isoformat() if slots else "待确认"
        report = PublishingPreparationReport(
            account_id=account_id,
            platform=platform,
            readiness="ready",
            period=f"{first_date} 至 {last_date}",
            items=[item.model_dump(mode="json") for item in slots],
            operating_notes=["仅创建手动发布任务，不会调用平台发布。"],
            package=package,
            participating_experts=list(dict.fromkeys([*experts, *participants])),
        )
        data = report.model_dump(mode="json")
        return data, DeliverableType.PUBLISH_PACKAGE, data

    if definition.code == "publishing_preparation":
        issues = _string_list(latest.get("issues"))
        suggestions = _string_list(latest.get("optimization_suggestions"))
        readiness = "needs_input" if issues else "ready"
        report = PublishingPreparationReport(
            account_id=account_id,
            platform=platform,
            readiness=readiness,
            period="待确认发布窗口",
            items=[
                {
                    "title": user_input[:120],
                    "status": "待人工确认",
                    "checklist": suggestions
                    or [
                        "确认标题、正文、话题和封面素材。",
                        "确认发布时间、可见范围和评论设置。",
                        "确认账号授权有效后再进入发布审批。",
                    ],
                }
            ],
            operating_notes=issues or ["本 Skill 只完成发布准备，不会直接向平台发布。"],
            participating_experts=participants,
        )
        data = report.model_dump(mode="json")
        return (
            data,
            DeliverableType.PUBLISH_CALENDAR,
            {
                "period": data["period"],
                "items": data["items"],
                "operating_notes": data["operating_notes"],
                "publish_package": dict(
                    tool_results.get("publish_package_prepare", {}).get("publish_package") or {}
                ),
            },
        )

    if definition.code == "engagement_review":
        engagement_context = tool_results.get("account.engagement_context", {})
        report = EngagementReviewReport(
            account_id=account_id,
            period=dict(engagement_context.get("period") or {}),
            status="ready",
            common_questions=_string_list(latest.get("common_questions")),
            sentiment=dict(latest.get("sentiment") or {}),
            response_guidelines=_string_list(latest.get("response_guidelines")),
            content_opportunities=_string_list(latest.get("content_opportunities")),
            evidence_refs=evidence_refs,
            participating_experts=participants,
        )
        data = report.model_dump(mode="json")
        return (
            data,
            DeliverableType.CS_RECORD,
            {
                "common_questions": data["common_questions"],
                "sentiment": data["sentiment"],
                "response_guidelines": data["response_guidelines"],
                "content_opportunities": data["content_opportunities"],
                "evidence_refs": data["evidence_refs"],
            },
        )

    raise KeyError(definition.code)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _period_label(value: dict[str, Any]) -> str:
    days = value.get("days")
    if isinstance(days, int) and days > 0:
        return f"最近 {days} 天"
    start = value.get("start")
    end = value.get("end")
    if start and end:
        return f"{start} 至 {end}"
    return "当前周期"


def _build_account_analysis_answer(
    *,
    account_id: int,
    question: str,
    tool_result: dict[str, Any],
    expert_output: dict[str, Any],
    critic: _CriticResult,
    critic_iterations: int,
    participating_experts: list[str],
) -> AccountDataAnalysisAnswer:
    answerability = dict(tool_result.get("answerability") or {})
    reasons = [str(item) for item in answerability.get("reasons") or [] if str(item)]
    status = str(answerability.get("status") or "insufficient")
    if status == "insufficient":
        missing = [str(item) for item in answerability.get("missing_metrics") or [] if str(item)]
        missing_label = "、".join(missing) if missing else "所需指标"
        conclusion = f"当前缺少已确认的{missing_label}数据，暂时不能可靠回答这个问题。"
        interpretation: list[str] = []
        recommendations: list[dict[str, Any]] = []
        data_limits = reasons or ["当前周期没有足够的已确认数据"]
        next_action = "补齐并确认对应账号数据后重新分析"
    elif expert_output:
        conclusion = str(expert_output.get("conclusion") or "").strip() or (
            "已完成当前账号数据的事实核对。"
        )
        interpretation = [
            str(item).strip()
            for item in expert_output.get("interpretation") or []
            if str(item).strip()
        ]
        recommendations = [
            dict(item)
            for item in expert_output.get("recommendations") or []
            if isinstance(item, dict)
        ]
        data_limits = list(
            dict.fromkeys(
                [
                    *reasons,
                    *[
                        str(item).strip()
                        for item in expert_output.get("data_limits") or []
                        if str(item).strip()
                    ],
                ]
            )
        )
        next_action = str(expert_output.get("next_action") or "").strip() or (
            "选择一项建议进行短周期验证"
        )
    else:
        conclusion = "已读取当前账号的确定性指标事实，但本轮未生成专业解释。"
        interpretation = []
        recommendations = []
        data_limits = list(
            dict.fromkeys([*reasons, "运营专家本轮未能完成解释，当前仅展示确定性事实"])
        )
        next_action = "稍后重新生成解释，或直接查看关键事实"
    return AccountDataAnalysisAnswer(
        account_id=account_id,
        question=question,
        answerability=answerability,
        conclusion=conclusion,
        key_facts=list(tool_result.get("facts") or []),
        interpretation=interpretation,
        recommendations=recommendations,
        data_limits=data_limits,
        next_action=next_action,
        evidence_refs=list(tool_result.get("evidence_refs") or []),
        participating_experts=participating_experts,
        critic=AccountDataAnalysisCriticOutcome(
            passed=critic.passed,
            score=critic.score,
            iterations=critic_iterations,
            issues=critic.issues,
            suggestions=critic.suggestions,
        ),
    )


def validate_account_analysis_grounding(
    answer: AccountDataAnalysisAnswer,
    tool_result: dict[str, Any],
) -> None:
    """Reject expert prose that changes Tool-owned facts or claim strength."""

    serialized = answer.model_dump(mode="json")
    if serialized["key_facts"] != list(tool_result.get("facts") or []):
        raise ValueError("account analysis key facts differ from deterministic tool facts")
    if serialized["evidence_refs"] != list(tool_result.get("evidence_refs") or []):
        raise ValueError("account analysis evidence refs differ from deterministic tool refs")

    statements = [answer.conclusion, *answer.interpretation]
    if any(
        causal_term in statement
        for statement in statements
        for causal_term in ("导致", "造成", "证明了", "必然引起", "直接带来")
    ):
        raise ValueError("unsupported causal claim in account analysis")

    allowed_numbers: set[float] = set()
    for fact in answer.key_facts:
        for value in (
            fact.current_value,
            fact.previous_value,
            fact.absolute_change,
            fact.relative_change,
        ):
            if value is None:
                continue
            number = float(value)
            allowed_numbers.add(round(abs(number), 6))
            if abs(number) <= 1:
                allowed_numbers.add(round(abs(number * 100), 6))
        allowed_numbers.add(float(fact.current_period.days))
        if fact.comparison_period is not None:
            allowed_numbers.add(float(fact.comparison_period.days))

    for statement in statements:
        for raw_number in re.findall(r"-?\d+(?:\.\d+)?", statement):
            number = round(abs(float(raw_number)), 6)
            if number not in allowed_numbers:
                raise ValueError(f"unsupported numeric claim in account analysis: {raw_number}")
        for fact in answer.key_facts:
            if fact.metric_code not in statement and fact.label not in statement:
                continue
            if fact.direction == "down" and any(
                term in statement for term in ("上升", "增长", "提高", "增加")
            ):
                raise ValueError("reversed metric direction claim in account analysis")
            if fact.direction == "up" and any(
                term in statement for term in ("下降", "减少", "降低", "下滑")
            ):
                raise ValueError("reversed metric direction claim in account analysis")


def _build_report(
    *,
    account_id: int,
    days: int,
    data_context: dict[str, Any],
    expert_results: list[Any],
    evidence_refs: list[dict[str, Any]],
    critic: _CriticResult,
    critic_iterations: int,
) -> AccountInspectionReport:
    metrics: list[AccountInspectionMetric] = []
    for name, item in (data_context.get("metrics") or {}).items():
        if not isinstance(item, dict) or item.get("value") is None:
            continue
        value = item["value"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        metrics.append(
            AccountInspectionMetric(
                name=str(name),
                value=value,
                evidence_refs=list(item.get("evidence_refs") or []),
            )
        )
    snapshot_count = int(data_context.get("content_snapshot_count") or 0)
    missing_data: list[str] = []
    if snapshot_count == 0:
        missing_data.append("缺少已确认的内容表现快照")
    if not metrics:
        missing_data.append("缺少可核验的账号核心指标")
    sufficiency: DataSufficiency = (
        "insufficient"
        if not metrics or snapshot_count == 0
        else ("partial" if len(metrics) < 3 else "sufficient")
    )
    summaries = [
        str(getattr(item.invocation, "output_summary", "") or "").strip() for item in expert_results
    ]
    findings = [item for item in summaries if item]
    operator_payload = next(
        (
            dict(item.output)
            for item in reversed(expert_results)
            if isinstance(getattr(item, "output", None), dict)
            and isinstance(item.output.get("optimization_suggestions"), list)
        ),
        {},
    )
    if sufficiency == "insufficient":
        findings = ["当前只能确认数据缺口，尚不能形成账号表现或内容方向结论。"]
    if sufficiency == "insufficient":
        summary = "现有数据不足，无法形成可靠的表现结论；本报告先列出缺失数据和补数动作。"
        recommendations = ["先补齐账号指标和内容表现快照，再进行趋势与内容诊断。"]
        next_action = "补齐并确认最近30天账号及内容数据"
    else:
        summary = str(operator_payload.get("summary") or "").strip() or (
            "已基于所选账号的可核验证据完成账号体检。"
        )
        operator_findings = [
            str(item).strip()
            for key in ("highlights", "issues")
            for item in (operator_payload.get(key) or [])
            if str(item).strip()
        ]
        if operator_findings:
            findings = operator_findings
        recommendations = [
            str(item).strip()
            for item in (operator_payload.get("optimization_suggestions") or [])
            if str(item).strip()
        ] or (findings[-2:] or ["围绕已有证据继续验证内容方向。"])
        next_action = "确认体检结论并选择一项优化建议进入执行"
    period = dict(data_context.get("period") or {"days": days})
    period.setdefault("days", days)
    return AccountInspectionReport(
        account_id=account_id,
        period=period,
        data_sufficiency=sufficiency,
        missing_data=missing_data,
        summary=summary,
        key_metrics=metrics,
        findings=findings,
        recommendations=recommendations,
        next_action=next_action,
        evidence_refs=evidence_refs,
        participating_experts=[
            AgentCode.POSITIONING.value,
            AgentCode.CONTENT_DIRECTOR.value,
            AgentCode.OPERATOR.value,
        ],
        critic=AccountInspectionCriticOutcome(
            passed=critic.passed,
            score=critic.score,
            iterations=critic_iterations,
            issues=critic.issues,
            suggestions=critic.suggestions,
        ),
    )


def _review_report_payload(report: AccountInspectionReport) -> dict[str, Any]:
    data = report.model_dump(mode="json")
    data.pop("recommendations", None)
    return {
        **data,
        "period": f"最近{report.period.get('days', 30)}天",
        "key_metrics": {item.name: item.value for item in report.key_metrics},
        "highlights": report.findings or ["当前没有足够数据形成表现亮点结论"],
        "issues": report.missing_data or report.critic.issues or ["未发现明确异常"],
        "optimization_suggestions": report.recommendations,
    }


def _evidence_refs(data_context: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for item in (data_context.get("metrics") or {}).values():
        if not isinstance(item, dict):
            continue
        for ref in item.get("evidence_refs") or []:
            if not isinstance(ref, dict):
                continue
            kind = str(ref.get("kind") or "")
            identifier = ref.get("id")
            if not kind or not isinstance(identifier, int):
                continue
            key = (kind, identifier)
            if key not in seen:
                seen.add(key)
                refs.append({"kind": kind, "id": identifier})
    for source in data_context.get("sources") or []:
        if not isinstance(source, dict):
            continue
        identifier = source.get("batch_id")
        if isinstance(identifier, int):
            key = ("data_import_batch", identifier)
            if key not in seen:
                seen.add(key)
                refs.append({"kind": key[0], "id": key[1]})
    return refs


def _operation_evidence_sources(
    data_context: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    refs: list[dict[str, Any]] = []
    available_domains: set[str] = set()
    seen_batches: set[int] = set()
    for source in data_context.get("sources") or []:
        if not isinstance(source, dict):
            continue
        batch_id = source.get("batch_id")
        domains = sorted(
            {
                str(domain)
                for domain in source.get("data_domains") or []
                if isinstance(domain, str) and domain
            }
        )
        if not isinstance(batch_id, int) or not domains or batch_id in seen_batches:
            continue
        seen_batches.add(batch_id)
        available_domains.update(domains)
        refs.append(
            {
                "kind": "data_import_batch",
                "id": batch_id,
                "data_domains": domains,
            }
        )
    missing: list[str] = []
    if not available_domains.intersection({"account_metrics", "content_metrics"}):
        missing.append("account_or_content_data")
    if "benchmarks" not in available_domains:
        missing.append("benchmarks")
    return refs, missing


def _operation_evidence_domain_label(domain: str) -> str:
    return {
        "account_or_content_data": "账号或内容表现数据",
        "benchmarks": "对标数据",
    }.get(domain, domain)


def _evidence_label(ref: dict[str, Any]) -> str:
    return f"{ref.get('kind')}:{ref.get('id')}"


skill_runtime = SkillRuntime()

__all__ = [
    "SkillExecutionResult",
    "SkillRuntime",
    "skill_runtime",
    "validate_account_analysis_grounding",
]
