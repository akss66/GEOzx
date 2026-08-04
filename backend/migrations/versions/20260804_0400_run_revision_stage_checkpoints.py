"""Add run revision lineage and immutable final stage checkpoints.

Revision ID: 20260804_0400
Revises: 20260804_0300
Create Date: 2026-08-04 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK, JSONVariant

revision: str = "20260804_0400"
down_revision: str | None = "20260804_0300"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UPDATE_TRIGGER = "trg_skill_stage_checkpoints_no_update"
_UPDATE_FUNCTION = "fn_skill_stage_checkpoints_no_update"


def upgrade() -> None:
    bind = op.get_bind()
    dirty_scope = bind.scalar(
        sa.text(
            "SELECT COUNT(*) FROM conversation_threads AS thread "
            "LEFT JOIN accounts AS account ON account.id = thread.account_id "
            "WHERE account.id IS NULL OR account.org_id <> thread.org_id"
        )
    )
    if dirty_scope:
        raise RuntimeError("conversation thread account/org scope is inconsistent")

    with op.batch_alter_table("accounts") as batch_op:
        batch_op.create_unique_constraint("uq_accounts_id_org", ["id", "org_id"])
    with op.batch_alter_table("conversation_threads") as batch_op:
        batch_op.create_unique_constraint(
            "uq_conversation_thread_id_account_org",
            ["id", "account_id", "org_id"],
        )
    with op.batch_alter_table("conversation_turns") as batch_op:
        batch_op.create_unique_constraint(
            "uq_conversation_turn_id_target_thread_org",
            ["id", "target_turn_id", "thread_id", "org_id"],
        )
    _replace_agent_run_parent_foreign_keys(ondelete="CASCADE")

    _create_run_revisions()
    _create_skill_stage_checkpoints()
    _create_update_trigger()


def downgrade() -> None:
    _drop_update_trigger()
    op.drop_table("skill_stage_checkpoints")
    op.drop_table("run_revisions")
    _replace_agent_run_parent_foreign_keys(ondelete="SET NULL")
    with op.batch_alter_table("conversation_turns") as batch_op:
        batch_op.drop_constraint(
            "uq_conversation_turn_id_target_thread_org",
            type_="unique",
        )
    with op.batch_alter_table("conversation_threads") as batch_op:
        batch_op.drop_constraint(
            "uq_conversation_thread_id_account_org",
            type_="unique",
        )
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_constraint("uq_accounts_id_org", type_="unique")


def _replace_agent_run_parent_foreign_keys(*, ondelete: str) -> None:
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.drop_constraint(
            "fk_agent_runs_turn_id_conversation_turns",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_agent_runs_thread_id_conversation_threads",
            type_="foreignkey",
        )
        batch_op.create_foreign_key(
            "fk_agent_runs_thread_id_conversation_threads",
            "conversation_threads",
            ["thread_id"],
            ["id"],
            ondelete=ondelete,
        )
        batch_op.create_foreign_key(
            "fk_agent_runs_turn_id_conversation_turns",
            "conversation_turns",
            ["turn_id"],
            ["id"],
            ondelete=ondelete,
        )


def _create_run_revisions() -> None:
    op.create_table(
        "run_revisions",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("account_id", BigIntPK, nullable=False),
        sa.Column("thread_id", BigIntPK, nullable=False),
        sa.Column("task_id", BigIntPK, nullable=False),
        sa.Column("source_turn_id", BigIntPK, nullable=False),
        sa.Column("source_run_id", BigIntPK, nullable=False),
        sa.Column("source_skill_run_id", BigIntPK, nullable=True),
        sa.Column("revision_turn_id", BigIntPK, nullable=False),
        sa.Column("revision_run_id", BigIntPK, nullable=False),
        sa.Column("revision_skill_run_id", BigIntPK, nullable=True),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("dependency_graph_version", sa.String(64), nullable=False),
        sa.Column("earliest_affected_step", sa.String(160), nullable=True),
        sa.Column("changed_constraints", JSONVariant, nullable=False),
        sa.Column("direct_affected_steps", JSONVariant, nullable=False),
        sa.Column("affected_steps", JSONVariant, nullable=False),
        sa.Column("reused_steps", JSONVariant, nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("source_checkpoint_id", sa.String(160), nullable=True),
        sa.Column("fork_checkpoint_id", sa.String(160), nullable=True),
        sa.Column("fallback_reason", sa.String(120), nullable=True),
        sa.Column("manual_reconciliation_reason", sa.String(120), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "mode IN ('partial', 'full_recompute', 'manual_reconciliation')",
            name="ck_run_revisions_mode",
        ),
        sa.CheckConstraint(
            "status IN ('planned', 'waiting_predecessor', 'running', "
            "'completed', 'failed', 'cancelled')",
            name="ck_run_revisions_status",
        ),
        sa.CheckConstraint(
            "source_run_id <> revision_run_id",
            name="ck_run_revisions_distinct_runs",
        ),
        sa.CheckConstraint(
            "length(plan_hash) = 64",
            name="ck_run_revisions_plan_hash_length",
        ),
        sa.CheckConstraint(
            "(mode = 'manual_reconciliation' AND "
            "manual_reconciliation_reason IS NOT NULL) OR "
            "(mode <> 'manual_reconciliation' AND "
            "manual_reconciliation_reason IS NULL)",
            name="ck_run_revisions_manual_reason",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND started_at IS NOT NULL AND finished_at IS NULL) OR "
            "(status IN ('completed', 'failed', 'cancelled') AND finished_at IS NOT NULL) OR "
            "(status IN ('planned', 'waiting_predecessor') AND "
            "started_at IS NULL AND finished_at IS NULL)",
            name="ck_run_revisions_lifecycle",
        ),
        sa.CheckConstraint(
            "(mode <> 'partial' OR earliest_affected_step IS NOT NULL) AND "
            "(mode <> 'manual_reconciliation' OR fork_checkpoint_id IS NULL)",
            name="ck_run_revisions_partial_plan",
        ),
        sa.UniqueConstraint(
            "revision_run_id",
            name="uq_run_revisions_revision_run",
        ),
        sa.UniqueConstraint(
            "id",
            "org_id",
            "account_id",
            "task_id",
            "thread_id",
            "revision_turn_id",
            "revision_run_id",
            name="uq_run_revisions_id_revision_scope",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "org_id"],
            ["accounts.id", "accounts.org_id"],
            name="fk_run_revisions_account_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id", "account_id", "org_id"],
            [
                "conversation_threads.id",
                "conversation_threads.account_id",
                "conversation_threads.org_id",
            ],
            name="fk_run_revisions_thread_account_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "org_id"],
            ["brain_tasks.id", "brain_tasks.org_id"],
            name="fk_run_revisions_task_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_turn_id", "thread_id", "org_id"],
            ["conversation_turns.id", "conversation_turns.thread_id", "conversation_turns.org_id"],
            name="fk_run_revisions_source_turn_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
            ["source_skill_run_id", "task_id", "source_run_id", "thread_id", "source_turn_id"],
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
        sa.ForeignKeyConstraint(
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
    )
    op.create_index(
        "ix_run_revisions_source_run_created",
        "run_revisions",
        ["source_run_id", "created_at"],
    )
    op.create_index(
        "ix_run_revisions_scope_status",
        "run_revisions",
        ["org_id", "account_id", "task_id", "status"],
    )
    op.create_index(
        "ix_run_revisions_waiting",
        "run_revisions",
        ["task_id", "status", "created_at"],
    )


def _create_skill_stage_checkpoints() -> None:
    op.create_table(
        "skill_stage_checkpoints",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("account_id", BigIntPK, nullable=False),
        sa.Column("thread_id", BigIntPK, nullable=False),
        sa.Column("turn_id", BigIntPK, nullable=False),
        sa.Column("task_id", BigIntPK, nullable=False),
        sa.Column("run_id", BigIntPK, nullable=False),
        sa.Column("skill_run_id", BigIntPK, nullable=False),
        sa.Column("run_revision_id", BigIntPK, nullable=True),
        sa.Column("step_key", sa.String(160), nullable=False),
        sa.Column("stage_revision", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("skill_code", sa.String(120), nullable=False),
        sa.Column("skill_version", sa.Integer, nullable=False),
        sa.Column("dependency_graph_version", sa.String(64), nullable=False),
        sa.Column("stage_contract_hash", sa.String(64), nullable=False),
        sa.Column("input_snapshot", JSONVariant, nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("output_snapshot", JSONVariant, nullable=True),
        sa.Column("output_hash", sa.String(64), nullable=False),
        sa.Column("source_stage_checkpoint_id", BigIntPK, nullable=True),
        sa.Column("source_stage_status", sa.String(16), nullable=True),
        sa.Column("source_artifact_refs", JSONVariant, nullable=False),
        sa.Column("evidence_refs", JSONVariant, nullable=False),
        sa.Column("reuse_policy", sa.String(24), nullable=False),
        sa.Column("data_watermark_hash", sa.String(64), nullable=True),
        sa.Column("freshness_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("freshness_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("side_effect_level", sa.String(24), nullable=False),
        sa.Column(
            "manual_reconciliation_required",
            sa.Boolean,
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("langgraph_checkpoint_id", sa.String(160), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('completed', 'reused')",
            name="ck_stage_checkpoints_status",
        ),
        sa.CheckConstraint(
            "length(stage_contract_hash) = 64 AND length(input_hash) = 64 AND "
            "length(output_hash) = 64 AND "
            "(data_watermark_hash IS NULL OR length(data_watermark_hash) = 64)",
            name="ck_stage_checkpoints_hash_lengths",
        ),
        sa.CheckConstraint(
            "skill_version > 0 AND stage_revision > 0",
            name="ck_stage_checkpoints_positive_versions",
        ),
        sa.CheckConstraint(
            "reuse_policy IN ('immutable', 'freshness_bound', 'never')",
            name="ck_stage_checkpoints_reuse_policy",
        ),
        sa.CheckConstraint(
            "side_effect_level IN ('none', 'read', 'idempotent_write', 'non_idempotent_write')",
            name="ck_stage_checkpoints_side_effect_level",
        ),
        sa.CheckConstraint(
            "status <> 'completed' OR "
            "(source_stage_checkpoint_id IS NULL AND source_stage_status IS NULL "
            "AND output_snapshot IS NOT NULL)",
            name="ck_stage_checkpoints_completed_shape",
        ),
        sa.CheckConstraint(
            "status <> 'reused' OR "
            "(source_stage_checkpoint_id IS NOT NULL AND source_stage_status = 'completed' "
            "AND output_snapshot IS NULL AND run_revision_id IS NOT NULL "
            "AND manual_reconciliation_required = false "
            "AND reuse_policy IN ('immutable', 'freshness_bound') "
            "AND side_effect_level IN ('none', 'read'))",
            name="ck_stage_checkpoints_reused_shape",
        ),
        sa.CheckConstraint(
            "side_effect_level <> 'non_idempotent_write' OR reuse_policy = 'never'",
            name="ck_stage_checkpoints_non_idempotent_never_reuse",
        ),
        sa.CheckConstraint(
            "manual_reconciliation_required = false OR reuse_policy = 'never'",
            name="ck_stage_checkpoints_manual_never_reuse",
        ),
        sa.CheckConstraint(
            "(reuse_policy = 'freshness_bound' AND data_watermark_hash IS NOT NULL "
            "AND freshness_expires_at IS NOT NULL) OR "
            "(reuse_policy <> 'freshness_bound' AND data_watermark_hash IS NULL "
            "AND freshness_expires_at IS NULL AND freshness_validated_at IS NULL)",
            name="ck_stage_checkpoints_freshness_shape",
        ),
        sa.CheckConstraint(
            "status <> 'reused' OR reuse_policy <> 'freshness_bound' OR "
            "(freshness_validated_at IS NOT NULL "
            "AND freshness_validated_at <= freshness_expires_at)",
            name="ck_stage_checkpoints_reuse_freshness",
        ),
        sa.UniqueConstraint(
            "skill_run_id",
            "step_key",
            "stage_revision",
            name="uq_stage_checkpoints_skill_step_revision",
        ),
        sa.UniqueConstraint(
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
        sa.UniqueConstraint(
            "id",
            "status",
            "reuse_policy",
            "data_watermark_hash",
            "freshness_expires_at",
            name="uq_stage_checkpoints_source_freshness",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "org_id"],
            ["accounts.id", "accounts.org_id"],
            name="fk_stage_checkpoints_account_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id", "account_id", "org_id"],
            [
                "conversation_threads.id",
                "conversation_threads.account_id",
                "conversation_threads.org_id",
            ],
            name="fk_stage_checkpoints_thread_account_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "org_id"],
            ["brain_tasks.id", "brain_tasks.org_id"],
            name="fk_stage_checkpoints_task_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["turn_id", "thread_id", "org_id"],
            ["conversation_turns.id", "conversation_turns.thread_id", "conversation_turns.org_id"],
            name="fk_stage_checkpoints_turn_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
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
    )
    op.create_index(
        "ix_stage_checkpoints_revision_status",
        "skill_stage_checkpoints",
        ["run_revision_id", "status"],
    )
    op.create_index(
        "ix_stage_checkpoints_run_step_finalized",
        "skill_stage_checkpoints",
        ["run_id", "step_key", "finalized_at"],
    )
    op.create_index(
        "ix_stage_checkpoints_reuse_lookup",
        "skill_stage_checkpoints",
        [
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
        ],
    )
    op.create_index(
        "ix_stage_checkpoints_source",
        "skill_stage_checkpoints",
        ["source_stage_checkpoint_id"],
    )


def _create_update_trigger() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                f"CREATE FUNCTION {_UPDATE_FUNCTION}() RETURNS trigger AS $$ "
                "BEGIN RAISE EXCEPTION 'skill stage checkpoints are immutable'; END; "
                "$$ LANGUAGE plpgsql"
            )
        )
        op.execute(
            sa.text(
                f"CREATE TRIGGER {_UPDATE_TRIGGER} BEFORE UPDATE ON skill_stage_checkpoints "
                f"FOR EACH ROW EXECUTE FUNCTION {_UPDATE_FUNCTION}()"
            )
        )
        return
    op.execute(
        sa.text(
            f"CREATE TRIGGER {_UPDATE_TRIGGER} BEFORE UPDATE ON skill_stage_checkpoints "
            "BEGIN SELECT RAISE(ABORT, 'skill stage checkpoints are immutable'); END"
        )
    )


def _drop_update_trigger() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text(f"DROP TRIGGER {_UPDATE_TRIGGER} ON skill_stage_checkpoints"))
        op.execute(sa.text(f"DROP FUNCTION {_UPDATE_FUNCTION}()"))
        return
    op.execute(sa.text(f"DROP TRIGGER {_UPDATE_TRIGGER}"))
