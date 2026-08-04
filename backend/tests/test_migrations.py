import importlib
import inspect
import json
import os

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import event
from sqlalchemy.dialects import postgresql

from app.config import settings


def get_head_revision() -> str | None:
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    return script.get_current_head()


def test_tool_side_effect_outbox_migration_is_reversible(monkeypatch) -> None:
    migration = importlib.import_module("migrations.versions.20260730_0500_tool_side_effect_outbox")
    assert migration.down_revision == "20260730_0400"

    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    tool_calls = sa.Table(
        "agent_tool_calls",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tool_code", sa.String(120), nullable=False),
    )
    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(
            tool_calls.insert(),
            [{"id": 1, "tool_code": "account.profile"}],
        )
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        migration.upgrade()

        inspector = sa.inspect(connection)
        columns = {column["name"]: column for column in inspector.get_columns("agent_tool_calls")}
        checks = {
            constraint["name"] for constraint in inspector.get_check_constraints("agent_tool_calls")
        }
        assert columns["side_effect_level"]["nullable"] is False
        assert connection.execute(
            sa.text(
                "SELECT side_effect_level, provider_idempotency_key "
                "FROM agent_tool_calls WHERE id = 1"
            )
        ).one() == ("read", None)
        assert "ck_agent_tool_calls_side_effect_level" in checks
        assert inspector.has_table("tool_execution_attempts")

        migration.downgrade()

        inspector = sa.inspect(connection)
        assert inspector.has_table("tool_execution_attempts") is False
        assert "side_effect_level" not in {
            column["name"] for column in inspector.get_columns("agent_tool_calls")
        }
        assert "provider_idempotency_key" not in {
            column["name"] for column in inspector.get_columns("agent_tool_calls")
        }


def test_runtime_state_convergence_migrates_history_before_constraints(
    monkeypatch,
) -> None:
    migration = importlib.import_module(
        "migrations.versions.20260730_0200_runtime_state_convergence"
    )
    assert migration.down_revision == "20260730_0100"

    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    turns = sa.Table(
        "conversation_turns",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("assistant_response", sa.Text, nullable=True),
    )
    runs = sa.Table(
        "agent_runs",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("turn_id", sa.Integer, nullable=True),
        sa.Column("status", sa.String(40), nullable=False),
    )
    skills = sa.Table(
        "skill_runs",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("status", sa.String(40), nullable=False),
    )

    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(
            turns.insert(),
            [
                {"id": 1, "assistant_response": "正式成功回复"},
                {"id": 2, "assistant_response": None},
                {"id": 3, "assistant_response": None},
                {"id": 4, "assistant_response": None},
                {"id": 5, "assistant_response": None},
            ],
        )
        connection.execute(
            runs.insert(),
            [
                {"id": 10, "turn_id": 1, "status": "error"},
                {"id": 20, "turn_id": 2, "status": "retry_scheduled"},
                {"id": 30, "turn_id": 3, "status": "error"},
                {"id": 50, "turn_id": 5, "status": "mystery"},
            ],
        )
        connection.execute(
            skills.insert(),
            [
                {"id": 100, "status": "waiting_approval"},
                {"id": 101, "status": "dead_letter"},
                {"id": 102, "status": "mystery"},
            ],
        )
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        migration.upgrade()

        assert connection.execute(
            sa.text("SELECT id, status FROM conversation_turns ORDER BY id")
        ).all() == [
            (1, "completed"),
            (2, "retry_wait"),
            (3, "failed"),
            (4, "queued"),
            (5, "queued"),
        ]
        assert connection.execute(
            sa.text("SELECT id, status FROM agent_runs ORDER BY id")
        ).all() == [
            (10, "failed"),
            (20, "retry_wait"),
            (30, "failed"),
            (50, "failed"),
        ]
        assert connection.execute(
            sa.text("SELECT id, status FROM skill_runs ORDER BY id")
        ).all() == [
            (100, "waiting_permission"),
            (101, "failed"),
            (102, "failed"),
        ]
        inspector = sa.inspect(connection)
        assert {item["name"] for item in inspector.get_check_constraints("conversation_turns")} >= {
            "ck_conversation_turns_status"
        }
        assert {item["name"] for item in inspector.get_check_constraints("agent_runs")} >= {
            "ck_agent_runs_status"
        }
        assert {item["name"] for item in inspector.get_check_constraints("skill_runs")} >= {
            "ck_skill_runs_status"
        }
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text("UPDATE conversation_turns SET status = 'mystery' WHERE id = 4")
            )

        migration.downgrade()
        assert "status" not in {
            column["name"] for column in sa.inspect(connection).get_columns("conversation_turns")
        }


def test_runtime_scope_migration_backfills_only_canonical_sources_and_preflights_conflicts() -> (
    None
):
    migration = importlib.import_module(
        "migrations.versions.20260730_0300_runtime_scope_constraints"
    )
    assert migration.down_revision == "20260730_0200"

    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    for name, columns in (
        (
            "brain_tasks",
            (
                sa.Column("id", sa.Integer, primary_key=True),
                sa.Column("org_id", sa.Integer, nullable=False),
            ),
        ),
        (
            "agent_runs",
            (
                sa.Column("id", sa.Integer, primary_key=True),
                sa.Column("org_id", sa.Integer, nullable=False),
                sa.Column("task_id", sa.Integer),
                sa.Column("thread_id", sa.Integer),
                sa.Column("turn_id", sa.Integer),
            ),
        ),
        (
            "skill_runs",
            (
                sa.Column("id", sa.Integer, primary_key=True),
                sa.Column("org_id", sa.Integer, nullable=False),
                sa.Column("task_id", sa.Integer),
                sa.Column("run_id", sa.Integer, nullable=False),
                sa.Column("thread_id", sa.Integer, nullable=False),
                sa.Column("turn_id", sa.Integer, nullable=False),
            ),
        ),
        (
            "agent_invocations",
            (
                sa.Column("id", sa.Integer, primary_key=True),
                sa.Column("task_id", sa.Integer, nullable=False),
                sa.Column("run_id", sa.Integer),
                sa.Column("skill_run_id", sa.Integer),
                sa.Column("thread_id", sa.Integer),
                sa.Column("turn_id", sa.Integer),
            ),
        ),
        (
            "agent_tool_calls",
            (
                sa.Column("id", sa.Integer, primary_key=True),
                sa.Column("task_id", sa.Integer, nullable=False),
                sa.Column("invocation_id", sa.Integer),
                sa.Column("skill_run_id", sa.Integer),
                sa.Column("thread_id", sa.Integer),
                sa.Column("turn_id", sa.Integer),
            ),
        ),
        (
            "conversation_threads",
            (
                sa.Column("id", sa.Integer, primary_key=True),
                sa.Column("account_id", sa.Integer, nullable=False),
            ),
        ),
        (
            "conversation_turns",
            (
                sa.Column("id", sa.Integer, primary_key=True),
                sa.Column("thread_id", sa.Integer, nullable=False),
            ),
        ),
        (
            "content_items",
            (
                sa.Column("id", sa.Integer, primary_key=True),
                sa.Column("account_id", sa.Integer),
            ),
        ),
        (
            "deliverables",
            (
                sa.Column("id", sa.Integer, primary_key=True),
                sa.Column("content_item_id", sa.Integer, nullable=False),
                sa.Column("thread_id", sa.Integer),
                sa.Column("turn_id", sa.Integer),
                sa.Column("run_id", sa.Integer),
                sa.Column("skill_run_id", sa.Integer),
            ),
        ),
    ):
        sa.Table(name, metadata, *columns)

    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(sa.text("INSERT INTO brain_tasks VALUES (1, 7)"))
        connection.execute(sa.text("INSERT INTO conversation_threads VALUES (10, 99)"))
        connection.execute(sa.text("INSERT INTO conversation_turns VALUES (20, 10)"))
        connection.execute(sa.text("INSERT INTO content_items VALUES (30, 99)"))
        connection.execute(sa.text("INSERT INTO agent_runs VALUES (40, 7, 1, 10, 20)"))
        connection.execute(sa.text("INSERT INTO skill_runs VALUES (50, 7, 1, 40, 10, 20)"))
        connection.execute(
            sa.text(
                "INSERT INTO agent_invocations VALUES "
                "(60, 1, NULL, 50, NULL, NULL), "
                "(61, 1, NULL, NULL, NULL, NULL)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO agent_tool_calls VALUES "
                "(70, 1, 60, NULL, NULL, NULL), "
                "(71, 1, 61, NULL, NULL, NULL)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO deliverables VALUES "
                "(80, 30, NULL, NULL, NULL, 50), "
                "(81, 30, NULL, NULL, NULL, NULL)"
            )
        )

        migration._preflight(connection)
        migration._backfill_canonical_sources(connection)
        migration._preflight(connection)
        assert connection.execute(
            sa.text("SELECT run_id, thread_id, turn_id FROM agent_invocations WHERE id = 60")
        ).one() == (40, 10, 20)
        assert connection.execute(
            sa.text("SELECT skill_run_id, thread_id, turn_id FROM agent_tool_calls WHERE id = 70")
        ).one() == (50, 10, 20)
        assert connection.execute(
            sa.text("SELECT run_id, thread_id, turn_id FROM deliverables WHERE id = 80")
        ).one() == (40, 10, 20)
        assert connection.execute(
            sa.text(
                "SELECT run_id, skill_run_id, thread_id, turn_id "
                "FROM agent_invocations WHERE id = 61"
            )
        ).one() == (None, None, None, None)

        connection.execute(sa.text("UPDATE agent_invocations SET thread_id = 999 WHERE id = 60"))
        with pytest.raises(RuntimeError, match="invocation_graph"):
            migration._preflight(connection)

    source = inspect.getsource(migration)
    assert "brain_tasks.thread_id" not in source
    assert "account_ids" not in source


def test_client_workspace_migration_is_additive() -> None:
    module = importlib.import_module("migrations.versions.20260716_0200_client_workspace_shell")

    assert module.down_revision == "20260716_0100"
    source = inspect.getsource(module.upgrade)
    assert '"clients"' in source
    assert '"project_accounts"' in source
    assert 'drop_column("accounts", "project_id")' not in source
    assert "::workspace_role" in source


def test_knowledge_workspace_migration_preserves_legacy_entries() -> None:
    module = importlib.import_module("migrations.versions.20260717_0200_knowledge_workspace")

    assert module.down_revision == "20260717_0100"
    source = inspect.getsource(module.upgrade)
    assert '"knowledge_suggestions"' in source
    assert '"knowledge_citations"' in source
    assert "UPDATE knowledge_entries" in source
    assert 'drop_table("knowledge_entries")' not in source


def test_user_deletion_migration_restricts_all_creator_foreign_keys() -> None:
    module = importlib.import_module(
        "migrations.versions.20260720_0200_user_deletion_restrict_ownership"
    )

    assert module.down_revision == "20260720_0100"
    source = inspect.getsource(module.upgrade)
    assert '"matrix_distribution_plans"' in source
    assert '"knowledge_entries"' in source
    assert 'ondelete="RESTRICT"' in source


def test_user_deletion_migration_preserves_sqlite_constraint_name_on_downgrade() -> None:
    module = importlib.import_module(
        "migrations.versions.20260720_0200_user_deletion_restrict_ownership"
    )

    source = inspect.getsource(module.downgrade)
    assert 'new_sqlite_name="fk_matrix_distribution_plans_created_by_id_users"' in source


def test_user_deletion_preview_reservation_migration_is_reversible_and_non_sensitive() -> None:
    module = importlib.import_module(
        "migrations.versions.20260720_0300_user_deletion_preview_reservations"
    )

    assert module.down_revision == "20260720_0200"
    upgrade_source = inspect.getsource(module.upgrade)
    downgrade_source = inspect.getsource(module.downgrade)
    assert "op.create_table" in upgrade_source
    assert '"user_deletion_preview_reservations"' in upgrade_source
    assert "sa.UniqueConstraint" in upgrade_source
    assert '"organization_id"' in upgrade_source
    assert '"operation_id"' in upgrade_source
    assert 'drop_table("user_deletion_preview_reservations")' in downgrade_source
    for forbidden in ("preview_token", "target_email", "secondary_password"):
        assert forbidden not in upgrade_source


def test_turn_observability_migration_is_additive_nullable_and_reversible(
    monkeypatch,
) -> None:
    migration = importlib.import_module("migrations.versions.20260730_0600_turn_observability")
    assert migration.down_revision == "20260730_0500"

    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    turns = sa.Table(
        "conversation_turns",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_input", sa.Text, nullable=False),
    )
    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(turns.insert(), [{"id": 1, "user_input": "历史消息"}])
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        migration.upgrade()

        inspector = sa.inspect(connection)
        columns = {column["name"]: column for column in inspector.get_columns("conversation_turns")}
        metric_names = {
            "route_ms",
            "first_token_ms",
            "completion_ms",
            "total_ms",
            "model_call_count",
        }
        assert metric_names <= columns.keys()
        assert all(columns[name]["nullable"] for name in metric_names)
        assert connection.execute(
            sa.text(
                "SELECT route_ms, first_token_ms, completion_ms, total_ms, "
                "model_call_count FROM conversation_turns WHERE id = 1"
            )
        ).one() == (None, None, None, None, None)
        checks = {
            constraint["name"]
            for constraint in inspector.get_check_constraints("conversation_turns")
        }
        assert {
            "ck_conversation_turns_route_ms",
            "ck_conversation_turns_first_token_ms",
            "ck_conversation_turns_completion_ms",
            "ck_conversation_turns_total_ms",
            "ck_conversation_turns_model_call_count",
        } <= checks
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text("UPDATE conversation_turns SET model_call_count = -1 WHERE id = 1")
            )

        migration.downgrade()
        assert metric_names.isdisjoint(
            {column["name"] for column in sa.inspect(connection).get_columns("conversation_turns")}
        )


def test_data_import_parser_version_migration_is_reversible(monkeypatch) -> None:
    migration = importlib.import_module(
        "migrations.versions.20260731_0100_data_import_parser_version"
    )
    assert migration.down_revision == "20260730_0600"

    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    batches = sa.Table(
        "data_import_batches",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("template_code", sa.String(80), nullable=False),
    )
    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(
            batches.insert(),
            [{"id": 1, "template_code": "douyin_period_aggregate_v1"}],
        )
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        migration.upgrade()

        inspector = sa.inspect(connection)
        columns = {
            column["name"]: column for column in inspector.get_columns("data_import_batches")
        }
        checks = {
            constraint["name"]
            for constraint in inspector.get_check_constraints("data_import_batches")
        }
        assert columns["parser_version"]["nullable"] is False
        assert (
            connection.execute(
                sa.text("SELECT parser_version FROM data_import_batches WHERE id = 1")
            ).scalar_one()
            == 1
        )
        connection.execute(
            sa.text(
                "INSERT INTO data_import_batches (id, template_code) "
                "VALUES (2, 'douyin_daily_play_v1')"
            )
        )
        assert (
            connection.execute(
                sa.text("SELECT parser_version FROM data_import_batches WHERE id = 2")
            ).scalar_one()
            == 1
        )
        assert "ck_data_import_batches_parser_version_positive" in checks
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text("UPDATE data_import_batches SET parser_version = 0 WHERE id = 1")
            )

        migration.downgrade()
        assert "parser_version" not in {
            column["name"] for column in sa.inspect(connection).get_columns("data_import_batches")
        }


