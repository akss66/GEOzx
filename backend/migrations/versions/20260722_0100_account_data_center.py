"""Add the account data import ledger and normalized snapshots.

Revision ID: 20260722_0100
Revises: 20260721_0400
Create Date: 2026-07-22 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK, JSONVariant

revision: str = "20260722_0100"
down_revision: str | None = "20260721_0400"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

data_source_kind = sa.Enum(
    "official_api",
    "platform_export",
    "screenshot_verified",
    "manual_entry",
    name="data_source_kind",
)
import_batch_status = sa.Enum(
    "uploaded",
    "preview_ready",
    "committed",
    "revoked",
    "failed",
    name="import_batch_status",
)
import_row_status = sa.Enum(
    "ready",
    "invalid",
    "needs_resolution",
    "committed",
    "revoked",
    name="import_row_status",
)
content_identity_confidence = sa.Enum(
    "confirmed",
    "provisional",
    "ambiguous",
    "unresolved",
    name="content_identity_confidence",
)
conflict_status = sa.Enum(
    "open",
    "resolved",
    "ignored",
    name="conflict_status",
)
platform_enum = sa.Enum(
    "douyin",
    "xiaohongshu",
    "shipinhao",
    name="platform",
    create_type=False,
)


def upgrade() -> None:
    op.create_table(
        "data_import_batches",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("account_id", BigIntPK, nullable=False),
        sa.Column("created_by_id", BigIntPK, nullable=True),
        sa.Column("source_kind", data_source_kind, nullable=False),
        sa.Column("status", import_batch_status, nullable=False),
        sa.Column("template_code", sa.String(length=80), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("row_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "org_id",
            "account_id",
            "id",
            name="uq_data_import_batches_org_account_id",
        ),
    )
    for column in ("org_id", "account_id", "status"):
        op.create_index(op.f(f"ix_data_import_batches_{column}"), "data_import_batches", [column])
    op.create_index(
        "uq_data_import_batches_active_preview_identity",
        "data_import_batches",
        ["org_id", "account_id", "source_kind", "template_code", "content_sha256"],
        unique=True,
        sqlite_where=sa.text("committed_at IS NULL AND revoked_at IS NULL"),
        postgresql_where=sa.text("committed_at IS NULL AND revoked_at IS NULL"),
    )

    op.create_table(
        "data_artifacts",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("account_id", BigIntPK, nullable=False),
        sa.Column("batch_id", BigIntPK, nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["org_id", "account_id", "batch_id"],
            [
                "data_import_batches.org_id",
                "data_import_batches.account_id",
                "data_import_batches.id",
            ],
            name="fk_data_artifacts_batch_scope",
            ondelete="CASCADE",
        ),
    )
    for column in ("org_id", "account_id", "batch_id", "sha256"):
        op.create_index(op.f(f"ix_data_artifacts_{column}"), "data_artifacts", [column])

    op.create_table(
        "platform_content_records",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("account_id", BigIntPK, nullable=False),
        sa.Column("platform", platform_enum, nullable=False),
        sa.Column("canonical_import_batch_id", BigIntPK, nullable=True),
        sa.Column("external_content_id", sa.String(length=128), nullable=True),
        sa.Column("share_url", sa.String(length=1000), nullable=True),
        sa.Column("canonical_share_url", sa.String(length=1000), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("identity_confidence", content_identity_confidence, nullable=False),
        sa.Column("weak_fingerprint", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["org_id", "account_id", "canonical_import_batch_id"],
            [
                "data_import_batches.org_id",
                "data_import_batches.account_id",
                "data_import_batches.id",
            ],
            name="fk_platform_content_records_canonical_batch_scope",
        ),
        sa.UniqueConstraint(
            "org_id",
            "account_id",
            "id",
            name="uq_platform_content_records_org_account_id",
        ),
    )
    for column in ("org_id", "account_id", "platform", "canonical_import_batch_id"):
        op.create_index(
            op.f(f"ix_platform_content_records_{column}"),
            "platform_content_records",
            [column],
        )
    op.create_index(
        "ix_platform_content_records_weak_fingerprint",
        "platform_content_records",
        ["weak_fingerprint"],
    )
    op.create_index(
        "uq_platform_content_records_account_external_content_id",
        "platform_content_records",
        ["account_id", "platform", "external_content_id"],
        unique=True,
        sqlite_where=sa.text("external_content_id IS NOT NULL"),
        postgresql_where=sa.text("external_content_id IS NOT NULL"),
    )
    op.create_index(
        "uq_platform_content_records_account_canonical_share_url",
        "platform_content_records",
        ["account_id", "platform", "canonical_share_url"],
        unique=True,
        sqlite_where=sa.text("canonical_share_url IS NOT NULL"),
        postgresql_where=sa.text("canonical_share_url IS NOT NULL"),
    )

    op.create_table(
        "data_import_rows",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("account_id", BigIntPK, nullable=False),
        sa.Column("batch_id", BigIntPK, nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("status", import_row_status, nullable=False),
        sa.Column("raw_values", JSONVariant, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("normalized_values", JSONVariant, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("field_errors", JSONVariant, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("warnings", JSONVariant, server_default=sa.text("'[]'"), nullable=False),
        sa.Column(
            "candidate_content_ids", JSONVariant, server_default=sa.text("'[]'"), nullable=False
        ),
        sa.Column(
            "projected_target_ids", JSONVariant, server_default=sa.text("'[]'"), nullable=False
        ),
        sa.Column("platform_content_record_id", BigIntPK, nullable=True),
        sa.Column("resolution_outcome", sa.String(length=32), nullable=True),
        sa.Column("resolved_by_id", BigIntPK, nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("weak_fingerprint", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["org_id", "account_id", "batch_id"],
            [
                "data_import_batches.org_id",
                "data_import_batches.account_id",
                "data_import_batches.id",
            ],
            name="fk_data_import_rows_batch_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "account_id", "platform_content_record_id"],
            [
                "platform_content_records.org_id",
                "platform_content_records.account_id",
                "platform_content_records.id",
            ],
            name="fk_data_import_rows_content_scope",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_id"],
            ["users.id"],
            name="fk_data_import_rows_resolved_by_id_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("batch_id", "row_number", name="uq_data_import_rows_batch_row"),
    )
    for column in ("org_id", "account_id", "batch_id", "status", "platform_content_record_id"):
        op.create_index(op.f(f"ix_data_import_rows_{column}"), "data_import_rows", [column])
    op.create_index(
        "ix_data_import_rows_weak_fingerprint", "data_import_rows", ["weak_fingerprint"]
    )

    op.create_table(
        "account_metric_snapshots",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("account_id", BigIntPK, nullable=False),
        sa.Column("import_batch_id", BigIntPK, nullable=False),
        sa.Column("source_kind", data_source_kind, nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("follower_count", sa.Integer(), nullable=True),
        sa.Column("follower_delta", sa.Integer(), nullable=True),
        sa.Column("total_play", sa.Integer(), nullable=True),
        sa.Column("total_exposure", sa.Integer(), nullable=True),
        sa.Column("engagement_rate", sa.Float(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["org_id", "account_id", "import_batch_id"],
            [
                "data_import_batches.org_id",
                "data_import_batches.account_id",
                "data_import_batches.id",
            ],
            name="fk_account_metric_snapshots_batch_scope",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "account_id",
            "import_batch_id",
            "stat_date",
            name="uq_account_metric_snapshots_batch_date",
        ),
    )
    for column in ("org_id", "account_id", "import_batch_id", "stat_date"):
        op.create_index(
            op.f(f"ix_account_metric_snapshots_{column}"),
            "account_metric_snapshots",
            [column],
        )

    op.create_table(
        "audience_profile_snapshots",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("account_id", BigIntPK, nullable=False),
        sa.Column("import_batch_id", BigIntPK, nullable=False),
        sa.Column("source_kind", data_source_kind, nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("dimension", sa.String(length=80), nullable=False),
        sa.Column("total_audience", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["org_id", "account_id", "import_batch_id"],
            [
                "data_import_batches.org_id",
                "data_import_batches.account_id",
                "data_import_batches.id",
            ],
            name="fk_audience_profile_snapshots_batch_scope",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "org_id",
            "account_id",
            "id",
            name="uq_audience_profile_snapshots_org_account_id",
        ),
        sa.UniqueConstraint(
            "account_id",
            "import_batch_id",
            "stat_date",
            "dimension",
            name="uq_audience_profile_snapshots_batch_dimension",
        ),
    )
    for column in ("org_id", "account_id", "import_batch_id", "stat_date"):
        op.create_index(
            op.f(f"ix_audience_profile_snapshots_{column}"),
            "audience_profile_snapshots",
            [column],
        )

    op.create_table(
        "audience_profile_items",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("account_id", BigIntPK, nullable=False),
        sa.Column("snapshot_id", BigIntPK, nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("value", sa.String(length=120), nullable=False),
        sa.Column("ratio", sa.Float(), nullable=True),
        sa.Column("rank", sa.Integer(), server_default="0", nullable=False),
        sa.Column("meta", JSONVariant, server_default=sa.text("'{}'"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["org_id", "account_id", "snapshot_id"],
            [
                "audience_profile_snapshots.org_id",
                "audience_profile_snapshots.account_id",
                "audience_profile_snapshots.id",
            ],
            name="fk_audience_profile_items_snapshot_scope",
            ondelete="CASCADE",
        ),
    )
    for column in ("org_id", "account_id", "snapshot_id"):
        op.create_index(
            op.f(f"ix_audience_profile_items_{column}"), "audience_profile_items", [column]
        )

    op.create_table(
        "benchmark_snapshots",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("account_id", BigIntPK, nullable=False),
        sa.Column("import_batch_id", BigIntPK, nullable=False),
        sa.Column("source_kind", data_source_kind, nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("benchmark_code", sa.String(length=80), nullable=False),
        sa.Column("metric_code", sa.String(length=80), nullable=False),
        sa.Column("metric_value", sa.Float(), nullable=True),
        sa.Column("sample_size", sa.Integer(), nullable=True),
        sa.Column("meta", JSONVariant, server_default=sa.text("'{}'"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["org_id", "account_id", "import_batch_id"],
            [
                "data_import_batches.org_id",
                "data_import_batches.account_id",
                "data_import_batches.id",
            ],
            name="fk_benchmark_snapshots_batch_scope",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "account_id",
            "import_batch_id",
            "stat_date",
            "benchmark_code",
            "metric_code",
            name="uq_benchmark_snapshots_batch_metric",
        ),
    )
    for column in ("org_id", "account_id", "import_batch_id", "stat_date"):
        op.create_index(op.f(f"ix_benchmark_snapshots_{column}"), "benchmark_snapshots", [column])

    op.create_table(
        "data_conflicts",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("account_id", BigIntPK, nullable=False),
        sa.Column("batch_id", BigIntPK, nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("status", conflict_status, nullable=False),
        sa.Column("field_name", sa.String(length=120), nullable=False),
        sa.Column("conflict_code", sa.String(length=80), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("existing_value", JSONVariant, nullable=True),
        sa.Column("incoming_value", JSONVariant, nullable=True),
        sa.Column(
            "candidate_content_ids", JSONVariant, server_default=sa.text("'[]'"), nullable=False
        ),
        sa.Column("resolved_by_id", BigIntPK, nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["org_id", "account_id", "batch_id"],
            [
                "data_import_batches.org_id",
                "data_import_batches.account_id",
                "data_import_batches.id",
            ],
            name="fk_data_conflicts_batch_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["resolved_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "batch_id",
            "row_number",
            "field_name",
            name="uq_data_conflicts_batch_row_field",
        ),
    )
    for column in ("org_id", "account_id", "batch_id", "status"):
        op.create_index(op.f(f"ix_data_conflicts_{column}"), "data_conflicts", [column])

    with op.batch_alter_table("metric_snapshots") as batch_op:
        batch_op.add_column(sa.Column("import_batch_id", BigIntPK, nullable=True))
        batch_op.add_column(sa.Column("platform_content_record_id", BigIntPK, nullable=True))
        batch_op.add_column(sa.Column("like_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("comment_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("share_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("favorite_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("cover_click_rate", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("avg_watch_time_seconds", sa.Float(), nullable=True))
        batch_op.create_check_constraint(
            "ck_metric_snapshots_account_required_for_source_links",
            "(import_batch_id IS NULL AND platform_content_record_id IS NULL) "
            "OR account_id IS NOT NULL",
        )
        batch_op.create_foreign_key(
            "fk_metric_snapshots_import_batch_scope",
            "data_import_batches",
            ["org_id", "account_id", "import_batch_id"],
            ["org_id", "account_id", "id"],
            ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            "fk_metric_snapshots_content_scope",
            "platform_content_records",
            ["org_id", "account_id", "platform_content_record_id"],
            ["org_id", "account_id", "id"],
            ondelete="CASCADE",
        )
        batch_op.create_index("ix_metric_snapshots_import_batch_id", ["import_batch_id"])
        batch_op.create_index(
            "ix_metric_snapshots_platform_content_record_id",
            ["platform_content_record_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("metric_snapshots") as batch_op:
        batch_op.drop_index("ix_metric_snapshots_platform_content_record_id")
        batch_op.drop_index("ix_metric_snapshots_import_batch_id")
        batch_op.drop_constraint(
            "ck_metric_snapshots_account_required_for_source_links",
            type_="check",
        )
        batch_op.drop_constraint(
            "fk_metric_snapshots_content_scope",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_metric_snapshots_import_batch_scope",
            type_="foreignkey",
        )
        batch_op.drop_column("avg_watch_time_seconds")
        batch_op.drop_column("cover_click_rate")
        batch_op.drop_column("favorite_count")
        batch_op.drop_column("share_count")
        batch_op.drop_column("comment_count")
        batch_op.drop_column("like_count")
        batch_op.drop_column("platform_content_record_id")
        batch_op.drop_column("import_batch_id")

    for column in ("status", "batch_id", "account_id", "org_id"):
        op.drop_index(op.f(f"ix_data_conflicts_{column}"), table_name="data_conflicts")
    op.drop_table("data_conflicts")

    for column in ("stat_date", "import_batch_id", "account_id", "org_id"):
        op.drop_index(op.f(f"ix_benchmark_snapshots_{column}"), table_name="benchmark_snapshots")
    op.drop_table("benchmark_snapshots")

    for column in ("snapshot_id", "account_id", "org_id"):
        op.drop_index(
            op.f(f"ix_audience_profile_items_{column}"), table_name="audience_profile_items"
        )
    op.drop_table("audience_profile_items")

    for column in ("stat_date", "import_batch_id", "account_id", "org_id"):
        op.drop_index(
            op.f(f"ix_audience_profile_snapshots_{column}"),
            table_name="audience_profile_snapshots",
        )
    op.drop_table("audience_profile_snapshots")

    for column in ("stat_date", "import_batch_id", "account_id", "org_id"):
        op.drop_index(
            op.f(f"ix_account_metric_snapshots_{column}"),
            table_name="account_metric_snapshots",
        )
    op.drop_table("account_metric_snapshots")

    op.drop_index("ix_data_import_rows_weak_fingerprint", table_name="data_import_rows")
    for column in ("platform_content_record_id", "status", "batch_id", "account_id", "org_id"):
        op.drop_index(op.f(f"ix_data_import_rows_{column}"), table_name="data_import_rows")
    op.drop_table("data_import_rows")

    op.drop_index(
        "uq_platform_content_records_account_canonical_share_url",
        table_name="platform_content_records",
    )
    op.drop_index(
        "uq_platform_content_records_account_external_content_id",
        table_name="platform_content_records",
    )
    op.drop_index(
        "ix_platform_content_records_weak_fingerprint",
        table_name="platform_content_records",
    )
    for column in ("canonical_import_batch_id", "platform", "account_id", "org_id"):
        op.drop_index(
            op.f(f"ix_platform_content_records_{column}"),
            table_name="platform_content_records",
        )
    op.drop_table("platform_content_records")

    op.drop_index(
        "uq_data_import_batches_active_preview_identity",
        table_name="data_import_batches",
    )
    for column in ("sha256", "batch_id", "account_id", "org_id"):
        op.drop_index(op.f(f"ix_data_artifacts_{column}"), table_name="data_artifacts")
    op.drop_table("data_artifacts")

    for column in ("status", "account_id", "org_id"):
        op.drop_index(op.f(f"ix_data_import_batches_{column}"), table_name="data_import_batches")
    op.drop_table("data_import_batches")

    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS conflict_status")
        op.execute("DROP TYPE IF EXISTS content_identity_confidence")
        op.execute("DROP TYPE IF EXISTS import_row_status")
        op.execute("DROP TYPE IF EXISTS import_batch_status")
        op.execute("DROP TYPE IF EXISTS data_source_kind")
