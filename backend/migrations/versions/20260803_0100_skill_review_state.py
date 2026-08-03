"""Add the explicit Skill human-review terminal state.

Revision ID: 20260803_0100
Revises: 20260731_0300
Create Date: 2026-08-03 10:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260803_0100"
down_revision: str | None = "20260731_0300"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WITH_REVIEW = (
    "status IN ("
    "'running', 'retry_wait', 'waiting_permission', 'needs_review', 'completed', "
    "'blocked', 'failed', 'cancelled', 'stopped'"
    ")"
)
_WITHOUT_REVIEW = (
    "status IN ("
    "'running', 'retry_wait', 'waiting_permission', 'completed', "
    "'blocked', 'failed', 'cancelled', 'stopped'"
    ")"
)


def upgrade() -> None:
    with op.batch_alter_table("skill_runs") as batch_op:
        batch_op.drop_constraint("ck_skill_runs_status", type_="check")
        batch_op.create_check_constraint("ck_skill_runs_status", _WITH_REVIEW)


def downgrade() -> None:
    op.execute("UPDATE skill_runs SET status = 'completed' WHERE status = 'needs_review'")
    with op.batch_alter_table("skill_runs") as batch_op:
        batch_op.drop_constraint("ck_skill_runs_status", type_="check")
        batch_op.create_check_constraint("ck_skill_runs_status", _WITHOUT_REVIEW)
