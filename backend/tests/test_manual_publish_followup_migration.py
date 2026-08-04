from __future__ import annotations

import importlib
from io import StringIO

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_manual_publish_followup_migration_is_reversible_and_cross_dialect(
    monkeypatch,
) -> None:
    migration = importlib.import_module("migrations.versions.20260805_0100_manual_publish_followup")
    assert migration.down_revision == "20260804_0500"

    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    sa.Table(
        "content_schedule_entries",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False),
        sa.Column("account_id", sa.Integer, nullable=False),
        sa.Column("created_by_id", sa.Integer, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
    )
    with engine.begin() as connection:
        metadata.create_all(connection)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        migration.upgrade()
        columns = {
            column["name"]: column
            for column in sa.inspect(connection).get_columns("content_schedule_entries")
        }
        assert columns["published_at"]["nullable"] is True
        assert "ix_content_schedule_entries_publication_followup" in {
            index["name"]
            for index in sa.inspect(connection).get_indexes("content_schedule_entries")
        }

        migration.downgrade()
        assert "published_at" not in {
            column["name"]
            for column in sa.inspect(connection).get_columns("content_schedule_entries")
        }
        assert "ix_content_schedule_entries_publication_followup" not in {
            index["name"]
            for index in sa.inspect(connection).get_indexes("content_schedule_entries")
        }

    output = StringIO()
    postgres_context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    monkeypatch.setattr(migration, "op", Operations(postgres_context))
    migration.upgrade()
    migration.downgrade()
    sql = output.getvalue().lower()
    assert "add column published_at timestamp with time zone" in sql
    assert "create index ix_content_schedule_entries_publication_followup" in sql
    assert "drop index ix_content_schedule_entries_publication_followup" in sql
    assert "drop column published_at" in sql
