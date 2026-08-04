from __future__ import annotations

import importlib
from io import StringIO

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_schedule_source_slot_unique_is_reversible_and_cross_dialect(monkeypatch) -> None:
    migration = importlib.import_module(
        "migrations.versions.20260805_0200_schedule_source_slot_unique"
    )
    assert migration.down_revision == "20260805_0100"

    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    sa.Table(
        "content_schedule_entries",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False),
        sa.Column("account_id", sa.Integer, nullable=False),
        sa.Column("source_artifact_id", sa.Integer, nullable=False),
        sa.Column("source_artifact_version", sa.Integer, nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
    )
    with engine.begin() as connection:
        metadata.create_all(connection)
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()
        constraints = {
            item["name"]
            for item in sa.inspect(connection).get_unique_constraints(
                "content_schedule_entries"
            )
        }
        assert "uq_content_schedule_source_slot" in constraints
        migration.downgrade()
        constraints = {
            item["name"]
            for item in sa.inspect(connection).get_unique_constraints(
                "content_schedule_entries"
            )
        }
        assert "uq_content_schedule_source_slot" not in constraints

    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    monkeypatch.setattr(migration, "op", Operations(context))
    migration.upgrade()
    migration.downgrade()
    sql = output.getvalue().lower()
    assert "add constraint uq_content_schedule_source_slot unique" in sql
    assert "drop constraint uq_content_schedule_source_slot" in sql
