import importlib
import inspect


def test_client_workspace_migration_is_additive() -> None:
    module = importlib.import_module(
        "migrations.versions.20260716_0200_client_workspace_shell"
    )

    assert module.down_revision == "20260716_0100"
    source = inspect.getsource(module.upgrade)
    assert '"clients"' in source
    assert '"project_accounts"' in source
    assert 'drop_column("accounts", "project_id")' not in source
    assert "::workspace_role" in source


def test_knowledge_workspace_migration_preserves_legacy_entries() -> None:
    module = importlib.import_module(
        "migrations.versions.20260717_0200_knowledge_workspace"
    )

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
    assert (
        'new_sqlite_name="fk_matrix_distribution_plans_created_by_id_users"'
        in source
    )


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