def test_bulk_account_data_ingestion_migration_is_reversible(monkeypatch) -> None:
    migration = importlib.import_module(
        "migrations.versions.20260731_0200_bulk_account_data_ingestion"
    )
    assert migration.down_revision == "20260731_0100"

    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    sa.Table("orgs", metadata, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table("users", metadata, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table("accounts", metadata, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table(
        "data_import_batches",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False),
        sa.Column("account_id", sa.Integer, nullable=False),
        sa.UniqueConstraint(
            "org_id",
            "account_id",
            "id",
            name="uq_data_import_batches_org_account_id",
        ),
    )
    sa.Table(
        "data_import_rows",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False),
        sa.Column("account_id", sa.Integer, nullable=False),
        sa.Column("batch_id", sa.Integer, nullable=False),
    )
    with engine.begin() as connection:
        metadata.create_all(connection)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        migration.upgrade()

        inspector = sa.inspect(connection)
        assert {"data_import_jobs", "data_import_files", "data_field_observations"} <= set(
            inspector.get_table_names()
        )
        batch_columns = {column["name"] for column in inspector.get_columns("data_import_batches")}
        assert {
            "job_id",
            "job_file_id",
            "sheet_name",
            "dataset_ordinal",
            "confirmed_sequence",
        } <= batch_columns
        observation_uniques = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("data_field_observations")
        }
        assert "uq_data_field_observations_source_field" in observation_uniques

        migration.downgrade()
        inspector = sa.inspect(connection)
        assert {
            "data_import_jobs",
            "data_import_files",
            "data_field_observations",
        }.isdisjoint(inspector.get_table_names())
        assert "job_id" not in {
            column["name"] for column in inspector.get_columns("data_import_batches")
        }


def test_migration_head_is_manual_publish_followup() -> None:
    assert get_head_revision() == "20260805_0100"


def test_turn_interrupts_sqlite_upgrade_and_downgrade(monkeypatch) -> None:
    migration = importlib.import_module(
        "migrations.versions.20260804_0500_turn_interrupts"
    )
    assert migration.down_revision == "20260804_0450"
    engine = sa.create_engine("sqlite://")

    with engine.begin() as connection:
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        migration.upgrade()
        inspector = sa.inspect(connection)
        assert "turn_interrupts" in inspector.get_table_names()
        assert {
            "uq_turn_interrupts_effective_pending",
            "ix_turn_interrupts_scope_status",
            "ix_turn_interrupts_source",
            "ix_turn_interrupts_resolved_by",
        } <= {index["name"] for index in inspector.get_indexes("turn_interrupts")}

        migration.downgrade()
        assert "turn_interrupts" not in sa.inspect(connection).get_table_names()


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is required for the PostgreSQL migration gate",
)
def test_turn_interrupts_postgres_upgrade_downgrade_reupgrade_gate(monkeypatch) -> None:
    raw_url = os.environ["TEST_POSTGRES_URL"]
    async_url = raw_url.replace(
        "postgresql+psycopg://", "postgresql+asyncpg://"
    ).replace("postgresql://", "postgresql+asyncpg://")
    sync_url = raw_url.replace(
        "postgresql+asyncpg://", "postgresql+psycopg://"
    ).replace("postgresql://", "postgresql+psycopg://")
    monkeypatch.setattr(settings, "database_url", async_url)
    config = Config("alembic.ini")

    command.upgrade(config, "20260804_0450")
    command.upgrade(config, "20260804_0500")
    engine = sa.create_engine(sync_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
                "20260804_0500"
            )
            inspector = sa.inspect(connection)
            assert "turn_interrupts" in inspector.get_table_names()
            assert "uq_turn_interrupts_effective_pending" in {
                item["name"] for item in inspector.get_indexes("turn_interrupts")
            }

        command.downgrade(config, "20260804_0450")
        with engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
                "20260804_0450"
            )
            assert "turn_interrupts" not in sa.inspect(connection).get_table_names()

        command.upgrade(config, "20260804_0500")
        with engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
                "20260804_0500"
            )
            assert "turn_interrupts" in sa.inspect(connection).get_table_names()
    finally:
        engine.dispose()


def test_revision_terminal_deliverable_streams_sqlite_upgrade_and_downgrade(
    monkeypatch,
) -> None:
    migration = importlib.import_module(
        "migrations.versions.20260804_0450_revision_terminal_deliverable_streams"
    )
    assert migration.down_revision == "20260804_0400"
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    sa.Table(
        "deliverables",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("content_item_id", sa.Integer, nullable=False),
        sa.Column("agent_code", sa.String(64), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.UniqueConstraint("content_item_id", "type", "version", name="uq_deliverable_version"),
    )
    sa.Table(
        "run_revisions",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('planned', 'waiting_predecessor', 'running', "
            "'completed', 'failed', 'cancelled')",
            name="ck_run_revisions_status",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND started_at IS NOT NULL AND finished_at IS NULL) OR "
            "(status IN ('completed', 'failed', 'cancelled') AND finished_at IS NOT NULL) OR "
            "(status IN ('planned', 'waiting_predecessor') AND "
            "started_at IS NULL AND finished_at IS NULL)",
            name="ck_run_revisions_lifecycle",
        ),
    )

    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(
            sa.text(
                "INSERT INTO deliverables "
                "(id, content_item_id, agent_code, type, version) VALUES "
                "(1, 1, 'decision', 'review', 1), "
                "(2, 1, 'content', 'review', 2), "
                "(3, 1, 'decision', 'review', 3)"
            )
        )
        connection.execute(sa.text("INSERT INTO run_revisions (id, status) VALUES (1, 'planned')"))
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        migration.upgrade()

        rows = connection.execute(
            sa.text("SELECT agent_code, version FROM deliverables ORDER BY agent_code, version")
        ).all()
        assert rows == [("content", 2), ("decision", 1), ("decision", 3)]
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO deliverables "
                    "(id, content_item_id, agent_code, type, version) "
                    "VALUES (5, 1, 'decision', 'review', 3)"
                )
            )
        connection.execute(
            sa.text(
                "UPDATE run_revisions SET status = 'blocked', "
                "finished_at = CURRENT_TIMESTAMP WHERE id = 1"
            )
        )

        migration.downgrade()

        assert (
            connection.scalar(sa.text("SELECT status FROM run_revisions WHERE id = 1")) == "failed"
        )
        versions = (
            connection.execute(
                sa.text(
                    "SELECT version FROM deliverables WHERE content_item_id = 1 "
                    "AND type = 'review' ORDER BY version"
                )
            )
            .scalars()
            .all()
        )
        assert versions == [1, 2, 3]


