import importlib
import inspect

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import event
from sqlalchemy.dialects import postgresql


def get_head_revision() -> str | None:
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    return script.get_current_head()


def test_tool_side_effect_outbox_migration_is_reversible(monkeypatch) -> None:
    migration = importlib.import_module(
        "migrations.versions.20260730_0500_tool_side_effect_outbox"
    )
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
        columns = {
            column["name"]: column
            for column in inspector.get_columns("agent_tool_calls")
        }
        checks = {
            constraint["name"]
            for constraint in inspector.get_check_constraints("agent_tool_calls")
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


def test_migration_head_is_tool_side_effect_outbox() -> None:
    assert get_head_revision() == "20260730_0500"


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
