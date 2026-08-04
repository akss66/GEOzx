"""Database-enforced lineage for run revisions and final stage checkpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DatabaseError, IntegrityError

from app.models import (
    Account,
    AgentRun,
    BrainTask,
    ConversationThread,
    ConversationTurn,
    Org,
    RunRevision,
    SkillRun,
    SkillStageCheckpoint,
    User,
)
from app.models.enums import (
    AccountStatus,
    BrainTaskStatus,
    BrainTaskType,
    Platform,
    UserRole,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


async def _runtime_scope(
    session,
    admin,
    *,
    suffix: str = "base",
    account: Account | None = None,
    thread: ConversationThread | None = None,
    task: BrainTask | None = None,
) -> SimpleNamespace:
    if account is None:
        account = Account(
            org_id=admin.org_id,
            platform=Platform.DOUYIN,
            nickname=f"checkpoint-{suffix}",
            status=AccountStatus.ACTIVE,
        )
        session.add(account)
        await session.flush()
    if thread is None:
        thread = ConversationThread(
            org_id=admin.org_id,
            created_by_id=admin.id,
            account_id=account.id,
            title=f"checkpoint-{suffix}",
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
    if task is None:
        task = BrainTask(
            org_id=admin.org_id,
            created_by_id=admin.id,
            title=f"checkpoint-{suffix}",
            type=BrainTaskType.CONTENT_CREATION,
            status=BrainTaskStatus.RUNNING,
        )
        session.add(task)
    session.add(revision_turn)
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
        input_hash=HASH_A,
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
        input_hash=HASH_A,
        output_snapshot={},
    )
    session.add_all([source_skill, revision_skill])
    await session.flush()
    return SimpleNamespace(
        account=account,
        thread=thread,
        source_turn=source_turn,
        revision_turn=revision_turn,
        task=task,
        source_run=source_run,
        revision_run=revision_run,
        source_skill=source_skill,
        revision_skill=revision_skill,
    )


def _revision(scope) -> RunRevision:
    return RunRevision(
        org_id=scope.account.org_id,
        account_id=scope.account.id,
        thread_id=scope.thread.id,
        task_id=scope.task.id,
        source_turn_id=scope.source_turn.id,
        source_run_id=scope.source_run.id,
        source_skill_run_id=scope.source_skill.id,
        revision_turn_id=scope.revision_turn.id,
        revision_run_id=scope.revision_run.id,
        revision_skill_run_id=scope.revision_skill.id,
        mode="partial",
        status="planned",
        dependency_graph_version="operation-loop/v1",
        earliest_affected_step="script_generation",
        changed_constraints={"offer_terms": {"operation": "set"}},
        direct_affected_steps=["script_generation"],
        affected_steps=["script_generation", "quality_review"],
        reused_steps=["read_account_data"],
        plan_hash=HASH_A,
    )


def _completed_checkpoint(scope, **overrides) -> SkillStageCheckpoint:
    values = {
        "org_id": scope.account.org_id,
        "account_id": scope.account.id,
        "thread_id": scope.thread.id,
        "turn_id": scope.source_turn.id,
        "task_id": scope.task.id,
        "run_id": scope.source_run.id,
        "skill_run_id": scope.source_skill.id,
        "step_key": "script_generation",
        "stage_revision": 1,
        "status": "completed",
        "skill_code": "operation_iteration",
        "skill_version": 1,
        "dependency_graph_version": "operation-loop/v1",
        "stage_contract_hash": HASH_A,
        "input_snapshot": {"schema_version": 1, "data": {}},
        "input_hash": HASH_B,
        "output_snapshot": {"schema_version": 1, "data": {}},
        "output_hash": HASH_C,
        "source_artifact_refs": [],
        "evidence_refs": [],
        "reuse_policy": "immutable",
        "side_effect_level": "none",
        "manual_reconciliation_required": False,
        "finalized_at": datetime.now(UTC),
    }
    values.update(overrides)
    return SkillStageCheckpoint(**values)


def _reused_checkpoint(scope, revision, source, **overrides) -> SkillStageCheckpoint:
    values = {
        "org_id": scope.account.org_id,
        "account_id": scope.account.id,
        "thread_id": scope.thread.id,
        "turn_id": scope.revision_turn.id,
        "task_id": scope.task.id,
        "run_id": scope.revision_run.id,
        "skill_run_id": scope.revision_skill.id,
        "run_revision_id": revision.id,
        "step_key": source.step_key,
        "stage_revision": 1,
        "status": "reused",
        "skill_code": source.skill_code,
        "skill_version": source.skill_version,
        "dependency_graph_version": source.dependency_graph_version,
        "stage_contract_hash": source.stage_contract_hash,
        "input_snapshot": source.input_snapshot,
        "input_hash": source.input_hash,
        "output_snapshot": None,
        "output_hash": source.output_hash,
        "source_stage_checkpoint_id": source.id,
        "source_stage_status": "completed",
        "source_artifact_refs": source.source_artifact_refs,
        "evidence_refs": source.evidence_refs,
        "reuse_policy": source.reuse_policy,
        "data_watermark_hash": source.data_watermark_hash,
        "freshness_expires_at": source.freshness_expires_at,
        "side_effect_level": source.side_effect_level,
        "manual_reconciliation_required": False,
        "finalized_at": datetime.now(UTC),
    }
    values.update(overrides)
    return SkillStageCheckpoint(**values)


def test_revision_and_checkpoint_metadata_exposes_named_database_contracts() -> None:
    account_uniques = {item.name for item in Account.__table__.constraints}
    thread_uniques = {item.name for item in ConversationThread.__table__.constraints}
    turn_uniques = {item.name for item in ConversationTurn.__table__.constraints}
    revision_constraints = {item.name for item in RunRevision.__table__.constraints}
    revision_foreign_key_deletes = {
        item.name: item.ondelete
        for item in RunRevision.__table__.foreign_key_constraints
    }
    checkpoint_constraints = {item.name for item in SkillStageCheckpoint.__table__.constraints}

    assert "uq_accounts_id_org" in account_uniques
    assert "uq_conversation_thread_id_account_org" in thread_uniques
    assert "uq_conversation_turn_id_target_thread_org" in turn_uniques
    assert {
        "ck_run_revisions_mode",
        "ck_run_revisions_status",
        "ck_run_revisions_distinct_runs",
        "ck_run_revisions_plan_hash_length",
        "ck_run_revisions_manual_reason",
        "ck_run_revisions_lifecycle",
        "ck_run_revisions_partial_plan",
        "uq_run_revisions_revision_run",
        "uq_run_revisions_id_revision_scope",
    } <= revision_constraints
    assert revision_foreign_key_deletes[
        "fk_run_revisions_source_skill_scope"
    ] == "CASCADE"
    assert revision_foreign_key_deletes[
        "fk_run_revisions_revision_skill_scope"
    ] == "CASCADE"
    assert {
        "ck_stage_checkpoints_status",
        "ck_stage_checkpoints_hash_lengths",
        "ck_stage_checkpoints_positive_versions",
        "ck_stage_checkpoints_completed_shape",
        "ck_stage_checkpoints_reused_shape",
        "ck_stage_checkpoints_non_idempotent_never_reuse",
        "ck_stage_checkpoints_manual_never_reuse",
        "ck_stage_checkpoints_freshness_shape",
        "ck_stage_checkpoints_reuse_freshness",
        "uq_stage_checkpoints_source_compatibility",
        "uq_stage_checkpoints_source_freshness",
        "uq_stage_checkpoints_skill_step_revision",
    } <= checkpoint_constraints


@pytest.mark.asyncio
async def test_legal_completed_and_reused_final_facts(session, admin) -> None:
    await session.execute(text("PRAGMA foreign_keys=ON"))
    scope = await _runtime_scope(session, admin)
    revision = _revision(scope)
    session.add(revision)
    await session.flush()
    completed = _completed_checkpoint(scope)
    session.add(completed)
    await session.flush()
    reused = _reused_checkpoint(scope, revision, completed)
    session.add(reused)

    await session.commit()

    assert completed.id is not None
    assert reused.id is not None
    assert reused.source_stage_checkpoint_id == completed.id


@pytest.mark.asyncio
async def test_reused_checkpoint_rejects_cross_account_source(session, admin) -> None:
    await session.execute(text("PRAGMA foreign_keys=ON"))
    source_scope = await _runtime_scope(session, admin, suffix="source-account")
    source = _completed_checkpoint(source_scope)
    session.add(source)
    await session.flush()
    revision_scope = await _runtime_scope(session, admin, suffix="revision-account")
    revision = _revision(revision_scope)
    session.add(revision)
    await session.flush()
    session.add(_reused_checkpoint(revision_scope, revision, source))

    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.parametrize("boundary", ["thread", "task"])
@pytest.mark.asyncio
async def test_reused_checkpoint_rejects_cross_runtime_scope_source(
    session, admin, boundary: str
) -> None:
    await session.execute(text("PRAGMA foreign_keys=ON"))
    source_scope = await _runtime_scope(session, admin, suffix=f"source-{boundary}")
    source = _completed_checkpoint(source_scope)
    session.add(source)
    await session.flush()
    shared = {
        "account": source_scope.account,
        "thread": source_scope.thread,
        "task": source_scope.task,
    }
    shared[boundary] = None
    revision_scope = await _runtime_scope(
        session,
        admin,
        suffix=f"revision-{boundary}",
        **shared,
    )
    revision = _revision(revision_scope)
    session.add(revision)
    await session.flush()
    session.add(_reused_checkpoint(revision_scope, revision, source))

    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_reused_checkpoint_rejects_cross_org_source(session, admin) -> None:
    await session.execute(text("PRAGMA foreign_keys=ON"))
    source_scope = await _runtime_scope(session, admin, suffix="source-org")
    source = _completed_checkpoint(source_scope)
    session.add(source)
    await session.flush()
    other_org = Org(name="other checkpoint org")
    other_admin = User(
        org=other_org,
        email="other-checkpoint-admin@example.com",
        hashed_password="not-used-in-this-test",
        display_name="Other checkpoint admin",
        role=UserRole.ADMIN,
    )
    session.add_all([other_org, other_admin])
    await session.flush()
    revision_scope = await _runtime_scope(session, other_admin, suffix="revision-org")
    revision = _revision(revision_scope)
    session.add(revision)
    await session.flush()
    session.add(_reused_checkpoint(revision_scope, revision, source))

    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "running"),
        ("stage_contract_hash", "short"),
        ("input_hash", "short"),
        ("output_hash", "short"),
        ("skill_version", 0),
        ("stage_revision", 0),
        ("reuse_policy", "unknown"),
        ("side_effect_level", "unknown"),
    ],
)
@pytest.mark.asyncio
async def test_checkpoint_rejects_invalid_final_fact_contract(
    session, admin, field: str, value
) -> None:
    await session.execute(text("PRAGMA foreign_keys=ON"))
    scope = await _runtime_scope(session, admin, suffix=field)
    session.add(_completed_checkpoint(scope, **{field: value}))

    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.parametrize(
    "overrides",
    [
        {"output_snapshot": None},
        {"source_stage_checkpoint_id": 999, "source_stage_status": "completed"},
    ],
)
@pytest.mark.asyncio
async def test_completed_checkpoint_rejects_non_final_shape(
    session, admin, overrides: dict
) -> None:
    await session.execute(text("PRAGMA foreign_keys=ON"))
    scope = await _runtime_scope(session, admin, suffix=str(len(overrides)))
    session.add(_completed_checkpoint(scope, **overrides))

    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_stage_checkpoint_id": None, "source_stage_status": None},
        {"output_snapshot": {"schema_version": 1, "data": {}}},
        {"run_revision_id": None},
        {"source_stage_status": "reused"},
    ],
)
@pytest.mark.asyncio
async def test_reused_checkpoint_rejects_invalid_shape(session, admin, overrides: dict) -> None:
    await session.execute(text("PRAGMA foreign_keys=ON"))
    scope = await _runtime_scope(session, admin, suffix=str(len(overrides)))
    revision = _revision(scope)
    source = _completed_checkpoint(scope)
    session.add_all([revision, source])
    await session.flush()
    session.add(_reused_checkpoint(scope, revision, source, **overrides))

    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("step_key", "other_step"),
        ("stage_contract_hash", HASH_B),
        ("input_hash", HASH_C),
        ("output_hash", HASH_A),
    ],
)
@pytest.mark.asyncio
async def test_reused_checkpoint_rejects_source_contract_or_hash_mismatch(
    session, admin, field: str, value: str
) -> None:
    await session.execute(text("PRAGMA foreign_keys=ON"))
    scope = await _runtime_scope(session, admin, suffix=field)
    revision = _revision(scope)
    source = _completed_checkpoint(scope)
    session.add_all([revision, source])
    await session.flush()
    session.add(_reused_checkpoint(scope, revision, source, **{field: value}))

    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.parametrize(
    "source_overrides",
    [
        {"reuse_policy": "never"},
        {
            "reuse_policy": "never",
            "side_effect_level": "non_idempotent_write",
        },
        {
            "reuse_policy": "never",
            "manual_reconciliation_required": True,
        },
    ],
)
@pytest.mark.asyncio
async def test_unsafe_source_policy_cannot_create_reused_checkpoint(
    session, admin, source_overrides: dict
) -> None:
    await session.execute(text("PRAGMA foreign_keys=ON"))
    scope = await _runtime_scope(session, admin, suffix=str(len(source_overrides)))
    revision = _revision(scope)
    source = _completed_checkpoint(scope, **source_overrides)
    session.add_all([revision, source])
    await session.flush()
    session.add(_reused_checkpoint(scope, revision, source))

    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_reused_checkpoint_rejects_reused_source(session, admin) -> None:
    await session.execute(text("PRAGMA foreign_keys=ON"))
    scope = await _runtime_scope(session, admin, suffix="reused-source")
    revision = _revision(scope)
    completed = _completed_checkpoint(scope)
    session.add_all([revision, completed])
    await session.flush()
    reused = _reused_checkpoint(scope, revision, completed)
    session.add(reused)
    await session.flush()
    session.add(
        _reused_checkpoint(
            scope,
            revision,
            reused,
            stage_revision=2,
            source_stage_status="reused",
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.parametrize("mismatch", ["watermark", "expiry", "expired_validation"])
@pytest.mark.asyncio
async def test_freshness_source_contract_is_fail_closed(session, admin, mismatch: str) -> None:
    await session.execute(text("PRAGMA foreign_keys=ON"))
    scope = await _runtime_scope(session, admin, suffix=mismatch)
    revision = _revision(scope)
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    source = _completed_checkpoint(
        scope,
        reuse_policy="freshness_bound",
        data_watermark_hash=HASH_A,
        freshness_expires_at=expires_at,
    )
    session.add_all([revision, source])
    await session.flush()
    overrides = {"freshness_validated_at": datetime.now(UTC)}
    if mismatch == "watermark":
        overrides["data_watermark_hash"] = HASH_B
    elif mismatch == "expiry":
        overrides["freshness_expires_at"] = expires_at + timedelta(seconds=1)
    else:
        overrides["freshness_validated_at"] = expires_at + timedelta(seconds=1)
    session.add(_reused_checkpoint(scope, revision, source, **overrides))

    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_update_is_immutable_but_parent_thread_cascade_can_delete(session, admin) -> None:
    await session.execute(text("PRAGMA foreign_keys=ON"))
    scope = await _runtime_scope(session, admin, suffix="immutable")
    completed = _completed_checkpoint(scope)
    session.add(completed)
    await session.commit()
    await session.execute(
        text(
            "CREATE TRIGGER trg_test_stage_checkpoint_no_update "
            "BEFORE UPDATE ON skill_stage_checkpoints "
            "BEGIN SELECT RAISE(ABORT, 'skill stage checkpoints are immutable'); END"
        )
    )
    await session.commit()

    with pytest.raises(DatabaseError, match="immutable"):
        await session.execute(
            text("UPDATE skill_stage_checkpoints SET output_hash = :value WHERE id = :id"),
            {"value": HASH_A, "id": completed.id},
        )
    await session.rollback()

    await session.delete(scope.thread)
    await session.commit()
    assert (await session.scalar(select(func.count(SkillStageCheckpoint.id)))) == 0


@pytest.mark.asyncio
async def test_service_order_bulk_delete_removes_revision_runtime_and_turns(
    session, admin
) -> None:
    await session.execute(text("PRAGMA foreign_keys=ON"))
    scope = await _runtime_scope(session, admin, suffix="bulk-turn-delete")
    revision = _revision(scope)
    source = _completed_checkpoint(scope)
    session.add_all([revision, source])
    await session.flush()
    session.add(_reused_checkpoint(scope, revision, source))
    thread_id = scope.thread.id
    await session.commit()

    await session.execute(delete(SkillRun).where(SkillRun.thread_id == thread_id))
    await session.execute(delete(AgentRun).where(AgentRun.thread_id == thread_id))
    result = await session.execute(
        delete(ConversationTurn).where(ConversationTurn.thread_id == thread_id)
    )
    await session.delete(scope.thread)
    await session.commit()

    assert result.rowcount == 2
    assert (
        await session.scalar(
            select(func.count(ConversationTurn.id)).where(
                ConversationTurn.thread_id == thread_id
            )
        )
        == 0
    )
    assert (await session.scalar(select(func.count(RunRevision.id)))) == 0
    assert (await session.scalar(select(func.count(SkillStageCheckpoint.id)))) == 0
