"""Freeze persisted Skill input identity for deterministic recovery.

Revision ID: 20260730_0400
Revises: 20260730_0300
Create Date: 2026-07-30 04:00:00.000000
"""

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0400"
down_revision: str | None = "20260730_0300"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIVE = ("running", "retry_wait", "waiting_permission")


def _canonical_snapshot(value: object) -> dict:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise RuntimeError("skill recovery preflight: invalid input_snapshot")
    return value


def _hash(value: dict) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def upgrade() -> None:
    bind = op.get_bind()
    duplicate = bind.execute(
        sa.text(
            """
            SELECT run_id, skill_code
            FROM skill_runs
            WHERE status IN ('running', 'retry_wait', 'waiting_permission')
            GROUP BY run_id, skill_code
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError("skill recovery preflight: ambiguous active SkillRuns")

    op.add_column(
        "skill_runs",
        sa.Column("input_hash", sa.String(length=64), nullable=True),
    )
    rows = bind.execute(sa.text("SELECT id, input_snapshot FROM skill_runs ORDER BY id")).mappings()
    for row in rows:
        snapshot = _canonical_snapshot(row["input_snapshot"])
        bind.execute(
            sa.text("UPDATE skill_runs SET input_hash = :hash WHERE id = :id"),
            {"hash": _hash(snapshot), "id": row["id"]},
        )
    missing = bind.execute(
        sa.text(
            "SELECT id FROM skill_runs WHERE input_hash IS NULL OR length(input_hash) <> 64 LIMIT 1"
        )
    ).first()
    if missing is not None:
        raise RuntimeError("skill recovery preflight: input hash backfill failed")
    op.alter_column(
        "skill_runs",
        "input_hash",
        existing_type=sa.String(length=64),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("skill_runs", "input_hash")
