"""Close revision terminals and scope deliverable version streams by agent.

Revision ID: 20260804_0450
Revises: 20260804_0400
Create Date: 2026-08-04 21:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260804_0450"
down_revision: str | None = "20260804_0400"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_TERMINALS = "'completed', 'failed', 'cancelled', 'blocked', 'stopped', 'manual_reconciliation'"
_OLD_TERMINALS = "'completed', 'failed', 'cancelled'"


def _replace_revision_constraints(*, terminal_statuses: str) -> None:
    allowed_statuses = (
        "'planned', 'waiting_predecessor', 'running', " + terminal_statuses
    )
    with op.batch_alter_table("run_revisions") as batch_op:
        batch_op.drop_constraint("ck_run_revisions_lifecycle", type_="check")
        batch_op.drop_constraint("ck_run_revisions_status", type_="check")
        batch_op.create_check_constraint(
            "ck_run_revisions_status",
            f"status IN ({allowed_statuses})",
        )
        batch_op.create_check_constraint(
            "ck_run_revisions_lifecycle",
            "(status = 'running' AND started_at IS NOT NULL AND finished_at IS NULL) OR "
            f"(status IN ({terminal_statuses}) AND finished_at IS NOT NULL) OR "
            "(status IN ('planned', 'waiting_predecessor') AND "
            "started_at IS NULL AND finished_at IS NULL)",
        )


def _require_legacy_version_compatibility() -> None:
    collision = op.get_bind().scalar(
        sa.text(
            "SELECT COUNT(*) FROM ("
            "SELECT 1 FROM deliverables "
            "GROUP BY content_item_id, type, version HAVING COUNT(*) > 1"
            ") AS cross_agent_collisions"
        )
    )
    if collision:
        raise RuntimeError(
            "cannot downgrade deliverable streams: cross-agent version collisions exist"
        )


def upgrade() -> None:
    _replace_revision_constraints(terminal_statuses=_NEW_TERMINALS)
    with op.batch_alter_table("deliverables") as batch_op:
        batch_op.drop_constraint("uq_deliverable_version", type_="unique")
    with op.batch_alter_table("deliverables") as batch_op:
        batch_op.create_unique_constraint(
            "uq_deliverable_version",
            ["content_item_id", "agent_code", "type", "version"],
        )


def downgrade() -> None:
    _require_legacy_version_compatibility()
    with op.batch_alter_table("deliverables") as batch_op:
        batch_op.drop_constraint("uq_deliverable_version", type_="unique")
    with op.batch_alter_table("deliverables") as batch_op:
        batch_op.create_unique_constraint(
            "uq_deliverable_version",
            ["content_item_id", "type", "version"],
        )
    op.execute(
        sa.text(
            "UPDATE run_revisions SET status = 'failed' "
            "WHERE status IN ('blocked', 'stopped', 'manual_reconciliation')"
        )
    )
    _replace_revision_constraints(terminal_statuses=_OLD_TERMINALS)
