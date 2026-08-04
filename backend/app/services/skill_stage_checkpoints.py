"""Transaction-neutral final checkpoint writes and atomic revision reuse barrier."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentToolCall,
    ContentItem,
    ConversationThread,
    Deliverable,
    RunRevision,
    SkillRun,
    SkillStageCheckpoint,
)
from app.orchestrator.checkpoint_graph_contracts import (
    CheckpointGraphContract,
    CheckpointStepSpec,
    require_checkpoint_graph_contract,
)
from app.orchestrator.runtime_scope import RuntimeScope
from app.orchestrator.step_dependencies import build_invalidation_plan
from app.schemas.run_revision import (
    ArtifactRef,
    CheckpointWriteResult,
    CompletedStageDraft,
    EvidenceRef,
    ExpectedStageInputs,
    FreshnessStamp,
    FullRecompute,
    ManualReconciliation,
    PartialExecution,
    ResolvedStageOutput,
    StageDataEnvelope,
    StageReuseBinding,
    _validate_json_column_size,
)
from app.services.checkpoint_freshness import (
    assess_checkpoint_freshness,
    get_freshness_validator,
    load_transaction_db_now,
)
from app.services.checkpoint_hashing import (
    canonical_json_sha256,
    stage_contract_hash,
    stage_input_hash,
    stage_output_hash,
)
from app.services.run_revisions import (
    _canonical_execute_steps,
    _persisted_plan_hash,
    fall_back_to_full_recompute,
    require_manual_reconciliation,
)


class CheckpointServiceConflict(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


async def _validate_skill_scope(session: AsyncSession, scope: RuntimeScope) -> SkillRun:
    if scope.skill_run_id is None:
        raise CheckpointServiceConflict(
            "CHECKPOINT_SCOPE_CONFLICT", "Checkpoint scope requires a SkillRun"
        )
    skill_run = await session.get(SkillRun, scope.skill_run_id)
    thread = await session.get(ConversationThread, scope.thread_id)
    if (
        skill_run is None
        or thread is None
        or skill_run.org_id != scope.org_id
        or skill_run.thread_id != scope.thread_id
        or skill_run.turn_id != scope.turn_id
        or skill_run.run_id != scope.run_id
        or skill_run.task_id != scope.task_id
        or thread.org_id != scope.org_id
        or thread.account_id != scope.account_id
    ):
        raise CheckpointServiceConflict(
            "CHECKPOINT_SCOPE_CONFLICT", "Checkpoint runtime scope does not match"
        )
    return skill_run


def _step(contract: CheckpointGraphContract, step_key: str) -> CheckpointStepSpec:
    for step in contract.steps:
        if step.key == step_key:
            return step
    raise CheckpointServiceConflict(
        "CHECKPOINT_GRAPH_CONTRACT_MISSING", "Checkpoint step is not registered"
    )


def _require_registry_contract(contract: CheckpointGraphContract) -> None:
    try:
        canonical = require_checkpoint_graph_contract(
            contract.skill_code, contract.skill_version
        )
    except (AttributeError, ValueError) as error:
        raise CheckpointServiceConflict(
            "CHECKPOINT_GRAPH_CONTRACT_MISSING", "Checkpoint graph contract is not registered"
        ) from error
    if contract is not canonical:
        raise CheckpointServiceConflict(
            "CHECKPOINT_GRAPH_CONTRACT_MISSING", "Checkpoint graph contract is not canonical"
        )


def _parse_output(row: SkillStageCheckpoint, step: CheckpointStepSpec) -> StageDataEnvelope:
    try:
        output = StageDataEnvelope.model_validate(row.output_snapshot, strict=True)
    except ValidationError as error:
        raise CheckpointServiceConflict(
            "CHECKPOINT_OUTPUT_CORRUPT", "Checkpoint output is not a strict envelope"
        ) from error
    if (
        output.schema_version != step.output_schema_version
        or set(output.data) != set(step.produces_outputs)
        or stage_output_hash(output) != row.output_hash
    ):
        raise CheckpointServiceConflict(
            "CHECKPOINT_OUTPUT_CORRUPT", "Checkpoint output identity does not match"
        )
    return output


async def _side_effect_verdict(
    session: AsyncSession, source_skill_run_id: int
) -> tuple[str, str | None, tuple[int, ...], str | None]:
    calls = tuple(
        (
            await session.scalars(
                select(AgentToolCall)
                .where(AgentToolCall.skill_run_id == source_skill_run_id)
                .order_by(AgentToolCall.id)
                .with_for_update()
            )
        ).all()
    )
    risky_writes = tuple(call for call in calls if call.side_effect_level != "read")
    if risky_writes:
        reason = (
            "external_write_ambiguous"
            if any(call.status in {"dispatched", "ambiguous"} for call in risky_writes)
            else "idempotent_replay_contract_missing"
            if any(call.side_effect_level == "idempotent_write" for call in risky_writes)
            else "non_idempotent_effect_completed"
        )
        level = (
            "non_idempotent_write"
            if any(call.side_effect_level == "non_idempotent_write" for call in risky_writes)
            else "idempotent_write"
        )
        return (
            "manual_reconciliation",
            reason,
            tuple(call.id for call in risky_writes),
            level,
        )
    if any(call.status in {"dispatched", "ambiguous"} for call in calls):
        return "full_recompute", "checkpoint_read_in_flight", (), "read"
    return "reusable", None, (), "read" if calls else None


async def _full_result(
    session: AsyncSession,
    *,
    revision: RunRevision,
    contract: CheckpointGraphContract,
    reason: str,
) -> FullRecompute:
    await fall_back_to_full_recompute(session, revision_id=revision.id, reason=reason)
    return FullRecompute(
        reason=reason,
        execute_steps=tuple(step.key for step in contract.steps),
        plan_hash=revision.plan_hash,
    )


async def prepare_revision_execution(
    session: AsyncSession,
    *,
    revision_scope: RuntimeScope,
    revision_id: int,
    contract: CheckpointGraphContract,
    expected_inputs: ExpectedStageInputs,
) -> PartialExecution | FullRecompute | ManualReconciliation:
    if not isinstance(expected_inputs, ExpectedStageInputs):
        raise TypeError("expected_inputs must be a validated ExpectedStageInputs DTO")
    _require_registry_contract(contract)
    revision_skill = await _validate_skill_scope(session, revision_scope)
    revision = await session.scalar(
        select(RunRevision).where(RunRevision.id == revision_id).with_for_update()
    )
    if (
        revision is None
        or revision.org_id != revision_scope.org_id
        or revision.account_id != revision_scope.account_id
        or revision.thread_id != revision_scope.thread_id
        or revision.task_id != revision_scope.task_id
        or revision.revision_turn_id != revision_scope.turn_id
        or revision.revision_run_id != revision_scope.run_id
        or revision.revision_skill_run_id != revision_scope.skill_run_id
        or revision.dependency_graph_version != contract.graph_version
        or revision_skill.skill_code != contract.skill_code
        or revision_skill.skill_version != contract.skill_version
    ):
        raise CheckpointServiceConflict(
            "CHECKPOINT_SCOPE_CONFLICT", "Revision checkpoint scope does not match"
        )
    if revision.source_skill_run_id is None:
        return await _full_result(
            session,
            revision=revision,
            contract=contract,
            reason="checkpoint_source_skill_missing",
        )
    if revision.mode == "manual_reconciliation":
        return ManualReconciliation(
            reason=revision.manual_reconciliation_reason or "source_checkpoint_manual",
            blocking_receipt_ids=(),
            plan_hash=revision.plan_hash,
        )

    effect, effect_reason, receipt_ids, _effect_level = await _side_effect_verdict(
        session, revision.source_skill_run_id
    )
    if effect == "manual_reconciliation":
        await require_manual_reconciliation(
            session, revision_id=revision.id, reason=effect_reason or "external_write_ambiguous"
        )
        return ManualReconciliation(
            reason=effect_reason or "external_write_ambiguous",
            blocking_receipt_ids=receipt_ids,
            plan_hash=revision.plan_hash,
        )
    source_manual = await session.scalar(
        select(SkillStageCheckpoint.id)
        .where(
            SkillStageCheckpoint.skill_run_id == revision.source_skill_run_id,
            SkillStageCheckpoint.status == "completed",
            SkillStageCheckpoint.manual_reconciliation_required.is_(True),
        )
        .order_by(SkillStageCheckpoint.id)
        .limit(1)
        .with_for_update()
    )
    if source_manual is not None:
        await require_manual_reconciliation(
            session, revision_id=revision.id, reason="source_checkpoint_manual"
        )
        return ManualReconciliation(
            reason="source_checkpoint_manual",
            blocking_receipt_ids=(),
            plan_hash=revision.plan_hash,
        )
    if effect == "full_recompute":
        return await _full_result(
            session,
            revision=revision,
            contract=contract,
            reason=effect_reason or "checkpoint_read_in_flight",
        )
    if revision.mode == "full_recompute":
        return FullRecompute(
            reason=revision.fallback_reason or "dependency_full_recompute",
            execute_steps=tuple(step.key for step in contract.steps),
            plan_hash=revision.plan_hash,
        )
    if revision.mode != "partial":
        raise CheckpointServiceConflict(
            "CHECKPOINT_IMMUTABILITY_CONFLICT", "Revision mode is not executable"
        )

    canonical_invalidation = build_invalidation_plan(
        contract.skill_code, set(revision.changed_constraints)
    )
    expected_execute = tuple(
        step.key
        for step in contract.steps
        if step.key
        in _canonical_execute_steps(
            invalidation=canonical_invalidation, contract=contract
        )
    )
    if tuple(revision.affected_steps) != expected_execute:
        raise CheckpointServiceConflict(
            "CHECKPOINT_IMMUTABILITY_CONFLICT", "Revision execution coverage is not canonical"
        )

    requested_reuse = tuple(
        step.key for step in contract.steps if step.key not in set(revision.affected_steps)
    )
    validated: list[
        tuple[
            CheckpointStepSpec,
            SkillStageCheckpoint,
            StageDataEnvelope,
            StageDataEnvelope,
            tuple[ArtifactRef, ...],
            tuple[EvidenceRef, ...],
            datetime | None,
        ]
    ] = []
    for step_key in requested_reuse:
        step = _step(contract, step_key)
        if step.reuse_policy == "never" or step.side_effect_level not in {"none", "read"}:
            return await _full_result(
                session,
                revision=revision,
                contract=contract,
                reason="checkpoint_contract_mismatch",
            )
        expected = expected_inputs.values.get(step_key)
        if expected is None:
            return await _full_result(
                session,
                revision=revision,
                contract=contract,
                reason="checkpoint_input_projection_missing",
            )
        candidate = await session.scalar(
            select(SkillStageCheckpoint)
            .where(
                SkillStageCheckpoint.org_id == revision.org_id,
                SkillStageCheckpoint.account_id == revision.account_id,
                SkillStageCheckpoint.thread_id == revision.thread_id,
                SkillStageCheckpoint.task_id == revision.task_id,
                SkillStageCheckpoint.turn_id == revision.source_turn_id,
                SkillStageCheckpoint.run_id == revision.source_run_id,
                SkillStageCheckpoint.skill_run_id == revision.source_skill_run_id,
                SkillStageCheckpoint.skill_code == contract.skill_code,
                SkillStageCheckpoint.skill_version == contract.skill_version,
                SkillStageCheckpoint.dependency_graph_version == contract.graph_version,
                SkillStageCheckpoint.step_key == step_key,
                SkillStageCheckpoint.status == "completed",
            )
            .order_by(SkillStageCheckpoint.stage_revision.desc(), SkillStageCheckpoint.id.desc())
            .limit(1)
            .with_for_update()
        )
        if candidate is None:
            return await _full_result(
                session, revision=revision, contract=contract, reason="checkpoint_missing"
            )
        expected_contract_hash = stage_contract_hash(contract=contract, step=step)
        if (
            candidate.source_stage_checkpoint_id is not None
            or candidate.stage_contract_hash != expected_contract_hash
            or candidate.reuse_policy != step.reuse_policy
            or candidate.side_effect_level != step.side_effect_level
            or candidate.manual_reconciliation_required
        ):
            return await _full_result(
                session,
                revision=revision,
                contract=contract,
                reason="checkpoint_contract_mismatch",
            )
        if expected.schema_version != step.input_schema_version or (
            stage_input_hash(expected) != candidate.input_hash
        ):
            return await _full_result(
                session,
                revision=revision,
                contract=contract,
                reason="checkpoint_input_mismatch",
            )
        try:
            source_input = StageDataEnvelope.model_validate(
                candidate.input_snapshot, strict=True
            )
        except ValidationError:
            return await _full_result(
                session,
                revision=revision,
                contract=contract,
                reason="checkpoint_input_mismatch",
            )
        if source_input != expected or stage_input_hash(source_input) != candidate.input_hash:
            return await _full_result(
                session,
                revision=revision,
                contract=contract,
                reason="checkpoint_input_mismatch",
            )
        try:
            output = _parse_output(candidate, step)
        except CheckpointServiceConflict:
            return await _full_result(
                session,
                revision=revision,
                contract=contract,
                reason="checkpoint_output_corrupt",
            )
        try:
            _validate_json_column_size(
                candidate.source_artifact_refs, label="artifact reference array"
            )
            _validate_json_column_size(
                candidate.evidence_refs, label="evidence reference array"
            )
            artifact_refs = tuple(
                ArtifactRef.model_validate(item, strict=True)
                for item in candidate.source_artifact_refs
            )
            evidence_refs = tuple(
                EvidenceRef.model_validate(item, strict=True) for item in candidate.evidence_refs
            )
        except (ValidationError, ValueError):
            return await _full_result(
                session,
                revision=revision,
                contract=contract,
                reason="artifact_hash_mismatch",
            )
        if any(ref.account_id != revision.account_id for ref in artifact_refs) or any(
            ref.account_id != revision.account_id for ref in evidence_refs
        ):
            return await _full_result(
                session,
                revision=revision,
                contract=contract,
                reason="evidence_scope_mismatch",
            )
        try:
            await _verify_artifacts(
                session, account_id=revision.account_id, refs=artifact_refs
            )
        except CheckpointServiceConflict:
            return await _full_result(
                session,
                revision=revision,
                contract=contract,
                reason="artifact_hash_mismatch",
            )
        if evidence_refs:
            return await _full_result(
                session,
                revision=revision,
                contract=contract,
                reason="evidence_scope_mismatch",
            )
        source_stamp = None
        if step.reuse_policy == "freshness_bound":
            policy_key = step.freshness_policy_key
            if policy_key is None:
                return await _full_result(
                    session,
                    revision=revision,
                    contract=contract,
                    reason="freshness_validator_missing",
                )
            expiry = candidate.freshness_expires_at
            if expiry is not None and (expiry.tzinfo is None or expiry.utcoffset() is None):
                expiry = expiry.replace(tzinfo=UTC)
            if candidate.data_watermark_hash is not None and expiry is not None:
                source_stamp = FreshnessStamp(
                    policy_key=policy_key,
                    watermark_hash=candidate.data_watermark_hash,
                    expires_at=expiry,
                )
        freshness = await assess_checkpoint_freshness(
            session,
            scope=revision_scope,
            step=step,
            input=expected,
            source_stamp=source_stamp,
        )
        if freshness.kind != "reusable":
            return await _full_result(
                session,
                revision=revision,
                contract=contract,
                reason=freshness.reason or "freshness_watermark_changed",
            )
        validated.append(
            (
                step,
                candidate,
                expected,
                output,
                artifact_refs,
                evidence_refs,
                freshness.validated_at,
            )
        )

    db_now = await load_transaction_db_now(session)
    bindings: list[StageReuseBinding] = []
    hydrated: dict[str, StageDataEnvelope] = {}
    for step, candidate, expected, output, artifacts, evidence, validated_at in validated:
        existing = await session.scalar(
            select(SkillStageCheckpoint).where(
                SkillStageCheckpoint.skill_run_id == revision_scope.skill_run_id,
                SkillStageCheckpoint.step_key == step.key,
                SkillStageCheckpoint.source_stage_checkpoint_id == candidate.id,
                SkillStageCheckpoint.status == "reused",
            )
        )
        if existing is None:
            next_revision = (
                await session.scalar(
                    select(func.max(SkillStageCheckpoint.stage_revision)).where(
                        SkillStageCheckpoint.skill_run_id == revision_scope.skill_run_id,
                        SkillStageCheckpoint.step_key == step.key,
                    )
                )
                or 0
            ) + 1
            existing = SkillStageCheckpoint(
                org_id=revision_scope.org_id,
                account_id=revision_scope.account_id,
                thread_id=revision_scope.thread_id,
                turn_id=revision_scope.turn_id,
                task_id=revision_scope.task_id,
                run_id=revision_scope.run_id,
                skill_run_id=revision_scope.skill_run_id,
                run_revision_id=revision.id,
                step_key=step.key,
                stage_revision=next_revision,
                status="reused",
                skill_code=contract.skill_code,
                skill_version=contract.skill_version,
                dependency_graph_version=contract.graph_version,
                stage_contract_hash=candidate.stage_contract_hash,
                input_snapshot=expected.model_dump(mode="json"),
                input_hash=candidate.input_hash,
                output_snapshot=None,
                output_hash=candidate.output_hash,
                source_stage_checkpoint_id=candidate.id,
                source_stage_status="completed",
                source_artifact_refs=[item.model_dump(mode="json") for item in artifacts],
                evidence_refs=[item.model_dump(mode="json") for item in evidence],
                reuse_policy=step.reuse_policy,
                data_watermark_hash=candidate.data_watermark_hash,
                freshness_expires_at=candidate.freshness_expires_at,
                freshness_validated_at=validated_at,
                side_effect_level=step.side_effect_level,
                manual_reconciliation_required=False,
                finalized_at=db_now,
            )
            session.add(existing)
            await session.flush()
        elif (
            existing.run_revision_id != revision.id
            or existing.stage_contract_hash != candidate.stage_contract_hash
            or existing.input_hash != candidate.input_hash
            or existing.output_hash != candidate.output_hash
        ):
            raise CheckpointServiceConflict(
                "CHECKPOINT_IMMUTABILITY_CONFLICT", "Reused checkpoint replay differs"
            )
        bindings.append(
            StageReuseBinding(
                step_key=step.key,
                source_checkpoint_id=candidate.id,
                checkpoint_id=existing.id,
                output=output,
            )
        )
        hydrated[step.key] = output
    revision.reused_steps = [binding.step_key for binding in bindings]
    revision.plan_hash = _persisted_plan_hash(revision)
    await session.flush()
    return PartialExecution(
        execute_steps=tuple(revision.affected_steps),
        reused=tuple(bindings),
        hydrated_outputs=hydrated,
        plan_hash=revision.plan_hash,
    )


async def _verify_artifacts(
    session: AsyncSession, *, account_id: int, refs: tuple[ArtifactRef, ...]
) -> None:
    for ref in refs:
        deliverable = await session.get(Deliverable, ref.deliverable_id)
        content = (
            await session.get(ContentItem, deliverable.content_item_id)
            if deliverable is not None
            else None
        )
        if (
            deliverable is None
            or content is None
            or content.account_id != account_id
            or deliverable.type.value != ref.artifact_type
            or deliverable.version != ref.version
            or canonical_json_sha256(domain="artifact-payload/v1", value=deliverable.payload)
            != ref.payload_hash
        ):
            raise CheckpointServiceConflict(
                "CHECKPOINT_ARTIFACT_CONFLICT", "Artifact reference is not durable"
            )


async def record_completed_stage(
    session: AsyncSession,
    *,
    scope: RuntimeScope,
    revision_id: int | None,
    contract: CheckpointGraphContract,
    draft: CompletedStageDraft,
) -> CheckpointWriteResult:
    if not isinstance(draft, CompletedStageDraft):
        raise TypeError("draft must be a validated CompletedStageDraft DTO")
    _require_registry_contract(contract)
    skill_run = await _validate_skill_scope(session, scope)
    if (
        skill_run.skill_code != contract.skill_code
        or skill_run.skill_version != contract.skill_version
    ):
        raise CheckpointServiceConflict(
            "CHECKPOINT_GRAPH_CONTRACT_MISSING", "SkillRun graph contract does not match"
        )
    step = _step(contract, draft.step_key)
    if (
        draft.input.schema_version != step.input_schema_version
        or draft.output.schema_version != step.output_schema_version
        or set(draft.output.data) != set(step.produces_outputs)
    ):
        raise CheckpointServiceConflict(
            "CHECKPOINT_GRAPH_CONTRACT_MISSING", "Stage envelope does not match contract"
        )
    if any(ref.account_id != scope.account_id for ref in draft.artifact_refs) or any(
        ref.account_id != scope.account_id for ref in draft.evidence_refs
    ):
        raise CheckpointServiceConflict(
            "CHECKPOINT_SCOPE_CONFLICT", "Checkpoint reference account does not match"
        )
    await _verify_artifacts(session, account_id=scope.account_id, refs=draft.artifact_refs)
    if draft.evidence_refs:
        raise CheckpointServiceConflict(
            "CHECKPOINT_EVIDENCE_RESOLVER_MISSING", "Evidence resolver is not registered"
        )
    if revision_id is not None:
        revision = await session.get(RunRevision, revision_id)
        if (
            revision is None
            or revision.revision_run_id != scope.run_id
            or revision.revision_turn_id != scope.turn_id
            or revision.revision_skill_run_id != scope.skill_run_id
        ):
            raise CheckpointServiceConflict(
                "CHECKPOINT_SCOPE_CONFLICT", "RunRevision does not match stage scope"
            )
    contract_hash = stage_contract_hash(contract=contract, step=step)
    input_hash = stage_input_hash(draft.input)
    output_hash = stage_output_hash(draft.output)
    effect, effect_reason, _receipt_ids, effect_level = await _side_effect_verdict(
        session, skill_run.id
    )
    if effect == "full_recompute":
        raise CheckpointServiceConflict(
            "CHECKPOINT_WRITE_RACE", effect_reason or "Checkpoint read is not finalized"
        )
    manual_required = effect == "manual_reconciliation"
    effective_reuse_policy = "never" if manual_required else step.reuse_policy
    effective_side_effect_level = effect_level or step.side_effect_level
    artifact_values = [item.model_dump(mode="json") for item in draft.artifact_refs]
    evidence_values = [item.model_dump(mode="json") for item in draft.evidence_refs]
    if draft.langgraph_checkpoint_id is not None:
        semantic_existing = await session.scalar(
            select(SkillStageCheckpoint)
            .where(
                SkillStageCheckpoint.skill_run_id == scope.skill_run_id,
                SkillStageCheckpoint.step_key == step.key,
                SkillStageCheckpoint.langgraph_checkpoint_id
                == draft.langgraph_checkpoint_id,
            )
            .order_by(SkillStageCheckpoint.id)
            .limit(1)
            .with_for_update()
        )
        if semantic_existing is not None:
            if _completed_fact_matches(
                semantic_existing,
                revision_id=revision_id,
                draft=draft,
                contract_hash=contract_hash,
                input_hash=input_hash,
                output_hash=output_hash,
                artifact_values=artifact_values,
                evidence_values=evidence_values,
                reuse_policy=effective_reuse_policy,
                side_effect_level=effective_side_effect_level,
                manual_required=manual_required,
            ):
                return CheckpointWriteResult(
                    checkpoint_id=semantic_existing.id, created=False
                )
            raise CheckpointServiceConflict(
                "CHECKPOINT_IMMUTABILITY_CONFLICT", "Completed checkpoint replay differs"
            )
    existing = await session.scalar(
        select(SkillStageCheckpoint)
        .where(
            SkillStageCheckpoint.skill_run_id == scope.skill_run_id,
            SkillStageCheckpoint.step_key == step.key,
        )
        .order_by(SkillStageCheckpoint.stage_revision.desc())
        .limit(1)
        .with_for_update()
    )
    next_stage_revision = 1
    if existing is not None:
        if _completed_fact_matches(
            existing,
            revision_id=revision_id,
            draft=draft,
            contract_hash=contract_hash,
            input_hash=input_hash,
            output_hash=output_hash,
            artifact_values=artifact_values,
            evidence_values=evidence_values,
            reuse_policy=effective_reuse_policy,
            side_effect_level=effective_side_effect_level,
            manual_required=manual_required,
        ):
            return CheckpointWriteResult(checkpoint_id=existing.id, created=False)
        next_stage_revision = existing.stage_revision + 1
    db_now = await load_transaction_db_now(session)
    watermark_hash = None
    expires_at = None
    if effective_reuse_policy == "freshness_bound":
        validator = get_freshness_validator(step.freshness_policy_key or "")
        if validator is None:
            raise CheckpointServiceConflict(
                "CHECKPOINT_GRAPH_CONTRACT_MISSING", "Freshness validator is missing"
            )
        stamp = await validator.current_stamp(
            session,
            scope=scope,
            step=step,
            input=draft.input,
            db_now=db_now,
        )
        watermark_hash = stamp.watermark_hash
        expires_at = stamp.expires_at
    row = SkillStageCheckpoint(
        org_id=scope.org_id,
        account_id=scope.account_id,
        thread_id=scope.thread_id,
        turn_id=scope.turn_id,
        task_id=scope.task_id,
        run_id=scope.run_id,
        skill_run_id=scope.skill_run_id,
        run_revision_id=revision_id,
        step_key=step.key,
        stage_revision=next_stage_revision,
        status="completed",
        skill_code=contract.skill_code,
        skill_version=contract.skill_version,
        dependency_graph_version=contract.graph_version,
        stage_contract_hash=contract_hash,
        input_snapshot=draft.input.model_dump(mode="json"),
        input_hash=input_hash,
        output_snapshot=draft.output.model_dump(mode="json"),
        output_hash=output_hash,
        source_artifact_refs=artifact_values,
        evidence_refs=evidence_values,
        reuse_policy=effective_reuse_policy,
        data_watermark_hash=watermark_hash,
        freshness_expires_at=expires_at,
        freshness_validated_at=None,
        side_effect_level=effective_side_effect_level,
        manual_reconciliation_required=manual_required,
        langgraph_checkpoint_id=draft.langgraph_checkpoint_id,
        finalized_at=db_now,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        winner = await session.scalar(
            select(SkillStageCheckpoint).where(
                SkillStageCheckpoint.skill_run_id == scope.skill_run_id,
                SkillStageCheckpoint.step_key == step.key,
                SkillStageCheckpoint.stage_revision == next_stage_revision,
            )
        )
        if winner is not None and _completed_fact_matches(
            winner,
            revision_id=revision_id,
            draft=draft,
            contract_hash=contract_hash,
            input_hash=input_hash,
            output_hash=output_hash,
            artifact_values=artifact_values,
            evidence_values=evidence_values,
            reuse_policy=effective_reuse_policy,
            side_effect_level=effective_side_effect_level,
            manual_required=manual_required,
        ):
            return CheckpointWriteResult(checkpoint_id=winner.id, created=False)
        if (
            winner is not None
            and draft.langgraph_checkpoint_id is not None
            and winner.langgraph_checkpoint_id == draft.langgraph_checkpoint_id
        ):
            raise CheckpointServiceConflict(
                "CHECKPOINT_IMMUTABILITY_CONFLICT", "Completed checkpoint replay differs"
            ) from None
        raise CheckpointServiceConflict(
            "CHECKPOINT_WRITE_RACE", "Concurrent completed checkpoint differs"
        ) from None
    return CheckpointWriteResult(checkpoint_id=row.id, created=True)


def _completed_fact_matches(
    row: SkillStageCheckpoint,
    *,
    revision_id: int | None,
    draft: CompletedStageDraft,
    contract_hash: str,
    input_hash: str,
    output_hash: str,
    artifact_values: list[dict],
    evidence_values: list[dict],
    reuse_policy: str,
    side_effect_level: str,
    manual_required: bool,
) -> bool:
    return (
        row.status == "completed"
        and row.run_revision_id == revision_id
        and row.stage_contract_hash == contract_hash
        and row.input_hash == input_hash
        and row.output_hash == output_hash
        and row.input_snapshot == draft.input.model_dump(mode="json")
        and row.output_snapshot == draft.output.model_dump(mode="json")
        and row.source_artifact_refs == artifact_values
        and row.evidence_refs == evidence_values
        and row.reuse_policy == reuse_policy
        and row.side_effect_level == side_effect_level
        and row.manual_reconciliation_required == manual_required
        and row.langgraph_checkpoint_id == draft.langgraph_checkpoint_id
    )


async def load_latest_stage_output(
    session: AsyncSession, *, scope: RuntimeScope, step_key: str
) -> ResolvedStageOutput:
    skill_run = await _validate_skill_scope(session, scope)
    contract = CheckpointGraphContract(
        skill_code=skill_run.skill_code,
        skill_version=skill_run.skill_version,
        graph_version="",
        steps=(),
    )
    from app.orchestrator.checkpoint_graph_contracts import require_checkpoint_graph_contract

    contract = require_checkpoint_graph_contract(contract.skill_code, contract.skill_version)
    step = _step(contract, step_key)
    row = await session.scalar(
        select(SkillStageCheckpoint)
        .where(
            SkillStageCheckpoint.skill_run_id == scope.skill_run_id,
            SkillStageCheckpoint.step_key == step_key,
        )
        .order_by(SkillStageCheckpoint.stage_revision.desc(), SkillStageCheckpoint.id.desc())
        .limit(1)
    )
    if row is None:
        raise CheckpointServiceConflict("CHECKPOINT_OUTPUT_MISSING", "Stage output is missing")
    source_id = None
    checkpoint_id = row.id
    if row.status == "reused":
        source_id = row.source_stage_checkpoint_id
        source = await session.get(SkillStageCheckpoint, source_id)
        if (
            source is None
            or source.status != "completed"
            or source.source_stage_checkpoint_id is not None
            or row.stage_contract_hash != source.stage_contract_hash
            or row.input_hash != source.input_hash
            or row.output_hash != source.output_hash
        ):
            raise CheckpointServiceConflict(
                "CHECKPOINT_OUTPUT_CORRUPT", "Reused checkpoint source is not one-hop completed"
            )
        row = source
    output = _parse_output(row, step)
    return ResolvedStageOutput(
        checkpoint_id=checkpoint_id,
        source_checkpoint_id=source_id,
        output=output,
    )


__all__ = [
    "CheckpointServiceConflict",
    "load_latest_stage_output",
    "prepare_revision_execution",
    "record_completed_stage",
]
