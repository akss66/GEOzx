"""Converge Turn, Run, SkillRun, and Task runtime states.

Revision ID: 20260730_0200
Revises: 20260730_0100
Create Date: 2026-07-30 02:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0200"
down_revision: str | None = "20260730_0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TURN_STATUS_SQL = (
    "status IN ("
    "'queued', 'running', 'retry_wait', 'waiting_permission', "
    "'waiting_decision', 'waiting_user', 'completed', 'blocked', "
    "'failed', 'dead_letter', 'cancelled', 'stopped'"
    ")"
)
_RUN_STATUS_SQL = (
    "status IN ("
    "'claimed', 'waiting_predecessor', 'queued', 'running', "
    "'retry_wait', 'waiting_permission', 'waiting_decision', "
    "'waiting_user', 'completed', 'blocked', 'failed', "
    "'dead_letter', 'cancelled', 'stopped'"
    ")"
)
_SKILL_STATUS_SQL = (
    "status IN ("
    "'running', 'retry_wait', 'waiting_permission', 'completed', "
    "'blocked', 'failed', 'cancelled', 'stopped'"
    ")"
)


def upgrade() -> None:
    with op.batch_alter_table("conversation_turns") as batch_op:
        batch_op.add_column(
            sa.Column(
                "status",
                sa.String(length=40),
                nullable=False,
                server_default="queued",
            )
        )
        batch_op.create_index(
            "ix_conversation_turns_status",
            ["status"],
            unique=False,
        )

    op.execute(
        sa.text(
            """
            UPDATE conversation_turns
            SET status = CASE
                WHEN assistant_response IS NOT NULL
                    THEN 'completed'
                ELSE COALESCE(
                    (
                        SELECT CASE
                            WHEN agent_runs.status IN (
                                'claimed', 'waiting_predecessor', 'pending', 'created'
                            ) THEN 'queued'
                            WHEN agent_runs.status IN ('in_progress', 'started')
                                THEN 'running'
                            WHEN agent_runs.status IN (
                                'retry_wait', 'retry_scheduled', 'retrying'
                            ) THEN 'retry_wait'
                            WHEN agent_runs.status IN ('waiting', 'waiting_input')
                                THEN 'waiting_user'
                            WHEN agent_runs.status IN (
                                'waiting_approval', 'paused'
                            ) THEN 'waiting_permission'
                            WHEN agent_runs.status IN (
                                'done', 'success', 'succeeded'
                            ) THEN 'completed'
                            WHEN agent_runs.status IN ('canceled', 'aborted')
                                THEN 'cancelled'
                            WHEN agent_runs.status = 'error'
                                THEN 'failed'
                            WHEN agent_runs.status IN (
                                'queued', 'running', 'waiting_permission',
                                'waiting_decision', 'waiting_user', 'completed',
                                'blocked', 'failed', 'dead_letter', 'cancelled',
                                'stopped'
                            ) THEN agent_runs.status
                            ELSE 'queued'
                        END
                        FROM agent_runs
                        WHERE agent_runs.turn_id = conversation_turns.id
                        ORDER BY agent_runs.id DESC
                        LIMIT 1
                    ),
                    'queued'
                )
            END
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE agent_runs
            SET status = CASE
                WHEN status IN (
                    'claimed', 'waiting_predecessor', 'queued', 'running',
                    'retry_wait', 'waiting_permission', 'waiting_decision',
                    'waiting_user', 'completed', 'blocked', 'failed',
                    'dead_letter', 'cancelled', 'stopped'
                ) THEN status
                WHEN status IN ('pending', 'created') THEN 'queued'
                WHEN status IN ('in_progress', 'started') THEN 'running'
                WHEN status IN ('retry_scheduled', 'retrying') THEN 'retry_wait'
                WHEN status IN ('waiting', 'waiting_input') THEN 'waiting_user'
                WHEN status IN ('waiting_approval', 'paused') THEN 'waiting_permission'
                WHEN status IN ('done', 'success', 'succeeded') THEN 'completed'
                WHEN status IN ('canceled', 'aborted') THEN 'cancelled'
                ELSE 'failed'
            END
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE skill_runs
            SET status = CASE
                WHEN status IN (
                    'running', 'retry_wait', 'waiting_permission', 'completed',
                    'blocked', 'failed', 'cancelled', 'stopped'
                ) THEN status
                WHEN status IN ('pending', 'created', 'claimed', 'queued') THEN 'running'
                WHEN status IN ('retry_scheduled', 'retrying') THEN 'retry_wait'
                WHEN status IN (
                    'waiting', 'waiting_approval', 'waiting_decision', 'waiting_user',
                    'paused'
                ) THEN 'waiting_permission'
                WHEN status IN ('done', 'success', 'succeeded') THEN 'completed'
                WHEN status IN ('canceled', 'aborted') THEN 'cancelled'
                WHEN status = 'dead_letter' THEN 'failed'
                ELSE 'failed'
            END
            """
        )
    )
    with op.batch_alter_table("conversation_turns") as batch_op:
        batch_op.create_check_constraint(
            "ck_conversation_turns_status",
            _TURN_STATUS_SQL,
        )
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.create_check_constraint(
            "ck_agent_runs_status",
            _RUN_STATUS_SQL,
        )
    with op.batch_alter_table("skill_runs") as batch_op:
        batch_op.create_check_constraint(
            "ck_skill_runs_status",
            _SKILL_STATUS_SQL,
        )


def downgrade() -> None:
    with op.batch_alter_table("skill_runs") as batch_op:
        batch_op.drop_constraint("ck_skill_runs_status", type_="check")
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.drop_constraint("ck_agent_runs_status", type_="check")
    with op.batch_alter_table("conversation_turns") as batch_op:
        batch_op.drop_constraint("ck_conversation_turns_status", type_="check")
        batch_op.drop_index("ix_conversation_turns_status")
        batch_op.drop_column("status")
