import importlib
import inspect

import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import event


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


def test_migration_head_is_account_data_center() -> None:
    assert get_head_revision() == "20260722_0100"


def test_account_data_center_migration_is_linear_and_additive() -> None:
    module = importlib.import_module("migrations.versions.20260722_0100_account_data_center")

    assert module.down_revision == "20260721_0400"
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
        upgraded_metric_checks = {
            constraint["name"]
            for constraint in inspector.get_check_constraints("metric_snapshots")
        }
        assert "import_batch_id" in upgraded_metric_columns
        assert "platform_content_record_id" in upgraded_metric_columns
        assert "canonical_import_batch_id" in upgraded_content_columns
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
        reupgraded_metric_checks = {
            constraint["name"]
            for constraint in inspector.get_check_constraints("metric_snapshots")
        }
        assert inspector.has_table("data_import_batches") is True
        assert inspector.has_table("platform_content_records") is True
        assert "import_batch_id" in reupgraded_metric_columns
        assert "platform_content_record_id" in reupgraded_metric_columns
        assert "canonical_import_batch_id" in reupgraded_content_columns
        assert "ck_metric_snapshots_account_required_for_source_links" in reupgraded_metric_checks