def test_revision_terminal_deliverable_streams_downgrade_rejects_collision_atomically(
    monkeypatch,
) -> None:
    migration = importlib.import_module(
        "migrations.versions.20260804_0450_revision_terminal_deliverable_streams"
    )
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    sa.Table(
        "deliverables",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("content_item_id", sa.Integer, nullable=False),
        sa.Column("agent_code", sa.String(64), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.UniqueConstraint(
            "content_item_id",
            "agent_code",
            "type",
            "version",
            name="uq_deliverable_version",
        ),
    )
    sa.Table(
        "run_revisions",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('planned', 'waiting_predecessor', 'running', "
            "'completed', 'failed', 'cancelled', 'blocked', 'stopped', "
            "'manual_reconciliation')",
            name="ck_run_revisions_status",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND started_at IS NOT NULL AND finished_at IS NULL) OR "
            "(status IN ('completed', 'failed', 'cancelled', 'blocked', 'stopped', "
            "'manual_reconciliation') AND finished_at IS NOT NULL) OR "
            "(status IN ('planned', 'waiting_predecessor') AND "
            "started_at IS NULL AND finished_at IS NULL)",
            name="ck_run_revisions_lifecycle",
        ),
    )
    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(
            sa.text(
                "INSERT INTO deliverables "
                "(id, content_item_id, agent_code, type, version) VALUES "
                "(1, 1, 'decision', 'review', 1), "
                "(2, 1, 'content', 'review', 1)"
            )
        )
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        with pytest.raises(RuntimeError, match="cross-agent version collisions"):
            migration.downgrade()

        assert connection.execute(
            sa.text("SELECT id, version FROM deliverables ORDER BY id")
        ).all() == [(1, 1), (2, 1)]
        assert {
            tuple(item["column_names"])
            for item in sa.inspect(connection).get_unique_constraints("deliverables")
        } == {("content_item_id", "agent_code", "type", "version")}


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is required for the PostgreSQL concurrent writer gate",
)
def test_revision_terminal_deliverable_streams_postgres_concurrent_writers(
    monkeypatch,
) -> None:
    import asyncio
    from uuid import uuid4

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import ContentItem
    from app.models.enums import AgentCode, DeliverableStatus, DeliverableType
    from app.services.runtime_deliverables import write_runtime_deliverable

    raw_url = os.environ["TEST_POSTGRES_URL"]
    async_url = raw_url.replace("postgresql+psycopg://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    monkeypatch.setattr(settings, "database_url", async_url)
    command.upgrade(Config("alembic.ini"), "20260804_0450")

    async def exercise() -> None:
        engine = create_async_engine(async_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        suffix = uuid4().hex
        try:
            async with sessions() as session:
                content = ContentItem(title=f"deliverable-concurrency-{suffix}")
                session.add(content)
                await session.commit()
                content_id = content.id

            first_locked = asyncio.Event()
            allow_first_commit = asyncio.Event()

            async def write_one(
                payload: str,
                *,
                agent_code: str = AgentCode.DECISION.value,
                hold_lock: bool = False,
            ) -> int:
                async with sessions() as session:
                    content = await session.get(ContentItem, content_id)
                    assert content is not None
                    deliverable = await write_runtime_deliverable(
                        session,
                        scope=None,
                        content=content,
                        agent_code=agent_code,
                        deliverable_type=DeliverableType.REVIEW_REPORT,
                        status=DeliverableStatus.PENDING_REVIEW,
                        payload={"summary": payload},
                    )
                    if hold_lock:
                        first_locked.set()
                        await allow_first_commit.wait()
                    await session.commit()
                    return deliverable.version

            first = asyncio.create_task(write_one("first", hold_lock=True))
            await asyncio.wait_for(first_locked.wait(), timeout=5)
            second = asyncio.create_task(write_one("second"))
            await asyncio.sleep(0.1)
            assert not second.done()
            allow_first_commit.set()
            assert await asyncio.gather(first, second) == [1, 2]
            assert (
                await write_one(
                    "other-agent",
                    agent_code=AgentCode.CONTENT_DIRECTOR.value,
                )
                == 1
            )
        finally:
            async with sessions() as session:
                content = await session.get(ContentItem, content_id)
                if content is not None:
                    await session.delete(content)
                    await session.commit()
            await engine.dispose()

    asyncio.run(exercise())


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is required for the PostgreSQL reference matrix gate",
)
def test_revision_terminal_deliverable_streams_postgres_reference_matrix_and_atomic_downgrade(
    monkeypatch,
) -> None:
    from uuid import uuid4

    raw_url = os.environ["TEST_POSTGRES_URL"]
    async_url = raw_url.replace("postgresql+psycopg://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg://").replace(
        "postgresql://", "postgresql+psycopg://"
    )
    monkeypatch.setattr(settings, "database_url", async_url)
    payload = json.dumps
    config = Config("alembic.ini")
    engine = sa.create_engine(sync_url)
    with engine.begin() as connection:
        connection.execute(
            sa.text("DELETE FROM deliverables WHERE payload @> CAST(:marker AS jsonb)"),
            {"marker": payload({"task4c_downgrade_collision": True})},
        )
    command.downgrade(config, "20260804_0400")
    suffix = uuid4().hex

    with engine.begin() as connection:

        def insert_id(sql: str, values: dict) -> int:
            return int(connection.scalar(sa.text(sql), values))

        org_id = insert_id("INSERT INTO orgs (name) VALUES (:name) RETURNING id", {"name": suffix})
        user_id = insert_id(
            "INSERT INTO users "
            "(org_id, email, hashed_password, display_name, role, is_active) "
            "VALUES (:org, :email, 'x', 'matrix', 'admin', true) RETURNING id",
            {"org": org_id, "email": f"{suffix}@example.com"},
        )
        account_id = insert_id(
            "INSERT INTO accounts (org_id, platform, nickname, status) "
            "VALUES (:org, 'douyin', :name, 'active') RETURNING id",
            {"org": org_id, "name": suffix},
        )
        content_id = insert_id(
            "INSERT INTO content_items "
            "(created_by_id, account_id, title, current_stage, status) "
            "VALUES (:user, :account, :title, 'positioning', 'draft') RETURNING id",
            {"user": user_id, "account": account_id, "title": suffix},
        )
        thread_id = insert_id(
            "INSERT INTO conversation_threads (org_id, created_by_id, account_id, title) "
            "VALUES (:org, :user, :account, :title) RETURNING id",
            {"org": org_id, "user": user_id, "account": account_id, "title": suffix},
        )
        turn_id = insert_id(
            "INSERT INTO conversation_turns "
            "(thread_id, org_id, created_by_id, client_message_id, user_input) "
            "VALUES (:thread, :org, :user, :key, 'matrix') RETURNING id",
            {"thread": thread_id, "org": org_id, "user": user_id, "key": suffix},
        )
        task_id = insert_id(
            "INSERT INTO brain_tasks "
            "(org_id, created_by_id, content_item_id, title, type, status, progress, "
            "current_focus, risk_count) VALUES "
            "(:org, :user, :content, 'matrix', 'content_creation', 'running', 1, '', 0) "
            "RETURNING id",
            {"org": org_id, "user": user_id, "content": content_id},
        )
        run_id = insert_id(
            "INSERT INTO agent_runs "
            "(org_id, requested_by_id, task_id, thread_id, turn_id, client_message_id, "
            "status, phase, request_payload, result_payload) VALUES "
            "(:org, :user, :task, :thread, :turn, :key, 'running', 'running', "
            "CAST(:request AS jsonb), CAST(:result AS jsonb)) RETURNING id",
            {
                "org": org_id,
                "user": user_id,
                "task": task_id,
                "thread": thread_id,
                "turn": turn_id,
                "key": suffix,
                "request": payload({"artifact_id": "pending"}),
                "result": payload({}),
            },
        )
        skill_id = insert_id(
            "INSERT INTO skill_runs "
            "(org_id, thread_id, turn_id, run_id, task_id, idempotency_key, skill_code, "
            "skill_version, status, input_snapshot, output_snapshot, input_hash) VALUES "
            "(:org, :thread, :turn, :run, :task, :key, 'operation_iteration', 1, "
            "'running', CAST(:input AS json), CAST(:output AS json), :hash) RETURNING id",
            {
                "org": org_id,
                "thread": thread_id,
                "turn": turn_id,
                "run": run_id,
                "task": task_id,
                "key": suffix,
                "input": payload({"source_artifact_ids": []}),
                "output": payload({"operation_plan": {"artifact_id": "pending"}}),
                "hash": "a" * 64,
            },
        )
        deliverable_ids: list[int] = []
        for agent_code, version in (
            ("00-decision", 1),
            ("02-content-director", 2),
            ("00-decision", 3),
        ):
            deliverable_ids.append(
                insert_id(
                    "INSERT INTO deliverables "
                    "(content_item_id, thread_id, turn_id, run_id, skill_run_id, agent_code, "
                    "type, version, status, payload) VALUES "
                    "(:content, :thread, :turn, :run, :skill, :agent, 'review_report', "
                    ":version, 'pending_review', CAST(:payload AS jsonb)) RETURNING id",
                    {
                        "content": content_id,
                        "thread": thread_id,
                        "turn": turn_id,
                        "run": run_id,
                        "skill": skill_id,
                        "agent": agent_code,
                        "version": version,
                        "payload": payload({"version": version}),
                    },
                )
            )
        source_id = deliverable_ids[-1]
        connection.execute(
            sa.text("UPDATE agent_runs SET request_payload=CAST(:value AS jsonb) WHERE id=:id"),
            {"id": run_id, "value": payload({"source_artifact_id": source_id, "version": 3})},
        )
        connection.execute(
            sa.text("UPDATE skill_runs SET output_snapshot=CAST(:value AS json) WHERE id=:id"),
            {
                "id": skill_id,
                "value": payload({"operation_plan": {"artifact_id": source_id, "version": 3}}),
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO deliverable_acceptances "
                "(task_id, deliverable_id, agent_code, agent_name, deliverable_type, title, "
                "version, summary, acceptance_items, history_versions, status, "
                "brain_rejudge_basis) VALUES "
                "(:task, :artifact, '00-decision', 'decision', 'review_report', 'matrix', 3, "
                "'', CAST('[]' AS json), CAST(:history AS json), 'pending', CAST('[]' AS json))"
            ),
            {"task": task_id, "artifact": source_id, "history": payload([{"version": 3}])},
        )
        connection.execute(
            sa.text(
                "INSERT INTO deliverable_action_executions "
                "(org_id, account_id, requested_by_id, artifact_id, artifact_version, "
                "action_code, idempotency_key, request_fingerprint, status, result_payload) "
                "VALUES (:org, :account, :user, :artifact, 3, 'shoot', :key, :hash, "
                "'completed', CAST(:result AS jsonb))"
            ),
            {
                "org": org_id,
                "account": account_id,
                "user": user_id,
                "artifact": source_id,
                "key": suffix,
                "hash": "b" * 64,
                "result": payload({"source_artifact_id": source_id, "version": 3}),
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO shoot_tasks "
                "(org_id, account_id, content_item_id, source_artifact_id, "
                "source_artifact_version, created_by_id, title, status) VALUES "
                "(:org, :account, :content, :artifact, 3, :user, 'matrix', 'pending')"
            ),
            {
                "org": org_id,
                "account": account_id,
                "content": content_id,
                "artifact": source_id,
                "user": user_id,
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO content_schedule_entries "
                "(org_id, account_id, content_item_id, source_artifact_id, "
                "source_artifact_version, created_by_id, scheduled_at, timezone, status) "
                "VALUES (:org, :account, :content, :artifact, 3, :user, NOW(), 'UTC', 'planned')"
            ),
            {
                "org": org_id,
                "account": account_id,
                "content": content_id,
                "artifact": source_id,
                "user": user_id,
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO skill_stage_checkpoints "
                "(org_id, account_id, thread_id, turn_id, task_id, run_id, skill_run_id, "
                "step_key, stage_revision, status, skill_code, skill_version, "
                "dependency_graph_version, stage_contract_hash, input_snapshot, input_hash, "
                "output_snapshot, output_hash, source_artifact_refs, evidence_refs, "
                "reuse_policy, side_effect_level, manual_reconciliation_required, finalized_at) "
                "VALUES (:org, :account, :thread, :turn, :task, :run, :skill, 'matrix', 1, "
                "'completed', 'operation_iteration', 1, 'v1', :hash, CAST('{}' AS jsonb), "
                ":hash, CAST(:output AS jsonb), :hash, CAST(:refs AS jsonb), "
                "CAST(:refs AS jsonb), 'immutable', 'none', false, NOW())"
            ),
            {
                "org": org_id,
                "account": account_id,
                "thread": thread_id,
                "turn": turn_id,
                "task": task_id,
                "run": run_id,
                "skill": skill_id,
                "hash": "c" * 64,
                "output": payload({"artifact_id": source_id, "version": 3}),
                "refs": payload([{"artifact_id": source_id, "version": 3}]),
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO agent_tool_calls "
                "(org_id, task_id, skill_run_id, thread_id, turn_id, module, tool_code, "
                "tool_name, status, permission_mode, requires_human_confirmation, "
                "input_summary, output_summary, cost, meta) VALUES "
                "(:org, :task, :skill, :thread, :turn, 'brain', 'matrix', 'matrix', "
                "'success', 'auto', false, '', '', 0, CAST(:meta AS jsonb))"
            ),
            {
                "org": org_id,
                "task": task_id,
                "skill": skill_id,
                "thread": thread_id,
                "turn": turn_id,
                "meta": payload({"publish_receipt": {"artifact_id": source_id, "version": 3}}),
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO events (type, org_id, account_id, content_item_id, thread_id, "
                "turn_id, run_id, payload, idempotency_key) VALUES "
                "('matrix.reference', :org, :account, :content, :thread, :turn, :run, "
                "CAST(:payload AS jsonb), :key)"
            ),
            {
                "org": org_id,
                "account": account_id,
                "content": content_id,
                "thread": thread_id,
                "turn": turn_id,
                "run": run_id,
                "payload": payload({"artifact_id": source_id, "version": 3}),
                "key": f"matrix:{suffix}",
            },
        )

    command.upgrade(config, "20260804_0450")
    with engine.connect() as connection:
        assert connection.execute(
            sa.text(
                "SELECT agent_code, version FROM deliverables "
                "WHERE content_item_id=:content ORDER BY version"
            ),
            {"content": content_id},
        ).all() == [("00-decision", 1), ("02-content-director", 2), ("00-decision", 3)]
        checks = {
            "acceptance": connection.scalar(
                sa.text("SELECT version FROM deliverable_acceptances WHERE deliverable_id=:id"),
                {"id": source_id},
            ),
            "action": connection.scalar(
                sa.text(
                    "SELECT artifact_version FROM deliverable_action_executions "
                    "WHERE artifact_id=:id"
                ),
                {"id": source_id},
            ),
            "shoot": connection.scalar(
                sa.text(
                    "SELECT source_artifact_version FROM shoot_tasks WHERE source_artifact_id=:id"
                ),
                {"id": source_id},
            ),
            "schedule": connection.scalar(
                sa.text(
                    "SELECT source_artifact_version FROM content_schedule_entries "
                    "WHERE source_artifact_id=:id"
                ),
                {"id": source_id},
            ),
            "checkpoint": connection.scalar(
                sa.text(
                    "SELECT output_snapshot->>'version' FROM skill_stage_checkpoints "
                    "WHERE skill_run_id=:id"
                ),
                {"id": skill_id},
            ),
            "skill": connection.scalar(
                sa.text(
                    "SELECT output_snapshot->'operation_plan'->>'version' "
                    "FROM skill_runs WHERE id=:id"
                ),
                {"id": skill_id},
            ),
            "tool": connection.scalar(
                sa.text(
                    "SELECT meta->'publish_receipt'->>'version' FROM agent_tool_calls "
                    "WHERE skill_run_id=:id"
                ),
                {"id": skill_id},
            ),
            "event": connection.scalar(
                sa.text("SELECT payload->>'version' FROM events WHERE idempotency_key=:key"),
                {"key": f"matrix:{suffix}"},
            ),
        }
        assert checks == {
            "acceptance": 3,
            "action": 3,
            "shoot": 3,
            "schedule": 3,
            "checkpoint": "3",
            "skill": "3",
            "tool": "3",
            "event": "3",
        }

    command.downgrade(config, "20260804_0400")
    command.upgrade(config, "20260804_0450")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO deliverables "
                "(content_item_id, agent_code, type, version, status, payload) VALUES "
                "(:content, '02-content-director', 'review_report', 1, "
                "'pending_review', CAST(:payload AS jsonb))"
            ),
            {
                "content": content_id,
                "payload": payload({"task4c_downgrade_collision": True}),
            },
        )
    with pytest.raises(RuntimeError, match="cross-agent version collisions"):
        command.downgrade(config, "20260804_0400")
    with engine.connect() as connection:
        assert (
            connection.scalar(
                sa.text(
                    "SELECT count(*) FROM deliverables WHERE content_item_id=:content AND version=1"
                ),
                {"content": content_id},
            )
            == 2
        )
        unique = {
            tuple(item["column_names"])
            for item in sa.inspect(connection).get_unique_constraints("deliverables")
            if item["name"] == "uq_deliverable_version"
        }
        assert unique == {("content_item_id", "agent_code", "type", "version")}
    with engine.begin() as connection:
        connection.execute(
            sa.text("DELETE FROM deliverables WHERE payload @> CAST(:marker AS jsonb)"),
            {"marker": payload({"task4c_downgrade_collision": True})},
        )
    engine.dispose()


