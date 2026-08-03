"""Scope durable runtime events to accounts and ordered conversation turns.

Revision ID: 20260804_0100
Revises: 20260803_0400
Create Date: 2026-08-04 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK

revision: str = "20260804_0100"
down_revision: str | None = "20260803_0400"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TURN_SEQUENCE_PREDICATE = "turn_id IS NOT NULL AND sequence IS NOT NULL"


def _backfill_inferable_event_scope() -> None:
    """Backfill only events whose canonical conversation thread can be resolved."""

    op.execute(
        sa.text(
            """
            UPDATE events
            SET
                org_id = (
                    SELECT threads.org_id
                    FROM conversation_threads AS threads
                    WHERE threads.id = CASE
                        WHEN events.turn_id IS NOT NULL THEN (
                            SELECT turns.thread_id
                            FROM conversation_turns AS turns
                            WHERE turns.id = events.turn_id
                              AND (
                                  events.thread_id IS NULL
                                  OR events.thread_id = turns.thread_id
                              )
                        )
                        ELSE events.thread_id
                    END
                ),
                account_id = (
                    SELECT threads.account_id
                    FROM conversation_threads AS threads
                    WHERE threads.id = CASE
                        WHEN events.turn_id IS NOT NULL THEN (
                            SELECT turns.thread_id
                            FROM conversation_turns AS turns
                            WHERE turns.id = events.turn_id
                              AND (
                                  events.thread_id IS NULL
                                  OR events.thread_id = turns.thread_id
                              )
                        )
                        ELSE events.thread_id
                    END
                )
            WHERE EXISTS (
                SELECT 1
                FROM conversation_threads AS threads
                WHERE threads.id = CASE
                    WHEN events.turn_id IS NOT NULL THEN (
                        SELECT turns.thread_id
                        FROM conversation_turns AS turns
                        WHERE turns.id = events.turn_id
                          AND (
                              events.thread_id IS NULL
                              OR events.thread_id = turns.thread_id
                          )
                    )
                    ELSE events.thread_id
                END
            )
            """
        )
    )


def _backfill_turn_sequences() -> None:
    op.execute(
        sa.text(
            """
            WITH ranked_events AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (PARTITION BY turn_id ORDER BY id) AS sequence
                FROM events
                WHERE turn_id IS NOT NULL
                  AND org_id IS NOT NULL
                  AND account_id IS NOT NULL
            )
            UPDATE events
            SET sequence = (
                SELECT ranked_events.sequence
                FROM ranked_events
                WHERE ranked_events.id = events.id
            )
            WHERE id IN (SELECT id FROM ranked_events)
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE conversation_turns
            SET next_event_sequence = COALESCE(
                (
                    SELECT MAX(events.sequence) + 1
                    FROM events
                    WHERE events.turn_id = conversation_turns.id
                ),
                1
            )
            """
        )
    )


def upgrade() -> None:
    with op.batch_alter_table("conversation_turns") as batch_op:
        batch_op.add_column(
            sa.Column(
                "next_event_sequence",
                sa.Integer(),
                server_default=sa.text("1"),
                nullable=False,
            )
        )

    with op.batch_alter_table("events") as batch_op:
        batch_op.add_column(sa.Column("org_id", BigIntPK, nullable=True))
        batch_op.add_column(sa.Column("account_id", BigIntPK, nullable=True))
        batch_op.add_column(sa.Column("sequence", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_events_org_id_orgs",
            "orgs",
            ["org_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            "fk_events_account_id_accounts",
            "accounts",
            ["account_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index("ix_events_org_id", ["org_id"])
        batch_op.create_index("ix_events_account_id", ["account_id"])

    _backfill_inferable_event_scope()
    _backfill_turn_sequences()

    op.create_index(
        "uq_events_turn_sequence",
        "events",
        ["turn_id", "sequence"],
        unique=True,
        postgresql_where=sa.text(_TURN_SEQUENCE_PREDICATE),
        sqlite_where=sa.text(_TURN_SEQUENCE_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index("uq_events_turn_sequence", table_name="events")

    with op.batch_alter_table("events") as batch_op:
        batch_op.drop_index("ix_events_account_id")
        batch_op.drop_index("ix_events_org_id")
        batch_op.drop_constraint(
            "fk_events_account_id_accounts",
            type_="foreignkey",
        )
        batch_op.drop_constraint("fk_events_org_id_orgs", type_="foreignkey")
        batch_op.drop_column("sequence")
        batch_op.drop_column("account_id")
        batch_op.drop_column("org_id")

    with op.batch_alter_table("conversation_turns") as batch_op:
        batch_op.drop_column("next_event_sequence")
