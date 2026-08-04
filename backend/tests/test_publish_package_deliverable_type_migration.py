from __future__ import annotations

import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_publish_package_enum_migration_is_postgres_only_and_reversible(
    monkeypatch,
) -> None:
    migration = importlib.import_module(
        "migrations.versions.20260805_0300_publish_package_deliverable_type"
    )
    assert migration.down_revision == "20260805_0200"

    sqlite = sa.create_engine("sqlite://")
    with sqlite.begin() as connection:
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()
        migration.downgrade()

    class _Dialect:
        name = "postgresql"

    class _Bind:
        dialect = _Dialect()

    class _Autocommit:
        def __enter__(self):
            return None

        def __exit__(self, *_args):
            return None

    class _Context:
        @staticmethod
        def autocommit_block():
            return _Autocommit()

    statements: list[str] = []

    class _PostgresOperations:
        @staticmethod
        def get_bind():
            return _Bind()

        @staticmethod
        def get_context():
            return _Context()

        @staticmethod
        def execute(statement: str):
            statements.append(statement)

    monkeypatch.setattr(migration, "op", _PostgresOperations())
    migration.upgrade()
    migration.downgrade()
    sql = "\n".join(statements).lower()
    assert "alter type deliverable_type add value if not exists 'publish_package'" in sql
    assert "where type::text = 'publish_package'" in sql
    assert "where deliverable_type::text = 'publish_package'" in sql
    assert "drop type deliverable_type" in sql
    assert "create type deliverable_type as enum" in sql