def test_run_revision_stage_checkpoint_sqlite_upgrade_downgrade_reupgrade(
    monkeypatch,
) -> None:
    migration = importlib.import_module(
        "migrations.versions.20260804_0400_run_revision_stage_checkpoints"
    )
    assert migration.down_revision == "20260804_0300"
    engine = sa.create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    metadata = sa.MetaData()
    sa.Table(
        "accounts",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False),
    )
    sa.Table(
        "conversation_threads",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("account_id", sa.Integer, nullable=False),
        sa.Column("org_id", sa.Integer, nullable=False),
        sa.UniqueConstraint("id", "org_id", name="uq_conversation_thread_id_org"),
    )
    sa.Table(
        "conversation_turns",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("target_turn_id", sa.Integer, nullable=True),
        sa.Column("thread_id", sa.Integer, nullable=False),
        sa.Column("org_id", sa.Integer, nullable=False),
        sa.UniqueConstraint("id", "thread_id", "org_id", name="uq_conversation_turn_id_thread_org"),
    )
    sa.Table(
        "brain_tasks",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False),
        sa.UniqueConstraint("id", "org_id", name="uq_brain_tasks_id_org"),
    )
    sa.Table(
        "agent_runs",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("task_id", sa.Integer, nullable=False),
        sa.Column("thread_id", sa.Integer, nullable=False),
        sa.Column("turn_id", sa.Integer, nullable=False),
        sa.Column("org_id", sa.Integer, nullable=False),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["conversation_threads.id"],
            name="fk_agent_runs_thread_id_conversation_threads",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["turn_id"],
            ["conversation_turns.id"],
            name="fk_agent_runs_turn_id_conversation_turns",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "id",
            "task_id",
            "thread_id",
            "turn_id",
            "org_id",
            name="uq_agent_runs_id_task_thread_turn_org",
        ),
    )
    sa.Table(
        "skill_runs",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("task_id", sa.Integer, nullable=False),
        sa.Column("run_id", sa.Integer, nullable=False),
        sa.Column("thread_id", sa.Integer, nullable=False),
        sa.Column("turn_id", sa.Integer, nullable=False),
        sa.UniqueConstraint(
            "id",
            "task_id",
            "run_id",
            "thread_id",
            "turn_id",
            name="uq_skill_runs_id_task_run_thread_turn",
        ),
    )

    with engine.begin() as connection:
        metadata.create_all(connection)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        migration.upgrade()
        inspector = sa.inspect(connection)
        assert {"run_revisions", "skill_stage_checkpoints"} <= set(inspector.get_table_names())
        assert "uq_accounts_id_org" in {
            item["name"] for item in inspector.get_unique_constraints("accounts")
        }
        assert "uq_conversation_thread_id_account_org" in {
            item["name"] for item in inspector.get_unique_constraints("conversation_threads")
        }
        assert "uq_conversation_turn_id_target_thread_org" in {
            item["name"] for item in inspector.get_unique_constraints("conversation_turns")
        }
        agent_run_foreign_keys = {
            item["name"]: item["options"].get("ondelete")
            for item in inspector.get_foreign_keys("agent_runs")
        }
        assert agent_run_foreign_keys["fk_agent_runs_thread_id_conversation_threads"] == "CASCADE"
        assert agent_run_foreign_keys["fk_agent_runs_turn_id_conversation_turns"] == "CASCADE"
        revision_foreign_keys = {
            item["name"]: item["options"].get("ondelete")
            for item in inspector.get_foreign_keys("run_revisions")
        }
        assert revision_foreign_keys["fk_run_revisions_source_skill_scope"] == "CASCADE"
        assert revision_foreign_keys["fk_run_revisions_revision_skill_scope"] == "CASCADE"
        assert (
            connection.scalar(
                sa.text(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' "
                    "AND name = 'trg_skill_stage_checkpoints_no_update'"
                )
            )
            == 1
        )
        connection.execute(sa.text("INSERT INTO accounts (id, org_id) VALUES (1, 1)"))
        connection.execute(
            sa.text("INSERT INTO conversation_threads (id, account_id, org_id) VALUES (1, 1, 1)")
        )
        connection.execute(
            sa.text(
                "INSERT INTO conversation_turns "
                "(id, target_turn_id, thread_id, org_id) VALUES "
                "(1, NULL, 1, 1), (2, 1, 1, 1)"
            )
        )
        connection.execute(sa.text("INSERT INTO brain_tasks (id, org_id) VALUES (1, 1)"))
        connection.execute(
            sa.text(
                "INSERT INTO agent_runs (id, task_id, thread_id, turn_id, org_id) "
                "VALUES (1, 1, 1, 1, 1), (2, 1, 1, 2, 1)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO skill_runs (id, task_id, run_id, thread_id, turn_id) "
                "VALUES (1, 1, 1, 1, 1), (2, 1, 2, 1, 2)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO run_revisions ("
                "id, org_id, account_id, thread_id, task_id, source_turn_id, "
                "source_run_id, source_skill_run_id, revision_turn_id, "
                "revision_run_id, revision_skill_run_id, mode, status, "
                "dependency_graph_version, earliest_affected_step, changed_constraints, "
                "direct_affected_steps, affected_steps, reused_steps, plan_hash"
                ") VALUES ("
                "1, 1, 1, 1, 1, 1, 1, 1, 2, 2, 2, 'partial', 'planned', "
                "'operation-loop/v1', 'script_generation', '{}', '[\"script_generation\"]', "
                "'[\"script_generation\"]', '[]', :plan_hash)"
            ),
            {"plan_hash": "a" * 64},
        )
        connection.execute(
            sa.text(
                "INSERT INTO skill_stage_checkpoints ("
                "id, org_id, account_id, thread_id, turn_id, task_id, run_id, "
                "skill_run_id, step_key, stage_revision, status, skill_code, "
                "skill_version, dependency_graph_version, stage_contract_hash, "
                "input_snapshot, input_hash, output_snapshot, output_hash, "
                "source_artifact_refs, evidence_refs, reuse_policy, side_effect_level, "
                "manual_reconciliation_required, finalized_at"
                ") VALUES ("
                "1, 1, 1, 1, 1, 1, 1, 1, 'script_generation', 1, 'completed', "
                "'operation_iteration', 1, 'operation-loop/v1', :contract_hash, "
                "'{}', :input_hash, '{}', :output_hash, '[]', '[]', 'immutable', "
                "'none', 0, CURRENT_TIMESTAMP)"
            ),
            {
                "contract_hash": "a" * 64,
                "input_hash": "b" * 64,
                "output_hash": "c" * 64,
            },
        )

        with pytest.raises(sa.exc.DatabaseError, match="immutable"):
            connection.execute(
                sa.text(
                    "UPDATE skill_stage_checkpoints SET output_hash = :output_hash WHERE id = 1"
                ),
                {"output_hash": "d" * 64},
            )

        connection.execute(sa.text("DELETE FROM skill_runs WHERE id IN (1, 2)"))
        assert connection.scalar(sa.text("SELECT COUNT(*) FROM run_revisions")) == 0
        assert connection.scalar(sa.text("SELECT COUNT(*) FROM skill_stage_checkpoints")) == 0
        connection.execute(sa.text("DELETE FROM agent_runs WHERE id IN (1, 2)"))
        deleted_turns = connection.execute(
            sa.text("DELETE FROM conversation_turns WHERE id IN (1, 2)")
        )
        connection.execute(sa.text("DELETE FROM conversation_threads WHERE id = 1"))
        assert deleted_turns.rowcount == 2
        assert connection.scalar(sa.text("SELECT COUNT(*) FROM agent_runs")) == 0
        assert connection.scalar(sa.text("SELECT COUNT(*) FROM conversation_turns")) == 0

        migration.downgrade()
        inspector = sa.inspect(connection)
        assert {"run_revisions", "skill_stage_checkpoints"}.isdisjoint(inspector.get_table_names())
        assert "uq_accounts_id_org" not in {
            item["name"] for item in inspector.get_unique_constraints("accounts")
        }
        assert {
            item["name"]: item["options"].get("ondelete")
            for item in inspector.get_foreign_keys("agent_runs")
        }["fk_agent_runs_thread_id_conversation_threads"] == "SET NULL"
        assert {
            item["name"]: item["options"].get("ondelete")
            for item in inspector.get_foreign_keys("agent_runs")
        }["fk_agent_runs_turn_id_conversation_turns"] == "SET NULL"

        migration.upgrade()
        assert {"run_revisions", "skill_stage_checkpoints"} <= set(
            sa.inspect(connection).get_table_names()
        )


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is required for the PostgreSQL migration gate",
)
def test_run_revision_stage_checkpoint_postgres_gate(monkeypatch) -> None:
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from sqlalchemy.orm import Session

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

    raw_url = os.environ["TEST_POSTGRES_URL"]
    async_url = raw_url.replace("postgresql+psycopg://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg://").replace(
        "postgresql://", "postgresql+psycopg://"
    )
    monkeypatch.setattr(settings, "database_url", async_url)
    config = Config("alembic.ini")
    engine = sa.create_engine(sync_url)
    hash_a = "a" * 64
    hash_b = "b" * 64
    hash_c = "c" * 64

    command.upgrade(config, "20260804_0400")
    try:
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
                "20260804_0400"
            )
            assert {"run_revisions", "skill_stage_checkpoints"} <= set(inspector.get_table_names())
            agent_run_fks = {
                item["name"]: item["options"].get("ondelete")
                for item in inspector.get_foreign_keys("agent_runs")
            }
            assert agent_run_fks["fk_agent_runs_thread_id_conversation_threads"] == "CASCADE"
            assert agent_run_fks["fk_agent_runs_turn_id_conversation_turns"] == "CASCADE"
            assert (
                connection.scalar(
                    sa.text(
                        "SELECT COUNT(*) FROM pg_trigger "
                        "WHERE tgname = 'trg_skill_stage_checkpoints_no_update' "
                        "AND NOT tgisinternal"
                    )
                )
                == 1
            )

        with Session(engine) as session:
            org = Org(name="checkpoint migration gate")
            user = User(
                org=org,
                email=f"checkpoint-migration-{uuid4().hex}@example.com",
                hashed_password="not-used-in-this-test",
                display_name="Checkpoint migration gate",
                role=UserRole.ADMIN,
            )
            session.add_all([org, user])
            session.flush()
            account = Account(
                org_id=org.id,
                platform=Platform.DOUYIN,
                nickname="checkpoint-migration-gate",
                status=AccountStatus.ACTIVE,
            )
            task = BrainTask(
                org_id=org.id,
                created_by_id=user.id,
                title="checkpoint migration gate",
                type=BrainTaskType.CONTENT_CREATION,
                status=BrainTaskStatus.RUNNING,
            )
            session.add_all([account, task])
            session.flush()
            thread = ConversationThread(
                org_id=org.id,
                created_by_id=user.id,
                account_id=account.id,
                title="checkpoint migration gate",
            )
            session.add(thread)
            session.flush()
            source_turn = ConversationTurn(
                thread_id=thread.id,
                org_id=org.id,
                created_by_id=user.id,
                client_message_id=f"source-{uuid4().hex}",
                user_input="source",
            )
            session.add(source_turn)
            session.flush()
            revision_turn = ConversationTurn(
                thread_id=thread.id,
                org_id=org.id,
                created_by_id=user.id,
                client_message_id=f"revision-{uuid4().hex}",
                user_input="revision",
                target_turn_id=source_turn.id,
                steering_mode="supplement",
            )
            session.add(revision_turn)
            session.flush()
            source_run = AgentRun(
                org_id=org.id,
                requested_by_id=user.id,
                task_id=task.id,
                thread_id=thread.id,
                turn_id=source_turn.id,
                client_message_id=f"source-{uuid4().hex}",
                status="completed",
                phase="completed",
                request_payload={},
                result_payload={},
            )
            revision_run = AgentRun(
                org_id=org.id,
                requested_by_id=user.id,
                task_id=task.id,
                thread_id=thread.id,
                turn_id=revision_turn.id,
                client_message_id=f"revision-{uuid4().hex}",
                status="waiting_predecessor",
                phase="waiting_predecessor",
                request_payload={},
                result_payload={},
            )
            session.add_all([source_run, revision_run])
            session.flush()
            source_skill = SkillRun(
                org_id=org.id,
                thread_id=thread.id,
                turn_id=source_turn.id,
                run_id=source_run.id,
                task_id=task.id,
                idempotency_key=f"source-{uuid4().hex}",
                skill_code="operation_iteration",
                skill_version=1,
                status="completed",
                input_snapshot={},
                input_hash=hash_a,
                output_snapshot={},
            )
            revision_skill = SkillRun(
                org_id=org.id,
                thread_id=thread.id,
                turn_id=revision_turn.id,
                run_id=revision_run.id,
                task_id=task.id,
                idempotency_key=f"revision-{uuid4().hex}",
                skill_code="operation_iteration",
                skill_version=1,
                status="running",
                input_snapshot={},
                input_hash=hash_a,
                output_snapshot={},
            )
            session.add_all([source_skill, revision_skill])
            session.flush()
            revision = RunRevision(
                org_id=org.id,
                account_id=account.id,
                thread_id=thread.id,
                task_id=task.id,
                source_turn_id=source_turn.id,
                source_run_id=source_run.id,
                source_skill_run_id=source_skill.id,
                revision_turn_id=revision_turn.id,
                revision_run_id=revision_run.id,
                revision_skill_run_id=revision_skill.id,
                mode="partial",
                status="planned",
                dependency_graph_version="operation-loop/v1",
                earliest_affected_step="script_generation",
                changed_constraints={},
                direct_affected_steps=["script_generation"],
                affected_steps=["script_generation"],
                reused_steps=[],
                plan_hash=hash_a,
            )
            session.add(revision)
            session.flush()
            source = SkillStageCheckpoint(
                org_id=org.id,
                account_id=account.id,
                thread_id=thread.id,
                turn_id=source_turn.id,
                task_id=task.id,
                run_id=source_run.id,
                skill_run_id=source_skill.id,
                step_key="script_generation",
                stage_revision=1,
                status="completed",
                skill_code="operation_iteration",
                skill_version=1,
                dependency_graph_version="operation-loop/v1",
                stage_contract_hash=hash_a,
                input_snapshot={"schema_version": 1, "data": {}},
                input_hash=hash_b,
                output_snapshot={"schema_version": 1, "data": {}},
                output_hash=hash_c,
                source_artifact_refs=[],
                evidence_refs=[],
                reuse_policy="immutable",
                side_effect_level="none",
                manual_reconciliation_required=False,
                finalized_at=datetime.now(UTC),
            )
            session.add(source)
            session.flush()

            reused_values = {
                "org_id": org.id,
                "account_id": account.id,
                "thread_id": thread.id,
                "turn_id": revision_turn.id,
                "task_id": task.id,
                "run_id": revision_run.id,
                "skill_run_id": revision_skill.id,
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
                "source_artifact_refs": [],
                "evidence_refs": [],
                "reuse_policy": "immutable",
                "side_effect_level": "none",
                "manual_reconciliation_required": False,
                "finalized_at": datetime.now(UTC),
            }

            def assert_checkpoint_rejected(
                values: dict,
                *,
                constraint_name: str | None = None,
            ) -> None:
                with pytest.raises(sa.exc.IntegrityError) as exc_info:
                    with session.begin_nested():
                        session.add(SkillStageCheckpoint(**values))
                        session.flush()
                if constraint_name is not None:
                    assert exc_info.value.orig.diag.constraint_name == constraint_name

            def reused_from(
                source_checkpoint: SkillStageCheckpoint,
                *,
                stage_revision: int = 1,
                **overrides,
            ) -> dict:
                values = reused_values | {
                    "step_key": source_checkpoint.step_key,
                    "stage_revision": stage_revision,
                    "stage_contract_hash": source_checkpoint.stage_contract_hash,
                    "input_snapshot": source_checkpoint.input_snapshot,
                    "input_hash": source_checkpoint.input_hash,
                    "output_hash": source_checkpoint.output_hash,
                    "source_stage_checkpoint_id": source_checkpoint.id,
                    "source_artifact_refs": source_checkpoint.source_artifact_refs,
                    "evidence_refs": source_checkpoint.evidence_refs,
                    "reuse_policy": source_checkpoint.reuse_policy,
                    "data_watermark_hash": source_checkpoint.data_watermark_hash,
                    "freshness_expires_at": source_checkpoint.freshness_expires_at,
                    "side_effect_level": source_checkpoint.side_effect_level,
                    "manual_reconciliation_required": (
                        source_checkpoint.manual_reconciliation_required
                    ),
                }
                values.update(overrides)
                return values

            def create_revision_scope(
                label: str,
                *,
                scope_org: Org,
                scope_user: User,
                scope_account: Account | None = None,
                scope_task: BrainTask | None = None,
                scope_thread: ConversationThread | None = None,
            ) -> dict:
                if scope_account is None:
                    scope_account = Account(
                        org_id=scope_org.id,
                        platform=Platform.DOUYIN,
                        nickname=f"checkpoint-{label}",
                        status=AccountStatus.ACTIVE,
                    )
                    session.add(scope_account)
                    session.flush()
                if scope_task is None:
                    scope_task = BrainTask(
                        org_id=scope_org.id,
                        created_by_id=scope_user.id,
                        title=f"checkpoint-{label}",
                        type=BrainTaskType.CONTENT_CREATION,
                        status=BrainTaskStatus.RUNNING,
                    )
                    session.add(scope_task)
                    session.flush()
                if scope_thread is None:
                    scope_thread = ConversationThread(
                        org_id=scope_org.id,
                        created_by_id=scope_user.id,
                        account_id=scope_account.id,
                        title=f"checkpoint-{label}",
                    )
                    session.add(scope_thread)
                    session.flush()
                scoped_source_turn = ConversationTurn(
                    thread_id=scope_thread.id,
                    org_id=scope_org.id,
                    created_by_id=scope_user.id,
                    client_message_id=f"source-{label}-{uuid4().hex}",
                    user_input="source",
                )
                session.add(scoped_source_turn)
                session.flush()
                scoped_revision_turn = ConversationTurn(
                    thread_id=scope_thread.id,
                    org_id=scope_org.id,
                    created_by_id=scope_user.id,
                    client_message_id=f"revision-{label}-{uuid4().hex}",
                    user_input="revision",
                    target_turn_id=scoped_source_turn.id,
                    steering_mode="supplement",
                )
                session.add(scoped_revision_turn)
                session.flush()
                scoped_source_run = AgentRun(
                    org_id=scope_org.id,
                    requested_by_id=scope_user.id,
                    task_id=scope_task.id,
                    thread_id=scope_thread.id,
                    turn_id=scoped_source_turn.id,
                    client_message_id=f"source-{label}-{uuid4().hex}",
                    status="completed",
                    phase="completed",
                    request_payload={},
                    result_payload={},
                )
                scoped_revision_run = AgentRun(
                    org_id=scope_org.id,
                    requested_by_id=scope_user.id,
                    task_id=scope_task.id,
                    thread_id=scope_thread.id,
                    turn_id=scoped_revision_turn.id,
                    client_message_id=f"revision-{label}-{uuid4().hex}",
                    status="waiting_predecessor",
                    phase="waiting_predecessor",
                    request_payload={},
                    result_payload={},
                )
                session.add_all([scoped_source_run, scoped_revision_run])
                session.flush()
                scoped_source_skill = SkillRun(
                    org_id=scope_org.id,
                    thread_id=scope_thread.id,
                    turn_id=scoped_source_turn.id,
                    run_id=scoped_source_run.id,
                    task_id=scope_task.id,
                    idempotency_key=f"source-{label}-{uuid4().hex}",
                    skill_code="operation_iteration",
                    skill_version=1,
                    status="completed",
                    input_snapshot={},
                    input_hash=hash_a,
                    output_snapshot={},
                )
                scoped_revision_skill = SkillRun(
                    org_id=scope_org.id,
                    thread_id=scope_thread.id,
                    turn_id=scoped_revision_turn.id,
                    run_id=scoped_revision_run.id,
                    task_id=scope_task.id,
                    idempotency_key=f"revision-{label}-{uuid4().hex}",
                    skill_code="operation_iteration",
                    skill_version=1,
                    status="running",
                    input_snapshot={},
                    input_hash=hash_a,
                    output_snapshot={},
                )
                session.add_all([scoped_source_skill, scoped_revision_skill])
                session.flush()
                scoped_revision = RunRevision(
                    org_id=scope_org.id,
                    account_id=scope_account.id,
                    thread_id=scope_thread.id,
                    task_id=scope_task.id,
                    source_turn_id=scoped_source_turn.id,
                    source_run_id=scoped_source_run.id,
                    source_skill_run_id=scoped_source_skill.id,
                    revision_turn_id=scoped_revision_turn.id,
                    revision_run_id=scoped_revision_run.id,
                    revision_skill_run_id=scoped_revision_skill.id,
                    mode="partial",
                    status="planned",
                    dependency_graph_version="operation-loop/v1",
                    earliest_affected_step="script_generation",
                    changed_constraints={},
                    direct_affected_steps=["script_generation"],
                    affected_steps=["script_generation"],
                    reused_steps=[],
                    plan_hash=hash_a,
                )
                session.add(scoped_revision)
                session.flush()
                scoped_checkpoint = SkillStageCheckpoint(
                    org_id=scope_org.id,
                    account_id=scope_account.id,
                    thread_id=scope_thread.id,
                    turn_id=scoped_source_turn.id,
                    task_id=scope_task.id,
                    run_id=scoped_source_run.id,
                    skill_run_id=scoped_source_skill.id,
                    step_key=source.step_key,
                    stage_revision=1,
                    status="completed",
                    skill_code=source.skill_code,
                    skill_version=source.skill_version,
                    dependency_graph_version=source.dependency_graph_version,
                    stage_contract_hash=source.stage_contract_hash,
                    input_snapshot=source.input_snapshot,
                    input_hash=source.input_hash,
                    output_snapshot=source.output_snapshot,
                    output_hash=source.output_hash,
                    source_artifact_refs=[],
                    evidence_refs=[],
                    reuse_policy=source.reuse_policy,
                    side_effect_level=source.side_effect_level,
                    manual_reconciliation_required=False,
                    finalized_at=datetime.now(UTC),
                )
                session.add(scoped_checkpoint)
                session.flush()
                return {
                    "turn_ids": [scoped_source_turn.id, scoped_revision_turn.id],
                    "run_ids": [scoped_source_run.id, scoped_revision_run.id],
                    "skill_ids": [scoped_source_skill.id, scoped_revision_skill.id],
                    "source_checkpoint": scoped_checkpoint,
                }

            other_org = Org(name="other checkpoint migration org")
            other_user = User(
                org=other_org,
                email=f"other-checkpoint-{uuid4().hex}@example.com",
                hashed_password="not-used-in-this-test",
                display_name="Other checkpoint migration user",
                role=UserRole.ADMIN,
            )
            session.add_all([other_org, other_user])
            session.flush()
            cross_org_scope = create_revision_scope(
                "cross-org",
                scope_org=other_org,
                scope_user=other_user,
            )
            cross_account_scope = create_revision_scope(
                "cross-account",
                scope_org=org,
                scope_user=user,
                scope_task=task,
            )
            cross_task_scope = create_revision_scope(
                "cross-task",
                scope_org=org,
                scope_user=user,
                scope_account=account,
                scope_thread=thread,
            )
            cross_thread_scope = create_revision_scope(
                "cross-thread",
                scope_org=org,
                scope_user=user,
                scope_account=account,
                scope_task=task,
            )
            cross_source_scopes = (
                cross_org_scope,
                cross_account_scope,
                cross_task_scope,
                cross_thread_scope,
            )
            for cross_scope in cross_source_scopes:
                assert_checkpoint_rejected(
                    reused_values
                    | {
                        "stage_revision": 2,
                        "source_stage_checkpoint_id": (cross_scope["source_checkpoint"].id),
                    },
                    constraint_name="fk_stage_checkpoints_source_compatibility",
                )

            assert_checkpoint_rejected(
                reused_values
                | {
                    "stage_revision": 2,
                    "source_stage_checkpoint_id": 9_999_999,
                }
            )
            assert_checkpoint_rejected(reused_values | {"stage_revision": 2, "input_hash": hash_a})

            for cross_scope in cross_source_scopes:
                session.execute(
                    sa.delete(SkillRun).where(SkillRun.id.in_(cross_scope["skill_ids"]))
                )
                session.execute(sa.delete(AgentRun).where(AgentRun.id.in_(cross_scope["run_ids"])))
                session.execute(
                    sa.delete(ConversationTurn).where(
                        ConversationTurn.id.in_(cross_scope["turn_ids"])
                    )
                )

            completed_values = {
                "org_id": org.id,
                "account_id": account.id,
                "thread_id": thread.id,
                "turn_id": source_turn.id,
                "task_id": task.id,
                "run_id": source_run.id,
                "skill_run_id": source_skill.id,
                "step_key": "missing_output",
                "stage_revision": 1,
                "status": "completed",
                "skill_code": "operation_iteration",
                "skill_version": 1,
                "dependency_graph_version": "operation-loop/v1",
                "stage_contract_hash": hash_a,
                "input_snapshot": {"schema_version": 1, "data": {}},
                "input_hash": hash_b,
                "output_snapshot": None,
                "output_hash": hash_c,
                "source_artifact_refs": [],
                "evidence_refs": [],
                "reuse_policy": "immutable",
                "side_effect_level": "none",
                "manual_reconciliation_required": False,
                "finalized_at": datetime.now(UTC),
            }
            assert_checkpoint_rejected(completed_values)
            assert_checkpoint_rejected(
                reused_values
                | {
                    "stage_revision": 2,
                    "output_snapshot": {"schema_version": 1, "data": {}},
                }
            )

            expires_at = datetime.now(UTC) + timedelta(hours=1)
            freshness_source = SkillStageCheckpoint(
                **(
                    completed_values
                    | {
                        "step_key": "freshness_step",
                        "output_snapshot": {"schema_version": 1, "data": {}},
                        "reuse_policy": "freshness_bound",
                        "data_watermark_hash": hash_a,
                        "freshness_expires_at": expires_at,
                    }
                )
            )
            never_source = SkillStageCheckpoint(
                **(
                    completed_values
                    | {
                        "step_key": "never_step",
                        "output_snapshot": {"schema_version": 1, "data": {}},
                        "reuse_policy": "never",
                    }
                )
            )
            non_idempotent_source = SkillStageCheckpoint(
                **(
                    completed_values
                    | {
                        "step_key": "non_idempotent_step",
                        "output_snapshot": {"schema_version": 1, "data": {}},
                        "reuse_policy": "never",
                        "side_effect_level": "non_idempotent_write",
                    }
                )
            )
            manual_source = SkillStageCheckpoint(
                **(
                    completed_values
                    | {
                        "step_key": "manual_step",
                        "output_snapshot": {"schema_version": 1, "data": {}},
                        "reuse_policy": "never",
                        "manual_reconciliation_required": True,
                    }
                )
            )
            session.add_all(
                [
                    freshness_source,
                    never_source,
                    non_idempotent_source,
                    manual_source,
                ]
            )
            session.flush()

            freshness_values = reused_from(
                freshness_source,
                freshness_validated_at=datetime.now(UTC),
            )
            assert_checkpoint_rejected(freshness_values | {"data_watermark_hash": hash_b})
            assert_checkpoint_rejected(
                freshness_values | {"freshness_expires_at": expires_at + timedelta(seconds=1)}
            )
            assert_checkpoint_rejected(
                freshness_values | {"freshness_validated_at": expires_at + timedelta(seconds=1)}
            )
            for unsafe_source in (
                never_source,
                non_idempotent_source,
                manual_source,
            ):
                assert_checkpoint_rejected(reused_from(unsafe_source))

            reused = SkillStageCheckpoint(**reused_values)
            session.add(reused)
            session.flush()
            resolved_source = session.get(
                SkillStageCheckpoint,
                reused.source_stage_checkpoint_id,
            )
            assert resolved_source is not None
            assert resolved_source.status == "completed"
            assert resolved_source.source_stage_checkpoint_id is None
            assert_checkpoint_rejected(reused_from(reused, stage_revision=2))
            session.commit()
            checkpoint_id = source.id
            org_id = org.id
            thread_id = thread.id

            with pytest.raises(sa.exc.DBAPIError, match="immutable"):
                session.execute(
                    sa.text(
                        "UPDATE skill_stage_checkpoints SET output_hash = :hash "
                        "WHERE id = :checkpoint_id"
                    ),
                    {"hash": hash_a, "checkpoint_id": checkpoint_id},
                )
            session.rollback()

            deleted_skills = session.execute(
                sa.delete(SkillRun).where(SkillRun.thread_id == thread_id)
            )
            deleted_runs = session.execute(
                sa.delete(AgentRun).where(AgentRun.thread_id == thread_id)
            )
            deleted_turns = session.execute(
                sa.delete(ConversationTurn).where(ConversationTurn.thread_id == thread_id)
            )
            deleted_thread = session.execute(
                sa.delete(ConversationThread).where(ConversationThread.id == thread_id)
            )
            session.commit()
            assert deleted_skills.rowcount == 2
            assert deleted_runs.rowcount == 2
            assert deleted_turns.rowcount == 2
            assert deleted_thread.rowcount == 1
            assert (
                session.scalar(
                    sa.select(sa.func.count(ConversationTurn.id)).where(
                        ConversationTurn.thread_id == thread_id
                    )
                )
                == 0
            )
            assert session.scalar(sa.select(sa.func.count(SkillStageCheckpoint.id))) == 0
            assert session.scalar(sa.select(sa.func.count(RunRevision.id))) == 0
            assert (
                session.scalar(
                    sa.select(sa.func.count(AgentRun.id)).where(AgentRun.thread_id == thread_id)
                )
                == 0
            )
            assert (
                session.scalar(
                    sa.select(sa.func.count(SkillRun.id)).where(SkillRun.thread_id == thread_id)
                )
                == 0
            )
            assert session.get(ConversationThread, thread_id) is None

            session.execute(
                sa.text("DELETE FROM orgs WHERE id IN (:org_id, :other_org_id)"),
                {"org_id": org_id, "other_org_id": other_org.id},
            )
            session.commit()

        command.downgrade(config, "20260804_0300")
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            assert {"run_revisions", "skill_stage_checkpoints"}.isdisjoint(
                inspector.get_table_names()
            )
            assert (
                connection.scalar(
                    sa.text(
                        "SELECT COUNT(*) FROM pg_proc "
                        "WHERE proname = 'fn_skill_stage_checkpoints_no_update'"
                    )
                )
                == 0
            )
            assert {
                item["name"]: item["options"].get("ondelete")
                for item in inspector.get_foreign_keys("agent_runs")
            }["fk_agent_runs_thread_id_conversation_threads"] == "SET NULL"

        command.upgrade(config, "20260804_0400")
        with engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
                "20260804_0400"
            )
            assert (
                connection.scalar(
                    sa.text(
                        "SELECT COUNT(*) FROM pg_trigger "
                        "WHERE tgname = 'trg_skill_stage_checkpoints_no_update' "
                        "AND NOT tgisinternal"
                    )
                )
                == 1
            )
    finally:
        engine.dispose()


