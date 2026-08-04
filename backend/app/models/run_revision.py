"""Cross-run revision lineage and immutable final stage checkpoint facts."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import BigIntPK, JSONVariant, TimestampMixin

NullableJSONVariant = JSON(none_as_null=True).with_variant(
    JSONB(none_as_null=True),
    "postgresql",
)


class RunRevision(Base, TimestampMixin):
    """The sole durable lineage record between source and revision runs."""

    __tablename__ = "run_revisions"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('partial', 'full_recompute', 'manual_reconciliation')",
            name="ck_run_revisions_mode",
        ),
        CheckConstraint(
            "status IN ('planned', 'waiting_predecessor', 'running', "
            "'completed', 'failed', 'cancelled', 'blocked', 'stopped', "
            "'manual_reconciliation')",
            name="ck_run_revisions_status",
        ),
        CheckConstraint(
            "source_run_id <> revision_run_id",
            name="ck_run_revisions_distinct_runs",
        ),
        CheckConstraint(
            "length(plan_hash) = 64",
            name="ck_run_revisions_plan_hash_length",
        ),
        CheckConstraint(
            "(mode = 'manual_reconciliation' AND "
            "manual_reconciliation_reason IS NOT NULL) OR "
            "(mode <> 'manual_reconciliation' AND "
            "manual_reconciliation_reason IS NULL)",
            name="ck_run_revisions_manual_reason",
        ),
        CheckConstraint(
            "(status = 'running' AND started_at IS NOT NULL AND finished_at IS NULL) OR "
            "(status IN ('completed', 'failed', 'cancelled', 'blocked', 'stopped', "
            "'manual_reconciliation') AND finished_at IS NOT NULL) OR "
            "(status IN ('planned', 'waiting_predecessor') AND "
            "started_at IS NULL AND finished_at IS NULL)",
            name="ck_run_revisions_lifecycle",
        ),
        CheckConstraint(
            "(mode <> 'partial' OR earliest_affected_step IS NOT NULL) AND "
            "(mode <> 'manual_reconciliation' OR fork_checkpoint_id IS NULL)",
            name="ck_run_revisions_partial_plan",
        ),
        UniqueConstraint(
            "revision_run_id",
            name="uq_run_revisions_revision_run",
        ),
        UniqueConstraint(
            "id",
            "org_id",
            "account_id",
            "task_id",
            "thread_id",
            "revision_turn_id",
            "revision_run_id",
            name="uq_run_revisions_id_revision_scope",
        ),
        ForeignKeyConstraint(
            ["account_id", "org_id"],
            ["accounts.id", "accounts.org_id"],
            name="fk_run_revisions_account_org",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["thread_id", "account_id", "org_id"],
            [
                "conversation_threads.id",
                "conversation_threads.account_id",
                "conversation_threads.org_id",
            ],
            name="fk_run_revisions_thread_account_org",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["task_id", "org_id"],
            ["brain_tasks.id", "brain_tasks.org_id"],
            name="fk_run_revisions_task_org",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_turn_id", "thread_id", "org_id"],
            [
                "conversation_turns.id",
                "conversation_turns.thread_id",
                "conversation_turns.org_id",
            ],
            name="fk_run_revisions_source_turn_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["revision_turn_id", "source_turn_id", "thread_id", "org_id"],
            [
                "conversation_turns.id",
                "conversation_turns.target_turn_id",
                "conversation_turns.thread_id",
                "conversation_turns.org_id",
            ],
            name="fk_run_revisions_revision_turn_lineage",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_run_id", "task_id", "thread_id", "source_turn_id", "org_id"],
            [
                "agent_runs.id",
                "agent_runs.task_id",
                "agent_runs.thread_id",
                "agent_runs.turn_id",
                "agent_runs.org_id",
            ],
            name="fk_run_revisions_source_run_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["revision_run_id", "task_id", "thread_id", "revision_turn_id", "org_id"],
            [
                "agent_runs.id",
                "agent_runs.task_id",
                "agent_runs.thread_id",
                "agent_runs.turn_id",
                "agent_runs.org_id",
            ],
            name="fk_run_revisions_revision_run_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            [
                "source_skill_run_id",
                "task_id",
                "source_run_id",
                "thread_id",
                "source_turn_id",
            ],
            [
                "skill_runs.id",
                "skill_runs.task_id",
                "skill_runs.run_id",
                "skill_runs.thread_id",
                "skill_runs.turn_id",
            ],
            name="fk_run_revisions_source_skill_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            [
                "revision_skill_run_id",
                "task_id",
                "revision_run_id",
                "thread_id",
                "revision_turn_id",
            ],
            [
                "skill_runs.id",
                "skill_runs.task_id",
                "skill_runs.run_id",
                "skill_runs.thread_id",
                "skill_runs.turn_id",
            ],
            name="fk_run_revisions_revision_skill_scope",
            ondelete="CASCADE",
        ),
        Index(
            "ix_run_revisions_source_run_created",
            "source_run_id",
            "created_at",
        ),
        Index(
            "ix_run_revisions_scope_status",
            "org_id",
            "account_id",
            "task_id",
            "status",
        ),
        Index(
            "ix_run_revisions_waiting",
            "task_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    account_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    thread_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    task_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    source_turn_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    source_run_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    source_skill_run_id: Mapped[int | None] = mapped_column(BigIntPK, nullable=True)
    revision_turn_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    revision_run_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    revision_skill_run_id: Mapped[int | None] = mapped_column(BigIntPK, nullable=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    dependency_graph_version: Mapped[str] = mapped_column(String(64), nullable=False)
    earliest_affected_step: Mapped[str | None] = mapped_column(String(160), nullable=True)
    changed_constraints: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    direct_affected_steps: Mapped[list] = mapped_column(JSONVariant, nullable=False)
    affected_steps: Mapped[list] = mapped_column(JSONVariant, nullable=False)
    reused_steps: Mapped[list] = mapped_column(JSONVariant, nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_checkpoint_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    fork_checkpoint_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    fallback_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    manual_reconciliation_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SkillStageCheckpoint(Base):
    """Append-only final output fact for one completed or reused stage."""

    __tablename__ = "skill_stage_checkpoints"
    __table_args__ = (
        CheckConstraint(
            "status IN ('completed', 'reused')",
            name="ck_stage_checkpoints_status",
        ),
        CheckConstraint(
            "length(stage_contract_hash) = 64 AND length(input_hash) = 64 AND "
            "length(output_hash) = 64 AND "
            "(data_watermark_hash IS NULL OR length(data_watermark_hash) = 64)",
            name="ck_stage_checkpoints_hash_lengths",
        ),
        CheckConstraint(
            "skill_version > 0 AND stage_revision > 0",
            name="ck_stage_checkpoints_positive_versions",
        ),
        CheckConstraint(
            "reuse_policy IN ('immutable', 'freshness_bound', 'never')",
            name="ck_stage_checkpoints_reuse_policy",
        ),
        CheckConstraint(
            "side_effect_level IN ('none', 'read', 'idempotent_write', 'non_idempotent_write')",
            name="ck_stage_checkpoints_side_effect_level",
        ),
        CheckConstraint(
            "status <> 'completed' OR "
            "(source_stage_checkpoint_id IS NULL AND source_stage_status IS NULL "
            "AND output_snapshot IS NOT NULL)",
            name="ck_stage_checkpoints_completed_shape",
        ),
        CheckConstraint(
            "status <> 'reused' OR "
            "(source_stage_checkpoint_id IS NOT NULL "
            "AND source_stage_status = 'completed' "
            "AND output_snapshot IS NULL "
            "AND run_revision_id IS NOT NULL "
            "AND manual_reconciliation_required = false "
            "AND reuse_policy IN ('immutable', 'freshness_bound') "
            "AND side_effect_level IN ('none', 'read'))",
            name="ck_stage_checkpoints_reused_shape",
        ),
        CheckConstraint(
            "side_effect_level <> 'non_idempotent_write' OR reuse_policy = 'never'",
            name="ck_stage_checkpoints_non_idempotent_never_reuse",
        ),
        CheckConstraint(
            "manual_reconciliation_required = false OR reuse_policy = 'never'",
            name="ck_stage_checkpoints_manual_never_reuse",
        ),
        CheckConstraint(
            "(reuse_policy = 'freshness_bound' "
            "AND data_watermark_hash IS NOT NULL "
            "AND freshness_expires_at IS NOT NULL) OR "
            "(reuse_policy <> 'freshness_bound' "
            "AND data_watermark_hash IS NULL "
            "AND freshness_expires_at IS NULL "
            "AND freshness_validated_at IS NULL)",
            name="ck_stage_checkpoints_freshness_shape",
        ),
        CheckConstraint(
            "status <> 'reused' OR reuse_policy <> 'freshness_bound' OR "
            "(freshness_validated_at IS NOT NULL "
            "AND freshness_validated_at <= freshness_expires_at)",
            name="ck_stage_checkpoints_reuse_freshness",
        ),
        UniqueConstraint(
            "skill_run_id",
            "step_key",
            "stage_revision",
            name="uq_stage_checkpoints_skill_step_revision",
        ),
        UniqueConstraint(
            "id",
            "status",
            "org_id",
            "account_id",
            "task_id",
            "thread_id",
            "step_key",
            "stage_contract_hash",
            "input_hash",
            "output_hash",
            "reuse_policy",
            "side_effect_level",
            "manual_reconciliation_required",
            name="uq_stage_checkpoints_source_compatibility",
        ),
        UniqueConstraint(
            "id",
            "status",
            "reuse_policy",
            "data_watermark_hash",
            "freshness_expires_at",
            name="uq_stage_checkpoints_source_freshness",
        ),
        ForeignKeyConstraint(
            ["account_id", "org_id"],
            ["accounts.id", "accounts.org_id"],
            name="fk_stage_checkpoints_account_org",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["thread_id", "account_id", "org_id"],
            [
                "conversation_threads.id",
                "conversation_threads.account_id",
                "conversation_threads.org_id",
            ],
            name="fk_stage_checkpoints_thread_account_org",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["task_id", "org_id"],
            ["brain_tasks.id", "brain_tasks.org_id"],
            name="fk_stage_checkpoints_task_org",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["turn_id", "thread_id", "org_id"],
            [
                "conversation_turns.id",
                "conversation_turns.thread_id",
                "conversation_turns.org_id",
            ],
            name="fk_stage_checkpoints_turn_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["run_id", "task_id", "thread_id", "turn_id", "org_id"],
            [
                "agent_runs.id",
                "agent_runs.task_id",
                "agent_runs.thread_id",
                "agent_runs.turn_id",
                "agent_runs.org_id",
            ],
            name="fk_stage_checkpoints_run_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["skill_run_id", "task_id", "run_id", "thread_id", "turn_id"],
            [
                "skill_runs.id",
                "skill_runs.task_id",
                "skill_runs.run_id",
                "skill_runs.thread_id",
                "skill_runs.turn_id",
            ],
            name="fk_stage_checkpoints_skill_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            [
                "run_revision_id",
                "org_id",
                "account_id",
                "task_id",
                "thread_id",
                "turn_id",
                "run_id",
            ],
            [
                "run_revisions.id",
                "run_revisions.org_id",
                "run_revisions.account_id",
                "run_revisions.task_id",
                "run_revisions.thread_id",
                "run_revisions.revision_turn_id",
                "run_revisions.revision_run_id",
            ],
            name="fk_stage_checkpoints_run_revision_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            [
                "source_stage_checkpoint_id",
                "source_stage_status",
                "org_id",
                "account_id",
                "task_id",
                "thread_id",
                "step_key",
                "stage_contract_hash",
                "input_hash",
                "output_hash",
                "reuse_policy",
                "side_effect_level",
                "manual_reconciliation_required",
            ],
            [
                "skill_stage_checkpoints.id",
                "skill_stage_checkpoints.status",
                "skill_stage_checkpoints.org_id",
                "skill_stage_checkpoints.account_id",
                "skill_stage_checkpoints.task_id",
                "skill_stage_checkpoints.thread_id",
                "skill_stage_checkpoints.step_key",
                "skill_stage_checkpoints.stage_contract_hash",
                "skill_stage_checkpoints.input_hash",
                "skill_stage_checkpoints.output_hash",
                "skill_stage_checkpoints.reuse_policy",
                "skill_stage_checkpoints.side_effect_level",
                "skill_stage_checkpoints.manual_reconciliation_required",
            ],
            name="fk_stage_checkpoints_source_compatibility",
        ),
        ForeignKeyConstraint(
            [
                "source_stage_checkpoint_id",
                "source_stage_status",
                "reuse_policy",
                "data_watermark_hash",
                "freshness_expires_at",
            ],
            [
                "skill_stage_checkpoints.id",
                "skill_stage_checkpoints.status",
                "skill_stage_checkpoints.reuse_policy",
                "skill_stage_checkpoints.data_watermark_hash",
                "skill_stage_checkpoints.freshness_expires_at",
            ],
            name="fk_stage_checkpoints_source_freshness",
        ),
        Index(
            "ix_stage_checkpoints_revision_status",
            "run_revision_id",
            "status",
        ),
        Index(
            "ix_stage_checkpoints_run_step_finalized",
            "run_id",
            "step_key",
            "finalized_at",
        ),
        Index(
            "ix_stage_checkpoints_reuse_lookup",
            "org_id",
            "account_id",
            "task_id",
            "skill_code",
            "skill_version",
            "dependency_graph_version",
            "step_key",
            "input_hash",
            "status",
            "finalized_at",
        ),
        Index(
            "ix_stage_checkpoints_source",
            "source_stage_checkpoint_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    account_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    thread_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    turn_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    task_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    run_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    skill_run_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    run_revision_id: Mapped[int | None] = mapped_column(BigIntPK, nullable=True)
    step_key: Mapped[str] = mapped_column(String(160), nullable=False)
    stage_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    skill_code: Mapped[str] = mapped_column(String(120), nullable=False)
    skill_version: Mapped[int] = mapped_column(Integer, nullable=False)
    dependency_graph_version: Mapped[str] = mapped_column(String(64), nullable=False)
    stage_contract_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_snapshot: Mapped[dict | None] = mapped_column(NullableJSONVariant, nullable=True)
    output_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_stage_checkpoint_id: Mapped[int | None] = mapped_column(BigIntPK, nullable=True)
    source_stage_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_artifact_refs: Mapped[list] = mapped_column(JSONVariant, nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSONVariant, nullable=False)
    reuse_policy: Mapped[str] = mapped_column(String(24), nullable=False)
    data_watermark_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    freshness_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    freshness_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    side_effect_level: Mapped[str] = mapped_column(String(24), nullable=False)
    manual_reconciliation_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    langgraph_checkpoint_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    finalized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
