"""Enforce canonical Operations Brain runtime provenance.

Revision ID: 20260730_0300
Revises: 20260730_0200
Create Date: 2026-07-30 03:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0300"
down_revision: str | None = "20260730_0200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PREFLIGHT_QUERIES: tuple[tuple[str, str], ...] = (
    (
        "agent_run_task_org",
        """
        SELECT 1
        FROM agent_runs r
        JOIN brain_tasks t ON t.id = r.task_id
        WHERE r.task_id IS NOT NULL AND r.org_id <> t.org_id
        LIMIT 1
        """,
    ),
    (
        "skill_run_graph",
        """
        SELECT 1
        FROM skill_runs s
        JOIN agent_runs r ON r.id = s.run_id
        LEFT JOIN brain_tasks t ON t.id = s.task_id
        WHERE s.org_id <> r.org_id
           OR s.thread_id <> r.thread_id
           OR s.turn_id <> r.turn_id
           OR (s.task_id IS NOT NULL AND (
                t.id IS NULL OR t.org_id <> s.org_id OR r.task_id <> s.task_id
           ))
        LIMIT 1
        """,
    ),
    (
        "invocation_graph",
        """
        SELECT 1
        FROM agent_invocations i
        LEFT JOIN skill_runs s ON s.id = i.skill_run_id
        LEFT JOIN agent_runs r ON r.id = i.run_id
        WHERE (
            (i.skill_run_id IS NOT NULL OR i.thread_id IS NOT NULL OR i.turn_id IS NOT NULL)
            AND (
                i.skill_run_id IS NULL OR i.run_id IS NULL
                OR i.thread_id IS NULL OR i.turn_id IS NULL
            )
            AND (
                i.skill_run_id IS NULL OR s.id IS NULL
                OR s.task_id <> i.task_id
                OR (i.run_id IS NOT NULL AND i.run_id <> s.run_id)
                OR (i.thread_id IS NOT NULL AND i.thread_id <> s.thread_id)
                OR (i.turn_id IS NOT NULL AND i.turn_id <> s.turn_id)
            )
        ) OR (
            i.skill_run_id IS NOT NULL AND (
                s.id IS NULL OR s.task_id <> i.task_id OR s.run_id <> i.run_id
                OR s.thread_id <> i.thread_id OR s.turn_id <> i.turn_id
            )
        ) OR (
            i.run_id IS NOT NULL AND i.thread_id IS NOT NULL AND i.turn_id IS NOT NULL
            AND (
                r.id IS NULL OR r.task_id <> i.task_id
                OR r.thread_id <> i.thread_id OR r.turn_id <> i.turn_id
            )
        )
        LIMIT 1
        """,
    ),
    (
        "tool_call_graph",
        """
        SELECT 1
        FROM agent_tool_calls c
        LEFT JOIN agent_invocations i ON i.id = c.invocation_id
        LEFT JOIN skill_runs s ON s.id = c.skill_run_id
        WHERE (
            (c.skill_run_id IS NOT NULL OR c.thread_id IS NOT NULL OR c.turn_id IS NOT NULL)
            AND (
                c.skill_run_id IS NULL OR c.thread_id IS NULL OR c.turn_id IS NULL
            )
            AND (
                c.invocation_id IS NULL OR i.id IS NULL
                OR i.skill_run_id IS NULL OR i.thread_id IS NULL OR i.turn_id IS NULL
                OR i.task_id <> c.task_id
                OR (
                    c.skill_run_id IS NOT NULL
                    AND c.skill_run_id <> i.skill_run_id
                )
                OR (c.thread_id IS NOT NULL AND c.thread_id <> i.thread_id)
                OR (c.turn_id IS NOT NULL AND c.turn_id <> i.turn_id)
            )
        ) OR (
            c.invocation_id IS NOT NULL AND (
                i.id IS NULL OR i.task_id <> c.task_id
            )
        ) OR (
            c.skill_run_id IS NOT NULL AND (
                s.id IS NULL OR s.task_id <> c.task_id
                OR s.thread_id <> c.thread_id OR s.turn_id <> c.turn_id
            )
        )
        LIMIT 1
        """,
    ),
    (
        "deliverable_graph",
        """
        SELECT 1
        FROM deliverables d
        JOIN content_items c ON c.id = d.content_item_id
        LEFT JOIN conversation_threads th ON th.id = d.thread_id
        LEFT JOIN conversation_turns tr ON tr.id = d.turn_id
        LEFT JOIN agent_runs r ON r.id = d.run_id
        LEFT JOIN skill_runs s ON s.id = d.skill_run_id
        LEFT JOIN conversation_threads sth ON sth.id = s.thread_id
        WHERE (
            (d.thread_id IS NOT NULL OR d.turn_id IS NOT NULL
             OR d.run_id IS NOT NULL OR d.skill_run_id IS NOT NULL)
            AND (
                d.thread_id IS NULL OR d.turn_id IS NULL
                OR d.run_id IS NULL OR d.skill_run_id IS NULL
            )
            AND (
                d.skill_run_id IS NULL OR s.id IS NULL
                OR (d.run_id IS NOT NULL AND d.run_id <> s.run_id)
                OR (d.thread_id IS NOT NULL AND d.thread_id <> s.thread_id)
                OR (d.turn_id IS NOT NULL AND d.turn_id <> s.turn_id)
                OR c.account_id IS NULL OR sth.id IS NULL
                OR c.account_id <> sth.account_id
            )
        ) OR (
            d.thread_id IS NOT NULL AND (
                th.id IS NULL OR c.account_id IS NULL OR c.account_id <> th.account_id
                OR tr.id IS NULL OR tr.thread_id <> d.thread_id
                OR r.id IS NULL OR r.thread_id <> d.thread_id OR r.turn_id <> d.turn_id
                OR s.id IS NULL OR s.run_id <> d.run_id
                OR s.thread_id <> d.thread_id OR s.turn_id <> d.turn_id
            )
        )
        LIMIT 1
        """,
    ),
)


def _preflight(bind: sa.engine.Connection) -> None:
    conflicts = [
        name
        for name, query in _PREFLIGHT_QUERIES
        if bind.execute(sa.text(query)).first() is not None
    ]
    if conflicts:
        raise RuntimeError("runtime scope preflight failed: " + ", ".join(sorted(conflicts)))


def _backfill_canonical_sources(bind: sa.engine.Connection) -> None:
    """Fill only sources whose canonical SkillRun/Invocation is explicit."""

    bind.execute(
        sa.text(
            """
            UPDATE agent_invocations
            SET run_id = (
                    SELECT s.run_id FROM skill_runs s
                    WHERE s.id = agent_invocations.skill_run_id
                ),
                thread_id = (
                    SELECT s.thread_id FROM skill_runs s
                    WHERE s.id = agent_invocations.skill_run_id
                ),
                turn_id = (
                    SELECT s.turn_id FROM skill_runs s
                    WHERE s.id = agent_invocations.skill_run_id
                )
            WHERE skill_run_id IS NOT NULL
              AND (run_id IS NULL OR thread_id IS NULL OR turn_id IS NULL)
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE agent_tool_calls
            SET skill_run_id = (
                    SELECT i.skill_run_id FROM agent_invocations i
                    WHERE i.id = agent_tool_calls.invocation_id
                ),
                thread_id = (
                    SELECT i.thread_id FROM agent_invocations i
                    WHERE i.id = agent_tool_calls.invocation_id
                ),
                turn_id = (
                    SELECT i.turn_id FROM agent_invocations i
                    WHERE i.id = agent_tool_calls.invocation_id
                )
            WHERE invocation_id IS NOT NULL
              AND EXISTS (
                    SELECT 1 FROM agent_invocations i
                    WHERE i.id = agent_tool_calls.invocation_id
                      AND i.skill_run_id IS NOT NULL
                      AND i.run_id IS NOT NULL
                      AND i.thread_id IS NOT NULL
                      AND i.turn_id IS NOT NULL
                )
              AND (skill_run_id IS NULL OR thread_id IS NULL OR turn_id IS NULL)
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE deliverables
            SET run_id = (
                    SELECT s.run_id FROM skill_runs s
                    WHERE s.id = deliverables.skill_run_id
                ),
                thread_id = (
                    SELECT s.thread_id FROM skill_runs s
                    WHERE s.id = deliverables.skill_run_id
                ),
                turn_id = (
                    SELECT s.turn_id FROM skill_runs s
                    WHERE s.id = deliverables.skill_run_id
                )
            WHERE skill_run_id IS NOT NULL
              AND (run_id IS NULL OR thread_id IS NULL OR turn_id IS NULL)
            """
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    _preflight(bind)
    _backfill_canonical_sources(bind)
    _preflight(bind)

    _create_unique_constraints()
    _create_foreign_keys()


def _create_unique_constraints() -> None:
    constraints = (
        ("uq_brain_tasks_id_org", "brain_tasks", ["id", "org_id"]),
        (
            "uq_conversation_turn_id_thread",
            "conversation_turns",
            ["id", "thread_id"],
        ),
        (
            "uq_agent_runs_id_thread_turn",
            "agent_runs",
            ["id", "thread_id", "turn_id"],
        ),
        (
            "uq_agent_runs_id_task_thread_turn",
            "agent_runs",
            ["id", "task_id", "thread_id", "turn_id"],
        ),
        (
            "uq_agent_runs_id_task_thread_turn_org",
            "agent_runs",
            ["id", "task_id", "thread_id", "turn_id", "org_id"],
        ),
        (
            "uq_skill_runs_id_task_run_thread_turn",
            "skill_runs",
            ["id", "task_id", "run_id", "thread_id", "turn_id"],
        ),
        (
            "uq_skill_runs_id_task_thread_turn",
            "skill_runs",
            ["id", "task_id", "thread_id", "turn_id"],
        ),
        (
            "uq_skill_runs_id_run_thread_turn",
            "skill_runs",
            ["id", "run_id", "thread_id", "turn_id"],
        ),
        (
            "uq_agent_invocations_id_task",
            "agent_invocations",
            ["id", "task_id"],
        ),
    )
    for name, table, columns in constraints:
        op.create_unique_constraint(name, table, columns)


def _create_foreign_keys() -> None:
    constraints = (
        (
            "fk_agent_runs_task_org",
            "agent_runs",
            "brain_tasks",
            ["task_id", "org_id"],
            ["id", "org_id"],
        ),
        (
            "fk_skill_runs_task_org",
            "skill_runs",
            "brain_tasks",
            ["task_id", "org_id"],
            ["id", "org_id"],
        ),
        (
            "fk_skill_runs_run_task_thread_turn_org",
            "skill_runs",
            "agent_runs",
            ["run_id", "task_id", "thread_id", "turn_id", "org_id"],
            ["id", "task_id", "thread_id", "turn_id", "org_id"],
        ),
        (
            "fk_agent_invocations_turn_thread",
            "agent_invocations",
            "conversation_turns",
            ["turn_id", "thread_id"],
            ["id", "thread_id"],
        ),
        (
            "fk_agent_invocations_run_task_thread_turn",
            "agent_invocations",
            "agent_runs",
            ["run_id", "task_id", "thread_id", "turn_id"],
            ["id", "task_id", "thread_id", "turn_id"],
        ),
        (
            "fk_agent_invocations_skill_task_run_thread_turn",
            "agent_invocations",
            "skill_runs",
            ["skill_run_id", "task_id", "run_id", "thread_id", "turn_id"],
            ["id", "task_id", "run_id", "thread_id", "turn_id"],
        ),
        (
            "fk_agent_tool_calls_turn_thread",
            "agent_tool_calls",
            "conversation_turns",
            ["turn_id", "thread_id"],
            ["id", "thread_id"],
        ),
        (
            "fk_agent_tool_calls_skill_task_thread_turn",
            "agent_tool_calls",
            "skill_runs",
            ["skill_run_id", "task_id", "thread_id", "turn_id"],
            ["id", "task_id", "thread_id", "turn_id"],
        ),
        (
            "fk_agent_tool_calls_invocation_task",
            "agent_tool_calls",
            "agent_invocations",
            ["invocation_id", "task_id"],
            ["id", "task_id"],
        ),
        (
            "fk_deliverables_turn_thread",
            "deliverables",
            "conversation_turns",
            ["turn_id", "thread_id"],
            ["id", "thread_id"],
        ),
        (
            "fk_deliverables_run_thread_turn",
            "deliverables",
            "agent_runs",
            ["run_id", "thread_id", "turn_id"],
            ["id", "thread_id", "turn_id"],
        ),
        (
            "fk_deliverables_skill_run_thread_turn",
            "deliverables",
            "skill_runs",
            ["skill_run_id", "run_id", "thread_id", "turn_id"],
            ["id", "run_id", "thread_id", "turn_id"],
        ),
    )
    for name, source, target, local, remote in constraints:
        op.create_foreign_key(name, source, target, local, remote)


def downgrade() -> None:
    foreign_keys = (
        ("deliverables", "fk_deliverables_skill_run_thread_turn"),
        ("deliverables", "fk_deliverables_run_thread_turn"),
        ("deliverables", "fk_deliverables_turn_thread"),
        ("agent_tool_calls", "fk_agent_tool_calls_invocation_task"),
        ("agent_tool_calls", "fk_agent_tool_calls_skill_task_thread_turn"),
        ("agent_tool_calls", "fk_agent_tool_calls_turn_thread"),
        (
            "agent_invocations",
            "fk_agent_invocations_skill_task_run_thread_turn",
        ),
        ("agent_invocations", "fk_agent_invocations_run_task_thread_turn"),
        ("agent_invocations", "fk_agent_invocations_turn_thread"),
        ("skill_runs", "fk_skill_runs_run_task_thread_turn_org"),
        ("skill_runs", "fk_skill_runs_task_org"),
        ("agent_runs", "fk_agent_runs_task_org"),
    )
    for table, name in foreign_keys:
        op.drop_constraint(name, table, type_="foreignkey")

    uniques = (
        ("agent_invocations", "uq_agent_invocations_id_task"),
        ("skill_runs", "uq_skill_runs_id_run_thread_turn"),
        ("skill_runs", "uq_skill_runs_id_task_thread_turn"),
        ("skill_runs", "uq_skill_runs_id_task_run_thread_turn"),
        ("agent_runs", "uq_agent_runs_id_task_thread_turn_org"),
        ("agent_runs", "uq_agent_runs_id_task_thread_turn"),
        ("agent_runs", "uq_agent_runs_id_thread_turn"),
        ("conversation_turns", "uq_conversation_turn_id_thread"),
        ("brain_tasks", "uq_brain_tasks_id_org"),
    )
    for table, name in uniques:
        op.drop_constraint(name, table, type_="unique")