def test_turn_steering_migration_adds_scoped_lineage_and_is_reversible(
    monkeypatch,
) -> None:
    migration = importlib.import_module("migrations.versions.20260804_0300_turn_steering")
    assert migration.down_revision == "20260804_0200"
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    sa.Table(
        "conversation_turns",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("thread_id", sa.Integer, nullable=False),
        sa.Column("org_id", sa.Integer, nullable=False),
        sa.UniqueConstraint(
            "id",
            "thread_id",
            "org_id",
            name="uq_conversation_turn_id_thread_org",
        ),
    )

    with engine.begin() as connection:
        metadata.create_all(connection)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()
        inspector = sa.inspect(connection)
        columns = {item["name"] for item in inspector.get_columns("conversation_turns")}
        assert {"target_turn_id", "steering_mode"} <= columns
        foreign_keys = {
            item["name"]: (
                tuple(item["constrained_columns"]),
                tuple(item["referred_columns"]),
            )
            for item in inspector.get_foreign_keys("conversation_turns")
        }
        assert foreign_keys["fk_conversation_turn_target_turn_thread_org"] == (
            ("target_turn_id", "thread_id", "org_id"),
            ("id", "thread_id", "org_id"),
        )
        checks = {item["name"] for item in inspector.get_check_constraints("conversation_turns")}
        assert {
            "ck_conversation_turn_target_not_self",
            "ck_conversation_turns_steering_lineage",
        } <= checks

        migration.downgrade()
        columns = {
            item["name"] for item in sa.inspect(connection).get_columns("conversation_turns")
        }
        assert {"target_turn_id", "steering_mode"}.isdisjoint(columns)


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is required for the PostgreSQL migration gate",
)
def test_turn_steering_postgres_upgrade_and_downgrade_gate(monkeypatch) -> None:
    raw_url = os.environ["TEST_POSTGRES_URL"]
    async_url = raw_url.replace("postgresql+psycopg://", "postgresql+asyncpg://").replace(
        "postgresql://",
        "postgresql+asyncpg://",
    )
    sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg://").replace(
        "postgresql://",
        "postgresql+psycopg://",
    )
    monkeypatch.setattr(settings, "database_url", async_url)
    config = Config("alembic.ini")

    command.upgrade(config, "20260804_0200")
    command.upgrade(config, "20260804_0300")
    engine = sa.create_engine(sync_url)
    try:
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
                "20260804_0300"
            )
            assert {"target_turn_id", "steering_mode"} <= {
                item["name"] for item in inspector.get_columns("conversation_turns")
            }
            assert "fk_conversation_turn_target_turn_thread_org" in {
                item["name"] for item in inspector.get_foreign_keys("conversation_turns")
            }

        command.downgrade(config, "20260804_0200")
        with engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
                "20260804_0200"
            )
            assert {"target_turn_id", "steering_mode"}.isdisjoint(
                {item["name"] for item in sa.inspect(connection).get_columns("conversation_turns")}
            )
    finally:
        engine.dispose()


def test_deliverable_actions_migration_creates_real_resources_and_is_reversible(
    monkeypatch,
) -> None:
    migration = importlib.import_module("migrations.versions.20260804_0200_deliverable_actions")
    assert migration.down_revision == "20260804_0100"
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    for table_name in ("orgs", "accounts", "users", "content_items", "deliverables"):
        sa.Table(table_name, metadata, sa.Column("id", sa.Integer, primary_key=True))

    with engine.begin() as connection:
        metadata.create_all(connection)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()
        inspector = sa.inspect(connection)
        assert {
            "deliverable_action_executions",
            "shoot_tasks",
            "content_schedule_entries",
        } <= set(inspector.get_table_names())
        unique_names = {
            item["name"]
            for item in inspector.get_unique_constraints("deliverable_action_executions")
        }
        assert "uq_deliverable_action_idempotency" in unique_names

        migration.downgrade()
        inspector = sa.inspect(connection)
        assert {
            "deliverable_action_executions",
            "shoot_tasks",
            "content_schedule_entries",
        }.isdisjoint(inspector.get_table_names())


