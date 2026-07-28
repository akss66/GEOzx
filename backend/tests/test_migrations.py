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


def test_migration_head_is_conversation_foundation() -> None:
    assert get_head_revision() == "20260728_0100"


def test_conversation_foundation_migration_preserves_legacy_runs(monkeypatch) -> None:
    migration = importlib.import_module(
        "migrations.versions.20260728_0100_conversation_foundation"
    )
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
            column["name"]: column
            for column in inspector.get_columns("conversation_threads")
        }
        turn_columns = {
            column["name"] for column in inspector.get_columns("conversation_turns")
        }
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
        agent_run_columns = {
            column["name"] for column in inspector.get_columns("agent_runs")
        }
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
        assert {
            item["name"] for item in inspector.get_check_constraints("agent_runs")
        } >= {"ck_agent_runs_turn_requires_thread"}

        legacy_run = connection.execute(
            sa.text(
                "SELECT task_id, thread_id, turn_id FROM agent_runs WHERE id = 30"
            )
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
            connection.execute(
                sa.text("UPDATE agent_runs SET turn_id = 51 WHERE id = 30")
            )
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text("UPDATE agent_runs SET org_id = 2, thread_id = 40 WHERE id = 30")
            )
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "UPDATE agent_runs SET thread_id = 41, turn_id = 51 WHERE id = 30"
                )
            )
        connection.execute(
            sa.text(
                "UPDATE agent_runs SET thread_id = 40, turn_id = 51 WHERE id = 30"
            )
        )
        inputs = connection.execute(
            sa.text(
                "SELECT user_input FROM conversation_turns "
                "WHERE thread_id = 40 ORDER BY id"
            )
        ).scalars()
        assert list(inputs) == ["查看最近七天数据", "制定下周内容策略"]

        migration.downgrade()
        inspector = sa.inspect(connection)
        assert inspector.has_table("conversation_threads") is False
        assert inspector.has_table("conversation_turns") is False
        downgraded_columns = {
            column["name"] for column in inspector.get_columns("agent_runs")
        }
        assert "thread_id" not in downgraded_columns
        assert "turn_id" not in downgraded_columns
        assert connection.execute(
            sa.text("SELECT task_id FROM agent_runs WHERE id = 30")
        ).scalar_one() == 20

        migration.upgrade()
        reupgraded_columns = {
            column["name"] for column in sa.inspect(connection).get_columns("agent_runs")
        }
        assert {"thread_id", "turn_id"} <= reupgraded_columns
        reupgraded_run = connection.execute(
            sa.text(
                "SELECT task_id, thread_id, turn_id FROM agent_runs WHERE id = 30"
            )
        ).one()
        assert reupgraded_run == (20, None, None)


def test_ai_coo_runtime_migration_is_additive_and_reversible() -> None:
    module = importlib.import_module(
        "migrations.versions.20260727_0300_ai_coo_runtime"
    )

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
    module = importlib.import_module(
        "migrations.versions.20260727_0100_platform_publish_jobs"
    )

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
    module = importlib.import_module(
        "migrations.versions.20260727_0200_platform_content_source"
    )

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
            constraint["name"]
            for constraint in inspector.get_check_constraints("metric_snapshots")
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
        assert (
            "uq_platform_content_records_account_canonical_share_url"
            in upgraded_content_indexes
        )
        assert "ck_metric_snapshots_account_required_for_source_links" in upgraded_metric_checks

        migration.downgrade()
        inspector = sa.inspect(connection)
        downgraded_metric_columns = {
            column["name"] for column in inspector.get_columns("metric_snapshots")
        }
        downgraded_metric_checks = {
            constraint["name"]
            for constraint in inspector.get_check_constraints("metric_snapshots")
        }
        assert inspector.has_table("data_import_batches") is False
        assert inspector.has_table("platform_content_records") is False
        assert "import_batch_id" not in downgraded_metric_columns
        assert "platform_content_record_id" not in downgraded_metric_columns
        assert (
            "ck_metric_snapshots_account_required_for_source_links"
            not in downgraded_metric_checks
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
            constraint["name"]
            for constraint in inspector.get_check_constraints("metric_snapshots")
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