def test_scoped_turn_events_migration_backfills_only_inferable_scope_and_is_reversible(
    monkeypatch,
) -> None:
    migration = importlib.import_module("migrations.versions.20260804_0100_scope_turn_events")
    assert migration.down_revision == "20260803_0400"

    engine = sa.create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    metadata = sa.MetaData()
    orgs = sa.Table("orgs", metadata, sa.Column("id", sa.Integer, primary_key=True))
    accounts = sa.Table(
        "accounts",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False),
    )
    threads = sa.Table(
        "conversation_threads",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False),
        sa.Column("account_id", sa.Integer, nullable=False),
    )
    turns = sa.Table(
        "conversation_turns",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("thread_id", sa.Integer, nullable=False),
        sa.Column("org_id", sa.Integer, nullable=False),
    )
    events = sa.Table(
        "events",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("type", sa.String(length=128), nullable=False),
        sa.Column("thread_id", sa.Integer, nullable=True),
        sa.Column("turn_id", sa.Integer, nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_events_idempotency_key"),
    )

    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(orgs.insert(), [{"id": 1}, {"id": 2}])
        connection.execute(
            accounts.insert(),
            [{"id": 10, "org_id": 1}, {"id": 20, "org_id": 2}],
        )
        connection.execute(
            threads.insert(),
            [
                {"id": 100, "org_id": 1, "account_id": 10},
                {"id": 200, "org_id": 2, "account_id": 20},
            ],
        )
        connection.execute(
            turns.insert(),
            [
                {"id": 1000, "thread_id": 100, "org_id": 1},
                {"id": 1001, "thread_id": 100, "org_id": 1},
                {"id": 2000, "thread_id": 200, "org_id": 2},
            ],
        )
        connection.execute(
            events.insert(),
            [
                {
                    "id": 1,
                    "type": "turn.received",
                    "thread_id": 100,
                    "turn_id": 1000,
                    "idempotency_key": "turn-1000-received",
                },
                {
                    "id": 2,
                    "type": "thread.legacy",
                    "thread_id": 100,
                    "turn_id": None,
                    "idempotency_key": None,
                },
                {
                    "id": 3,
                    "type": "step.completed",
                    "thread_id": None,
                    "turn_id": 1000,
                    "idempotency_key": "turn-1000-completed",
                },
                {
                    "id": 4,
                    "type": "legacy.unscoped",
                    "thread_id": None,
                    "turn_id": None,
                    "idempotency_key": None,
                },
                {
                    "id": 5,
                    "type": "legacy.unknown-thread",
                    "thread_id": 999,
                    "turn_id": None,
                    "idempotency_key": None,
                },
                {
                    "id": 6,
                    "type": "turn.received",
                    "thread_id": 200,
                    "turn_id": 2000,
                    "idempotency_key": "turn-2000-received",
                },
                {
                    "id": 7,
                    "type": "legacy.conflicting-scope",
                    "thread_id": 200,
                    "turn_id": 1000,
                    "idempotency_key": None,
                },
            ],
        )
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        migration.upgrade()

        inspector = sa.inspect(connection)
        event_columns = {column["name"]: column for column in inspector.get_columns("events")}
        turn_columns = {
            column["name"]: column for column in inspector.get_columns("conversation_turns")
        }
        assert {"org_id", "account_id", "sequence"} <= event_columns.keys()
        assert all(event_columns[name]["nullable"] for name in ("org_id", "account_id", "sequence"))
        assert turn_columns["next_event_sequence"]["nullable"] is False
        assert str(turn_columns["next_event_sequence"]["default"]).strip("'()") == "1"
        assert connection.execute(
            sa.text("SELECT id, org_id, account_id, sequence FROM events ORDER BY id")
        ).all() == [
            (1, 1, 10, 1),
            (2, 1, 10, None),
            (3, 1, 10, 2),
            (4, None, None, None),
            (5, None, None, None),
            (6, 2, 20, 1),
            (7, None, None, None),
        ]
        assert connection.execute(
            sa.text("SELECT id, next_event_sequence FROM conversation_turns ORDER BY id")
        ).all() == [(1000, 3), (1001, 1), (2000, 2)]

        indexes = {index["name"]: index for index in inspector.get_indexes("events")}
        sequence_index = indexes["uq_events_turn_sequence"]
        assert sequence_index["unique"] == 1
        assert sequence_index["column_names"] == ["turn_id", "sequence"]
        assert str(sequence_index["dialect_options"]["sqlite_where"]) == (
            "turn_id IS NOT NULL AND sequence IS NOT NULL"
        )
        foreign_keys = {item["name"]: item for item in inspector.get_foreign_keys("events")}
        assert foreign_keys["fk_events_org_id_orgs"]["referred_table"] == "orgs"
        assert foreign_keys["fk_events_org_id_orgs"]["options"]["ondelete"] == "CASCADE"
        assert foreign_keys["fk_events_account_id_accounts"]["referred_table"] == "accounts"
        assert foreign_keys["fk_events_account_id_accounts"]["options"]["ondelete"] == "CASCADE"

        connection.execute(
            sa.text(
                "INSERT INTO events "
                "(id, type, org_id, account_id, turn_id, sequence) "
                "VALUES (8, 'same-sequence-other-turn', 1, 10, 1001, 1)"
            )
        )
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO events "
                    "(id, type, org_id, account_id, turn_id, sequence) "
                    "VALUES (9, 'duplicate-sequence', 1, 10, 1000, 1)"
                )
            )
        connection.execute(
            sa.text("INSERT INTO events (id, type) VALUES (10, 'another-legacy-event')")
        )
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO events (id, type, idempotency_key) "
                    "VALUES (11, 'duplicate-key', 'turn-1000-received')"
                )
            )

        migration.downgrade()

        inspector = sa.inspect(connection)
        assert {"org_id", "account_id", "sequence"}.isdisjoint(
            column["name"] for column in inspector.get_columns("events")
        )
        assert "next_event_sequence" not in {
            column["name"] for column in inspector.get_columns("conversation_turns")
        }
        assert "uq_events_idempotency_key" in {
            item["name"] for item in inspector.get_unique_constraints("events")
        }
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO events (id, type, idempotency_key) "
                    "VALUES (12, 'duplicate-key-after-downgrade', 'turn-1000-received')"
                )
            )


def test_turn_tool_call_count_migration_is_linear_and_reversible(monkeypatch) -> None:
    migration = importlib.import_module("migrations.versions.20260803_0400_turn_tool_call_count")
    assert migration.down_revision == "20260803_0300"
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    sa.Table(
        "conversation_turns",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
    )

    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(sa.text("INSERT INTO conversation_turns (id) VALUES (1)"))
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        migration.upgrade()

        inspector = sa.inspect(connection)
        columns = {column["name"] for column in inspector.get_columns("conversation_turns")}
        checks = {
            constraint["name"]
            for constraint in inspector.get_check_constraints("conversation_turns")
        }
        assert "tool_call_count" in columns
        assert "ck_conversation_turns_tool_call_count" in checks
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text("UPDATE conversation_turns SET tool_call_count = -1 WHERE id = 1")
            )

        migration.downgrade()
        assert "tool_call_count" not in {
            column["name"] for column in sa.inspect(connection).get_columns("conversation_turns")
        }


def test_minimal_audit_records_migration_is_linear_and_reversible(monkeypatch) -> None:
    migration = importlib.import_module("migrations.versions.20260803_0300_minimal_audit_records")
    assert migration.down_revision == "20260803_0200"
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    sa.Table("orgs", metadata, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table("accounts", metadata, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table("users", metadata, sa.Column("id", sa.Integer, primary_key=True))

    with engine.begin() as connection:
        metadata.create_all(connection)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()

        inspector = sa.inspect(connection)
        assert inspector.has_table("audit_records")
        columns = {column["name"] for column in inspector.get_columns("audit_records")}
        assert {
            "org_id",
            "account_id",
            "actor_user_id",
            "category",
            "action",
            "outcome",
            "amount_usd",
            "details",
            "occurred_at",
        } <= columns
        assert {"thread_id", "turn_id", "run_id", "skill_run_id", "prompt"}.isdisjoint(columns)

        migration.downgrade()
        assert sa.inspect(connection).has_table("audit_records") is False


def test_douyin_account_metric_exports_migration_is_linear_and_reversible() -> None:
    migration = importlib.import_module(
        "migrations.versions.20260731_0300_douyin_account_metric_exports"
    )

    assert migration.down_revision == "20260731_0200"
    upgrade_source = inspect.getsource(migration.upgrade)
    downgrade_source = inspect.getsource(migration.downgrade)
    for column_name in {
        "profile_visit_count",
        "unfollow_count",
        "like_count",
        "comment_count",
        "share_count",
        "cover_click_rate",
    }:
        assert column_name in upgrade_source
        assert column_name in downgrade_source


def test_offline_migrations_fail_fast_for_data_dependent_chain() -> None:
    source = __import__("pathlib").Path("migrations/env.py").read_text(encoding="utf-8")
    assert "CommandError" in source
    assert "offline SQL is unsupported for data-dependent migrations" in source


def test_turn_provenance_migration_is_additive_and_reversible(monkeypatch) -> None:
    migration = importlib.import_module("migrations.versions.20260728_0300_turn_provenance")
    assert migration.down_revision == "20260728_0200"

    engine = sa.create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    metadata = sa.MetaData()
    conversation_threads = sa.Table(
        "conversation_threads",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
    )
    conversation_turns = sa.Table(
        "conversation_turns",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
    )
    agent_runs = sa.Table(
        "agent_runs",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
    )
    skill_runs = sa.Table(
        "skill_runs",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
    )
    deliverables = sa.Table(
        "deliverables",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    ledger_tables = {}
    for table_name in (
        "strategy_plans",
        "decision_traces",
        "reflection_records",
        "agent_quality_scores",
    ):
        ledger_tables[table_name] = sa.Table(
            table_name,
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column(
                "run_id",
                sa.Integer,
                sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
                index=True,
                nullable=True,
            ),
        )
    events = sa.Table(
        "events",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("type", sa.String(length=128), nullable=False),
    )

    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(conversation_threads.insert(), [{"id": 10}])
        connection.execute(conversation_turns.insert(), [{"id": 20}])
        connection.execute(agent_runs.insert(), [{"id": 30}, {"id": 31}])
        connection.execute(skill_runs.insert(), [{"id": 40}])
        connection.execute(
            deliverables.insert(),
            [{"id": 1, "payload": {"legacy": True}}],
        )
        for table in ledger_tables.values():
            connection.execute(table.insert(), [{"id": 1, "run_id": 31}])
        connection.execute(events.insert(), [{"id": 1, "type": "legacy.event"}])

        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(migration, "op", operations)
        migration.upgrade()

        task7_columns = {"thread_id", "turn_id", "run_id", "skill_run_id"}
        all_tables = ("deliverables", *ledger_tables, "events")
        for table_name in all_tables:
            inspector = sa.inspect(connection)
            columns = {column["name"]: column for column in inspector.get_columns(table_name)}
            assert task7_columns <= columns.keys()
            for column_name in task7_columns:
                assert columns[column_name]["nullable"] is True

            indexes = {tuple(index["column_names"]) for index in inspector.get_indexes(table_name)}
            for column_name in task7_columns:
                assert (column_name,) in indexes

            foreign_keys = {
                tuple(item["constrained_columns"]): item
                for item in inspector.get_foreign_keys(table_name)
            }
            for column_name, source_table in (
                ("thread_id", "conversation_threads"),
                ("turn_id", "conversation_turns"),
                ("run_id", "agent_runs"),
                ("skill_run_id", "skill_runs"),
            ):
                foreign_key = foreign_keys[(column_name,)]
                assert foreign_key["referred_table"] == source_table
                assert foreign_key["options"]["ondelete"] == "SET NULL"

        assert connection.execute(
            sa.select(deliverables.c.payload).where(deliverables.c.id == 1)
        ).scalar_one() == {"legacy": True}
        assert connection.execute(
            sa.text(
                "SELECT thread_id, turn_id, run_id, skill_run_id FROM deliverables WHERE id = 1"
            )
        ).one() == (None, None, None, None)
        for table_name in ledger_tables:
            assert connection.execute(
                sa.text(
                    f"SELECT run_id, thread_id, turn_id, skill_run_id "
                    f"FROM {table_name} WHERE id = 1"
                )
            ).one() == (31, None, None, None)

        migration.downgrade()
        for table_name in ledger_tables:
            columns = {column["name"] for column in sa.inspect(connection).get_columns(table_name)}
            assert "run_id" in columns
            assert {"thread_id", "turn_id", "skill_run_id"}.isdisjoint(columns)
            assert (
                connection.execute(
                    sa.text(f"SELECT run_id FROM {table_name} WHERE id = 1")
                ).scalar_one()
                == 31
            )
        for table_name in ("deliverables", "events"):
            columns = {column["name"] for column in sa.inspect(connection).get_columns(table_name)}
            assert task7_columns.isdisjoint(columns)

        migration.upgrade()
        for table_name in all_tables:
            columns = {column["name"] for column in sa.inspect(connection).get_columns(table_name)}
            assert task7_columns <= columns

        for table_name in all_tables:
            existing_columns = {
                column["name"] for column in sa.inspect(connection).get_columns(table_name)
            }
            values = {
                "id": 2,
                "thread_id": 10,
                "turn_id": 20,
                "run_id": 30,
                "skill_run_id": 40,
            }
            if "payload" in existing_columns:
                values["payload"] = {"traceable": True}
            if "type" in existing_columns:
                values["type"] = "traceable.event"
            reflected_table = sa.Table(
                table_name,
                sa.MetaData(),
                autoload_with=connection,
            )
            connection.execute(reflected_table.insert(), values)

        connection.execute(skill_runs.delete().where(skill_runs.c.id == 40))
        connection.execute(agent_runs.delete().where(agent_runs.c.id == 30))
        connection.execute(conversation_turns.delete().where(conversation_turns.c.id == 20))
        connection.execute(conversation_threads.delete().where(conversation_threads.c.id == 10))
        for table_name in all_tables:
            assert connection.execute(
                sa.text(
                    f"SELECT thread_id, turn_id, run_id, skill_run_id "
                    f"FROM {table_name} WHERE id = 2"
                )
            ).one() == (None, None, None, None)


def test_skill_runs_migration_preserves_legacy_runtime_rows(monkeypatch) -> None:
    migration = importlib.import_module("migrations.versions.20260728_0200_skill_runs")
    assert migration.down_revision == "20260728_0175"

    engine = sa.create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    metadata = sa.MetaData()
    orgs = sa.Table("orgs", metadata, sa.Column("id", sa.Integer, primary_key=True))
    conversation_threads = sa.Table(
        "conversation_threads",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False),
        sa.UniqueConstraint("id", "org_id", name="uq_conversation_thread_id_org"),
    )
    conversation_turns = sa.Table(
        "conversation_turns",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("thread_id", sa.Integer, nullable=False),
        sa.Column("org_id", sa.Integer, nullable=False),
        sa.UniqueConstraint(
            "id",
            "thread_id",
            "org_id",
            name="uq_conversation_turn_id_thread_org",
        ),
    )
    brain_tasks = sa.Table(
        "brain_tasks",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
    )
    agent_runs = sa.Table(
        "agent_runs",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False),
        sa.Column("thread_id", sa.Integer, nullable=True),
        sa.Column("turn_id", sa.Integer, nullable=True),
    )
    agent_invocations = sa.Table(
        "agent_invocations",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("status", sa.String(length=40), nullable=False),
    )
    agent_tool_calls = sa.Table(
        "agent_tool_calls",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("status", sa.String(length=40), nullable=False),
    )

    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(orgs.insert(), [{"id": 1}])
        connection.execute(
            conversation_threads.insert(),
            [{"id": 10, "org_id": 1}, {"id": 11, "org_id": 1}],
        )
        connection.execute(
            conversation_turns.insert(),
            [
                {"id": 20, "thread_id": 10, "org_id": 1},
                {"id": 21, "thread_id": 11, "org_id": 1},
            ],
        )
        connection.execute(brain_tasks.insert(), [{"id": 30}])
        connection.execute(
            agent_runs.insert(),
            [
                {
                    "id": 40,
                    "org_id": 1,
                    "thread_id": None,
                    "turn_id": None,
                },
                {
                    "id": 41,
                    "org_id": 1,
                    "thread_id": 10,
                    "turn_id": 20,
                },
                {
                    "id": 42,
                    "org_id": 1,
                    "thread_id": 11,
                    "turn_id": 21,
                },
            ],
        )
        connection.execute(
            agent_invocations.insert(),
            [{"id": 50, "status": "done"}],
        )
        connection.execute(
            agent_tool_calls.insert(),
            [{"id": 60, "status": "success"}],
        )
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(migration, "op", operations)

        migration.upgrade()
        inspector = sa.inspect(connection)
        assert inspector.has_table("skill_runs") is True
        skill_columns = {column["name"]: column for column in inspector.get_columns("skill_runs")}
        assert {
            "org_id",
            "thread_id",
            "turn_id",
            "run_id",
            "task_id",
            "idempotency_key",
            "skill_code",
            "skill_version",
            "status",
            "input_snapshot",
            "output_snapshot",
            "quality_score",
            "error_code",
        } <= skill_columns.keys()
        assert skill_columns["task_id"]["nullable"] is True
        assert isinstance(skill_columns["skill_version"]["type"], sa.Integer)
        skill_uniques = {
            item["name"]: tuple(item["column_names"])
            for item in inspector.get_unique_constraints("skill_runs")
        }
        assert skill_uniques["uq_skill_runs_run_idempotency"] == (
            "run_id",
            "idempotency_key",
        )
        run_uniques = {
            item["name"]: tuple(item["column_names"])
            for item in inspector.get_unique_constraints("agent_runs")
        }
        assert run_uniques["uq_agent_runs_id_thread_turn_org"] == (
            "id",
            "thread_id",
            "turn_id",
            "org_id",
        )
        skill_foreign_keys = {
            item["name"]: (
                tuple(item["constrained_columns"]),
                item["referred_table"],
                tuple(item["referred_columns"]),
            )
            for item in inspector.get_foreign_keys("skill_runs")
        }
        assert skill_foreign_keys["fk_skill_runs_run_thread_turn_org"] == (
            ("run_id", "thread_id", "turn_id", "org_id"),
            "agent_runs",
            ("id", "thread_id", "turn_id", "org_id"),
        )
        skill_checks = {
            item["name"]: item["sqltext"] for item in inspector.get_check_constraints("skill_runs")
        }
        assert "ck_skill_runs_skill_version_positive" in skill_checks
        invocation_columns = {
            column["name"]: column for column in inspector.get_columns("agent_invocations")
        }
        tool_columns = {
            column["name"]: column for column in inspector.get_columns("agent_tool_calls")
        }
        for columns in (invocation_columns, tool_columns):
            assert columns["skill_run_id"]["nullable"] is True
            assert columns["thread_id"]["nullable"] is True
            assert columns["turn_id"]["nullable"] is True
        assert connection.execute(
            sa.text(
                "SELECT status, skill_run_id, thread_id, turn_id "
                "FROM agent_invocations WHERE id = 50"
            )
        ).one() == ("done", None, None, None)
        assert connection.execute(
            sa.text(
                "SELECT status, skill_run_id, thread_id, turn_id "
                "FROM agent_tool_calls WHERE id = 60"
            )
        ).one() == ("success", None, None, None)

        connection.execute(
            sa.text(
                "INSERT INTO skill_runs "
                "(id, org_id, thread_id, turn_id, run_id, task_id, "
                "idempotency_key, skill_code, skill_version, status, "
                "input_snapshot, output_snapshot) "
                "VALUES (70, 1, 10, 20, 41, NULL, 'run-41-diagnosis', "
                "'account.diagnosis', 1, 'completed', '{}', '{}')"
            )
        )
        for row_id, version in ((72, 0), (73, -1)):
            with pytest.raises(sa.exc.IntegrityError):
                connection.execute(
                    sa.text(
                        "INSERT INTO skill_runs "
                        "(id, org_id, thread_id, turn_id, run_id, task_id, "
                        "idempotency_key, skill_code, skill_version, status, "
                        "input_snapshot, output_snapshot) "
                        "VALUES (:row_id, 1, 10, 20, 41, NULL, :key, "
                        "'account.diagnosis', :version, 'running', '{}', '{}')"
                    ),
                    {
                        "row_id": row_id,
                        "key": f"invalid-version-{version}",
                        "version": version,
                    },
                )
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO skill_runs "
                    "(id, org_id, thread_id, turn_id, run_id, task_id, "
                    "idempotency_key, skill_code, skill_version, status, "
                    "input_snapshot, output_snapshot) "
                    "VALUES (71, 1, 10, 20, 42, NULL, 'cross-thread-run', "
                    "'account.diagnosis', 1, 'running', '{}', '{}')"
                )
            )
        connection.execute(
            sa.text(
                "UPDATE agent_invocations "
                "SET skill_run_id = 70, thread_id = 10, turn_id = 20 "
                "WHERE id = 50"
            )
        )
        connection.execute(
            sa.text(
                "UPDATE agent_tool_calls "
                "SET skill_run_id = 70, thread_id = 10, turn_id = 20 "
                "WHERE id = 60"
            )
        )

        migration.downgrade()
        inspector = sa.inspect(connection)
        assert inspector.has_table("skill_runs") is False
        assert inspector.has_table("agent_invocations") is True
        assert inspector.has_table("agent_tool_calls") is True
        assert "uq_agent_runs_id_thread_turn_org" not in {
            item["name"] for item in inspector.get_unique_constraints("agent_runs")
        }
        assert {"skill_run_id", "thread_id", "turn_id"}.isdisjoint(
            column["name"] for column in inspector.get_columns("agent_invocations")
        )
        assert {"skill_run_id", "thread_id", "turn_id"}.isdisjoint(
            column["name"] for column in inspector.get_columns("agent_tool_calls")
        )
        assert (
            connection.execute(
                sa.text("SELECT status FROM agent_invocations WHERE id = 50")
            ).scalar_one()
            == "done"
        )
        assert (
            connection.execute(
                sa.text("SELECT status FROM agent_tool_calls WHERE id = 60")
            ).scalar_one()
            == "success"
        )

        migration.upgrade()
        inspector = sa.inspect(connection)
        assert inspector.has_table("skill_runs") is True
        assert "uq_agent_runs_id_thread_turn_org" in {
            item["name"] for item in inspector.get_unique_constraints("agent_runs")
        }
        assert "ck_skill_runs_skill_version_positive" in {
            item["name"] for item in inspector.get_check_constraints("skill_runs")
        }
        assert connection.execute(
            sa.text(
                "SELECT status, skill_run_id, thread_id, turn_id "
                "FROM agent_invocations WHERE id = 50"
            )
        ).one() == ("done", None, None, None)


def test_runtime_event_idempotency_migration_is_reversible(monkeypatch) -> None:
    migration = importlib.import_module(
        "migrations.versions.20260728_0175_runtime_event_idempotency"
    )
    assert migration.down_revision == "20260728_0150"

    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    events = sa.Table(
        "events",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("type", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
    )
    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(
            events.insert(),
            [{"id": 1, "type": "legacy.event", "payload": {"legacy": True}}],
        )
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(migration, "op", operations)

        migration.upgrade()
        columns = {item["name"] for item in sa.inspect(connection).get_columns("events")}
        assert "idempotency_key" in columns
        connection.execute(
            sa.text(
                "INSERT INTO events (id, type, idempotency_key) "
                "VALUES (2, 'brain.runtime.message_done', 'same-runtime-event')"
            )
        )
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO events (id, type, idempotency_key) "
                    "VALUES (3, 'brain.runtime.message_done', 'same-runtime-event')"
                )
            )
        connection.execute(sa.text("INSERT INTO events (id, type) VALUES (4, 'legacy.event')"))
        assert (
            connection.execute(sa.text("SELECT payload FROM events WHERE id = 1")).scalar_one()
            is not None
        )

        migration.downgrade()
        assert "idempotency_key" not in {
            item["name"] for item in sa.inspect(connection).get_columns("events")
        }
        migration.upgrade()
        assert "idempotency_key" in {
            item["name"] for item in sa.inspect(connection).get_columns("events")
        }


def test_account_scoped_content_migration_is_reversible_only_without_unscoped_rows(
    monkeypatch,
) -> None:
    migration = importlib.import_module("migrations.versions.20260728_0150_account_scoped_content")

    assert migration.down_revision == "20260728_0100"

    engine = sa.create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    metadata = sa.MetaData()
    projects = sa.Table("projects", metadata, sa.Column("id", sa.Integer, primary_key=True))
    accounts = sa.Table("accounts", metadata, sa.Column("id", sa.Integer, primary_key=True))
    content_items = sa.Table(
        "content_items",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            sa.Integer,
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=300), nullable=False),
    )

    with engine.begin() as connection:

        def assert_project_foreign_key_is_preserved() -> None:
            foreign_key = next(
                item
                for item in sa.inspect(connection).get_foreign_keys("content_items")
                if item["constrained_columns"] == ["project_id"]
            )
            assert foreign_key["referred_table"] == "projects"
            assert foreign_key["referred_columns"] == ["id"]
            if "ondelete" in foreign_key["options"]:
                assert foreign_key["options"]["ondelete"] == "CASCADE"

        def assert_project_delete_cascades(project_id: int, content_id: int) -> None:
            connection.execute(projects.insert(), [{"id": project_id}])
            connection.execute(
                content_items.insert(),
                [
                    {
                        "id": content_id,
                        "project_id": project_id,
                        "title": f"content for project {project_id}",
                    }
                ],
            )
            connection.execute(projects.delete().where(projects.c.id == project_id))
            assert (
                connection.execute(
                    sa.select(content_items.c.id).where(content_items.c.id == content_id)
                ).scalar_one_or_none()
                is None
            )

        metadata.create_all(connection)
        connection.execute(projects.insert(), [{"id": 1}])
        connection.execute(accounts.insert(), [{"id": 1}])
        connection.execute(
            content_items.insert(),
            [{"id": 1, "project_id": 1, "account_id": 1, "title": "legacy content"}],
        )
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(migration, "op", operations)

        migration.upgrade()
        columns = {
            column["name"]: column for column in sa.inspect(connection).get_columns("content_items")
        }
        assert columns["project_id"]["nullable"] is True
        assert_project_foreign_key_is_preserved()
        assert_project_delete_cascades(project_id=10, content_id=10)

        connection.execute(
            sa.text(
                "INSERT INTO content_items (id, project_id, account_id, title) "
                "VALUES (2, NULL, 1, 'account-only content')"
            )
        )
        with pytest.raises(RuntimeError, match="project_id IS NULL"):
            migration.downgrade()
        assert (
            connection.execute(
                sa.text("SELECT count(*) FROM content_items WHERE project_id IS NULL")
            ).scalar_one()
            == 1
        )

        connection.execute(sa.text("DELETE FROM content_items WHERE id = 2"))
        migration.downgrade()
        columns = {
            column["name"]: column for column in sa.inspect(connection).get_columns("content_items")
        }
        assert columns["project_id"]["nullable"] is False
        assert_project_foreign_key_is_preserved()
        assert_project_delete_cascades(project_id=20, content_id=20)
        assert connection.execute(
            sa.text("SELECT project_id, account_id, title FROM content_items WHERE id = 1")
        ).one() == (1, 1, "legacy content")

        migration.upgrade()
        columns = {
            column["name"]: column for column in sa.inspect(connection).get_columns("content_items")
        }
        assert columns["project_id"]["nullable"] is True
        assert_project_foreign_key_is_preserved()
        assert_project_delete_cascades(project_id=30, content_id=30)


def test_conversation_foundation_migration_preserves_legacy_runs(monkeypatch) -> None:
    migration = importlib.import_module("migrations.versions.20260728_0100_conversation_foundation")
    assert migration.down_revision == "20260727_0300"

    engine = sa.create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    metadata = sa.MetaData()
    orgs = sa.Table(
        "orgs",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
    )
    users = sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, sa.ForeignKey("orgs.id"), nullable=False),
    )
    sa.Table(
        "clients",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, sa.ForeignKey("orgs.id"), nullable=False),
    )
    sa.Table(
        "projects",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, sa.ForeignKey("orgs.id"), nullable=False),
    )
    accounts = sa.Table(
        "accounts",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, sa.ForeignKey("orgs.id"), nullable=False),
    )
    brain_tasks = sa.Table(
        "brain_tasks",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
    )
    agent_runs = sa.Table(
        "agent_runs",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("requested_by_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("task_id", sa.Integer, sa.ForeignKey("brain_tasks.id"), nullable=True),
        sa.Column("client_message_id", sa.String(length=128), nullable=False),
    )

    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(orgs.insert(), [{"id": 1}, {"id": 2}])
        connection.execute(users.insert(), [{"id": 10, "org_id": 1}])
        connection.execute(
            brain_tasks.insert(),
            [{"id": 20, "org_id": 1, "title": "迁移前旧任务"}],
        )
        connection.execute(
            agent_runs.insert(),
            [
                {
                    "id": 30,
                    "org_id": 1,
                    "requested_by_id": 10,
                    "task_id": 20,
                    "client_message_id": "legacy-message",
                }
            ],
        )
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(migration, "op", operations)

        migration.upgrade()
        inspector = sa.inspect(connection)
        assert inspector.has_table("conversation_threads") is True
        assert inspector.has_table("conversation_turns") is True
        thread_columns = {
            column["name"]: column for column in inspector.get_columns("conversation_threads")
        }
        turn_columns = {column["name"] for column in inspector.get_columns("conversation_turns")}
        assert thread_columns["account_id"]["nullable"] is False
        assert {"assistant_response", "intent"} <= turn_columns
        turn_uniques = {
            item["name"]: tuple(item["column_names"])
            for item in inspector.get_unique_constraints("conversation_turns")
        }
        assert ("thread_id", "client_message_id") in turn_uniques.values()
        assert turn_uniques["uq_conversation_turn_thread_client_message"] == (
            "thread_id",
            "client_message_id",
        )
        assert turn_uniques["uq_conversation_turn_id_thread_org"] == (
            "id",
            "thread_id",
            "org_id",
        )
        thread_uniques = {
            item["name"]: tuple(item["column_names"])
            for item in inspector.get_unique_constraints("conversation_threads")
        }
        assert thread_uniques["uq_conversation_thread_id_org"] == ("id", "org_id")
        turn_foreign_keys = {
            item["name"]: (
                tuple(item["constrained_columns"]),
                item["referred_table"],
                tuple(item["referred_columns"]),
            )
            for item in inspector.get_foreign_keys("conversation_turns")
        }
        assert turn_foreign_keys["fk_conversation_turn_thread_org"] == (
            ("thread_id", "org_id"),
            "conversation_threads",
            ("id", "org_id"),
        )
        agent_run_columns = {column["name"] for column in inspector.get_columns("agent_runs")}
        assert {"thread_id", "turn_id"} <= agent_run_columns
        agent_run_foreign_keys = {
            item["name"]: (
                tuple(item["constrained_columns"]),
                item["referred_table"],
                tuple(item["referred_columns"]),
            )
            for item in inspector.get_foreign_keys("agent_runs")
        }
        assert agent_run_foreign_keys["fk_agent_runs_thread_org"] == (
            ("thread_id", "org_id"),
            "conversation_threads",
            ("id", "org_id"),
        )
        assert agent_run_foreign_keys["fk_agent_runs_turn_thread_org"] == (
            ("turn_id", "thread_id", "org_id"),
            "conversation_turns",
            ("id", "thread_id", "org_id"),
        )
        assert {item["name"] for item in inspector.get_check_constraints("agent_runs")} >= {
            "ck_agent_runs_turn_requires_thread"
        }

        legacy_run = connection.execute(
            sa.text("SELECT task_id, thread_id, turn_id FROM agent_runs WHERE id = 30")
        ).one()
        assert legacy_run == (20, None, None)

        connection.execute(
            accounts.insert(),
            [{"id": 35, "org_id": 1}],
        )
        connection.execute(
            sa.text(
                "INSERT INTO conversation_threads "
                "(id, org_id, created_by_id, account_id, title) "
                "VALUES (40, 1, 10, 35, '新对话')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO conversation_turns "
                "(id, thread_id, org_id, created_by_id, client_message_id, user_input) "
                "VALUES "
                "(50, 40, 1, 10, 'message-1', '查看最近七天数据'), "
                "(51, 40, 1, 10, 'message-2', '制定下周内容策略')"
            )
        )
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO conversation_turns "
                    "(id, thread_id, org_id, created_by_id, client_message_id, user_input) "
                    "VALUES (52, 40, 2, 10, 'wrong-org-message', '跨组织消息')"
                )
            )
        connection.execute(
            sa.text(
                "INSERT INTO conversation_threads "
                "(id, org_id, created_by_id, account_id, title) "
                "VALUES (41, 1, 10, 35, '第二个对话')"
            )
        )
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(sa.text("UPDATE agent_runs SET turn_id = 51 WHERE id = 30"))
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text("UPDATE agent_runs SET org_id = 2, thread_id = 40 WHERE id = 30")
            )
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text("UPDATE agent_runs SET thread_id = 41, turn_id = 51 WHERE id = 30")
            )
        connection.execute(
            sa.text("UPDATE agent_runs SET thread_id = 40, turn_id = 51 WHERE id = 30")
        )
        inputs = connection.execute(
            sa.text("SELECT user_input FROM conversation_turns WHERE thread_id = 40 ORDER BY id")
        ).scalars()
        assert list(inputs) == ["查看最近七天数据", "制定下周内容策略"]

        migration.downgrade()
        inspector = sa.inspect(connection)
        assert inspector.has_table("conversation_threads") is False
        assert inspector.has_table("conversation_turns") is False
        downgraded_columns = {column["name"] for column in inspector.get_columns("agent_runs")}
        assert "thread_id" not in downgraded_columns
        assert "turn_id" not in downgraded_columns
        assert (
            connection.execute(sa.text("SELECT task_id FROM agent_runs WHERE id = 30")).scalar_one()
            == 20
        )

        migration.upgrade()
        reupgraded_columns = {
            column["name"] for column in sa.inspect(connection).get_columns("agent_runs")
        }
        assert {"thread_id", "turn_id"} <= reupgraded_columns
        reupgraded_run = connection.execute(
            sa.text("SELECT task_id, thread_id, turn_id FROM agent_runs WHERE id = 30")
        ).one()
        assert reupgraded_run == (20, None, None)


def test_ai_coo_runtime_migration_is_additive_and_reversible() -> None:
    module = importlib.import_module("migrations.versions.20260727_0300_ai_coo_runtime")

    assert module.down_revision == "20260727_0200"
    upgrade_source = inspect.getsource(module.upgrade)
    downgrade_source = inspect.getsource(module.downgrade)
    for table_name in (
        '"strategy_plans"',
        '"decision_traces"',
        '"experience_memories"',
        '"reflection_records"',
        '"agent_quality_scores"',
    ):
        assert table_name in upgrade_source
    assert 'drop_table("brain_tasks")' not in upgrade_source
    assert 'drop_column("brain_tasks"' not in upgrade_source
    assert 'drop_table("strategy_plans")' in downgrade_source
    assert 'drop_table("decision_traces")' in downgrade_source
    assert 'drop_table("experience_memories")' in downgrade_source
    assert 'drop_table("reflection_records")' in downgrade_source
    assert 'drop_table("agent_quality_scores")' in downgrade_source


def test_platform_publish_jobs_migration_is_additive_and_reversible() -> None:
    module = importlib.import_module("migrations.versions.20260727_0100_platform_publish_jobs")

    assert module.down_revision == "20260723_0200"
    upgrade_source = inspect.getsource(module.upgrade)
    downgrade_source = inspect.getsource(module.downgrade)
    assert '"platform_publish_jobs"' in upgrade_source
    assert '"share_id"' in upgrade_source
    assert '"posting_task_id"' in upgrade_source
    assert '"platform_content_record_id"' in upgrade_source
    assert "uq_platform_publish_jobs_org_idempotency" in upgrade_source
    assert 'drop_table("platform_publish_jobs")' in downgrade_source


def test_platform_content_source_migration_preserves_import_lineage() -> None:
    module = importlib.import_module("migrations.versions.20260727_0200_platform_content_source")

    assert module.down_revision == "20260727_0100"
    upgrade_source = inspect.getsource(module.upgrade)
    downgrade_source = inspect.getsource(module.downgrade)
    assert '"source_kind"' in upgrade_source
    assert '"source_metadata"' in upgrade_source
    assert "data_import_batches.source_kind" in upgrade_source
    assert 'drop_column("platform_content_records", "source_metadata")' in downgrade_source
    assert 'drop_column("platform_content_records", "source_kind")' in downgrade_source


def test_account_data_center_migration_is_linear_and_additive() -> None:
    module = importlib.import_module("migrations.versions.20260722_0100_account_data_center")

    assert module.down_revision == "20260721_0400"
    assert isinstance(module.platform_enum, postgresql.ENUM)
    assert module.platform_enum.create_type is False
    upgrade_source = inspect.getsource(module.upgrade)
    downgrade_source = inspect.getsource(module.downgrade)
    for table_name in (
        '"data_import_batches"',
        '"data_artifacts"',
        '"data_import_rows"',
        '"platform_content_records"',
        '"account_metric_snapshots"',
        '"audience_profile_snapshots"',
        '"audience_profile_items"',
        '"benchmark_snapshots"',
        '"data_conflicts"',
    ):
        assert table_name in upgrade_source
    assert '"metric_snapshots"' in upgrade_source
    assert '"import_batch_id"' in upgrade_source
    assert '"platform_content_record_id"' in upgrade_source
    assert '"content_sha256"' in upgrade_source
    assert '"canonical_share_url"' in upgrade_source
    assert '"resolution_outcome"' in upgrade_source
    assert '"resolved_by_id"' in upgrade_source
    assert '"resolved_at"' in upgrade_source
    assert "uq_data_import_batches_active_preview_identity" in upgrade_source
    assert "uq_platform_content_records_account_canonical_share_url" in upgrade_source
    assert 'drop_table("data_conflicts")' in downgrade_source


def test_account_data_center_migration_smoke_upgrade_downgrade_reupgrade(monkeypatch) -> None:
    migration = importlib.import_module("migrations.versions.20260722_0100_account_data_center")
    engine = sa.create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    metadata = sa.MetaData()
    orgs = sa.Table(
        "orgs",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
    )
    users = sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "org_id", sa.Integer, sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("email", sa.String(length=255), nullable=True),
    )
    accounts = sa.Table(
        "accounts",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "org_id", sa.Integer, sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("platform", sa.String(length=32), nullable=False),
    )
    content_items = sa.Table(
        "content_items",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, nullable=True),
    )
    sa.Table(
        "metric_snapshots",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "org_id", sa.Integer, sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "content_item_id",
            sa.Integer,
            sa.ForeignKey("content_items.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "account_id",
            sa.Integer,
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("play", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exposure", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("like_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("comment_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("share_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("follower_delta", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(orgs.insert(), [{"id": 1, "name": "Migration org"}])
        connection.execute(
            users.insert(),
            [{"id": 10, "org_id": 1, "email": "migration-user@test.com"}],
        )
        connection.execute(
            accounts.insert(),
            [{"id": 100, "org_id": 1, "platform": "douyin"}],
        )
        connection.execute(content_items.insert(), [{"id": 1000, "project_id": None}])
        connection.execute(
            sa.text(
                "INSERT INTO metric_snapshots "
                "(id, org_id, content_item_id, account_id, source, stat_date, title, "
                "play, exposure, completion_rate, like_rate, comment_rate, share_rate, "
                "follower_delta) VALUES ("
                "1, 1, 1000, 100, 'douyin', '2026-07-21', 'Pre-migration row', "
                "1, 2, 0.1, 0.2, 0.3, 0.4, 5)"
            )
        )
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(migration, "op", operations)

        migration.upgrade()
        inspector = sa.inspect(connection)
        assert inspector.has_table("data_import_batches") is True
        assert inspector.has_table("platform_content_records") is True
        upgraded_metric_columns = {
            column["name"] for column in inspector.get_columns("metric_snapshots")
        }
        upgraded_content_columns = {
            column["name"] for column in inspector.get_columns("platform_content_records")
        }
        upgraded_batch_columns = {
            column["name"] for column in inspector.get_columns("data_import_batches")
        }
        upgraded_row_columns = {
            column["name"] for column in inspector.get_columns("data_import_rows")
        }
        upgraded_metric_checks = {
            constraint["name"] for constraint in inspector.get_check_constraints("metric_snapshots")
        }
        upgraded_batch_indexes = {
            index["name"] for index in inspector.get_indexes("data_import_batches")
        }
        upgraded_content_indexes = {
            index["name"] for index in inspector.get_indexes("platform_content_records")
        }
        assert "import_batch_id" in upgraded_metric_columns
        assert "platform_content_record_id" in upgraded_metric_columns
        assert "content_sha256" in upgraded_batch_columns
        assert "canonical_import_batch_id" in upgraded_content_columns
        assert "canonical_share_url" in upgraded_content_columns
        assert "resolution_outcome" in upgraded_row_columns
        assert "resolved_by_id" in upgraded_row_columns
        assert "resolved_at" in upgraded_row_columns
        assert "uq_data_import_batches_active_preview_identity" in upgraded_batch_indexes
        assert "uq_platform_content_records_account_canonical_share_url" in upgraded_content_indexes
        assert "ck_metric_snapshots_account_required_for_source_links" in upgraded_metric_checks

        migration.downgrade()
        inspector = sa.inspect(connection)
        downgraded_metric_columns = {
            column["name"] for column in inspector.get_columns("metric_snapshots")
        }
        downgraded_metric_checks = {
            constraint["name"] for constraint in inspector.get_check_constraints("metric_snapshots")
        }
        assert inspector.has_table("data_import_batches") is False
        assert inspector.has_table("platform_content_records") is False
        assert "import_batch_id" not in downgraded_metric_columns
        assert "platform_content_record_id" not in downgraded_metric_columns
        assert (
            "ck_metric_snapshots_account_required_for_source_links" not in downgraded_metric_checks
        )

        migration.upgrade()
        inspector = sa.inspect(connection)
        reupgraded_metric_columns = {
            column["name"] for column in inspector.get_columns("metric_snapshots")
        }
        reupgraded_content_columns = {
            column["name"] for column in inspector.get_columns("platform_content_records")
        }
        reupgraded_batch_columns = {
            column["name"] for column in inspector.get_columns("data_import_batches")
        }
        reupgraded_row_columns = {
            column["name"] for column in inspector.get_columns("data_import_rows")
        }
        reupgraded_metric_checks = {
            constraint["name"] for constraint in inspector.get_check_constraints("metric_snapshots")
        }
        assert inspector.has_table("data_import_batches") is True
        assert inspector.has_table("platform_content_records") is True
        assert "import_batch_id" in reupgraded_metric_columns
        assert "platform_content_record_id" in reupgraded_metric_columns
        assert "content_sha256" in reupgraded_batch_columns
        assert "canonical_import_batch_id" in reupgraded_content_columns
        assert "canonical_share_url" in reupgraded_content_columns
        assert "resolution_outcome" in reupgraded_row_columns
        assert "ck_metric_snapshots_account_required_for_source_links" in reupgraded_metric_checks


def test_account_data_projection_guards_migration_is_linear_and_additive() -> None:
    module = importlib.import_module(
        "migrations.versions.20260722_0200_account_data_projection_guards"
    )

    assert module.down_revision == "20260722_0100"
    upgrade_source = inspect.getsource(module.upgrade)
    downgrade_source = inspect.getsource(module.downgrade)
    assert '"platform_content_records"' in upgrade_source
    assert '"metric_snapshots"' in upgrade_source
    assert '"canonical_import_row_number"' in upgrade_source
    assert "uq_platform_content_records_import_row_identity" in upgrade_source
    assert "uq_metric_snapshots_import_projection" in upgrade_source
    assert 'drop_constraint("uq_metric_snapshots_import_projection"' in downgrade_source


def test_account_data_projection_guards_migration_smoke(monkeypatch) -> None:
    base_migration = importlib.import_module(
        "migrations.versions.20260722_0100_account_data_center"
    )
    guard_migration = importlib.import_module(
        "migrations.versions.20260722_0200_account_data_projection_guards"
    )
    engine = sa.create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    metadata = sa.MetaData()
    sa.Table(
        "orgs",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
    )
    sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "org_id", sa.Integer, sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("email", sa.String(length=255), nullable=True),
    )
    sa.Table(
        "accounts",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "org_id", sa.Integer, sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("platform", sa.String(length=32), nullable=False),
    )
    sa.Table(
        "content_items",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, nullable=True),
    )
    sa.Table(
        "metric_snapshots",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "org_id", sa.Integer, sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "content_item_id",
            sa.Integer,
            sa.ForeignKey("content_items.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "account_id",
            sa.Integer,
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("play", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exposure", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("like_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("comment_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("share_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("follower_delta", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    with engine.begin() as connection:
        metadata.create_all(connection)
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(base_migration, "op", operations)
        monkeypatch.setattr(guard_migration, "op", operations)

        base_migration.upgrade()
        guard_migration.upgrade()

        inspector = sa.inspect(connection)
        content_columns = {
            column["name"] for column in inspector.get_columns("platform_content_records")
        }
        metric_uniques = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("metric_snapshots")
        }
        content_uniques = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("platform_content_records")
        }
        assert "canonical_import_row_number" in content_columns
        assert "uq_metric_snapshots_import_projection" in metric_uniques
        assert "uq_platform_content_records_import_row_identity" in content_uniques
