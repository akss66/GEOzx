"""Add durable bulk-import jobs and field-level observations.

Revision ID: 20260731_0200
Revises: 20260731_0100
Create Date: 2026-07-31 02:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260731_0200"
down_revision: str | None = "20260731_0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

import_job_status = sa.Enum(
    "queued",
    "processing",
    "completed",
    "completed_with_errors",
    "failed",
    name="import_job_status",
)
import_file_status = sa.Enum(
    "queued",
    "processing",
    "completed",
    "partially_completed",
    "failed",
    name="import_file_status",
)
data_source_kind = postgresql.ENUM(
    "official_api",
    "platform_export",
    "screenshot_verified",
    "manual_entry",
    name="data_source_kind",
    create_type=False,
)


def upgrade() -> None:
    op.create_table(
        "data_import_jobs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.Column("client_request_id", sa.String(length=120), nullable=False),
        sa.Column("status", import_job_status, nullable=False),
        sa.Column("file_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completed_file_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_file_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("file_count >= 0", name="ck_data_import_jobs_file_count_nonnegative"),
        sa.CheckConstraint(
            "completed_file_count >= 0",
            name="ck_data_import_jobs_completed_count_nonnegative",
        ),
        sa.CheckConstraint(
            "failed_file_count >= 0",
            name="ck_data_import_jobs_failed_count_nonnegative",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "account_id",
            "client_request_id",
            name="uq_data_import_jobs_client_request",
        ),
        sa.UniqueConstraint(
            "org_id",
            "account_id",
            "id",
            name="uq_data_import_jobs_org_account_id",
        ),
    )
    op.create_index("ix_data_import_jobs_account_id", "data_import_jobs", ["account_id"])
    op.create_index("ix_data_import_jobs_org_id", "data_import_jobs", ["org_id"])
    op.create_index("ix_data_import_jobs_status", "data_import_jobs", ["status"])

    op.create_table(
        "data_import_files",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("retry_of_file_id", sa.BigInteger(), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("status", import_file_status, nullable=False),
        sa.Column(
            "error_payload",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("byte_size >= 0", name="ck_data_import_files_byte_size_nonnegative"),
        sa.CheckConstraint("ordinal >= 1", name="ck_data_import_files_ordinal_positive"),
        sa.ForeignKeyConstraint(
            ["org_id", "account_id", "job_id"],
            ["data_import_jobs.org_id", "data_import_jobs.account_id", "data_import_jobs.id"],
            name="fk_data_import_files_job_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "account_id", "retry_of_file_id"],
            ["data_import_files.org_id", "data_import_files.account_id", "data_import_files.id"],
            name="fk_data_import_files_retry_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "ordinal", name="uq_data_import_files_job_ordinal"),
        sa.UniqueConstraint(
            "org_id",
            "account_id",
            "id",
            name="uq_data_import_files_org_account_id",
        ),
        sa.UniqueConstraint(
            "org_id",
            "account_id",
            "job_id",
            "id",
            name="uq_data_import_files_job_scope_id",
        ),
    )
    op.create_index("ix_data_import_files_account_id", "data_import_files", ["account_id"])
    op.create_index("ix_data_import_files_job_id", "data_import_files", ["job_id"])
    op.create_index("ix_data_import_files_org_id", "data_import_files", ["org_id"])
    op.create_index("ix_data_import_files_sha256", "data_import_files", ["sha256"])
    op.create_index("ix_data_import_files_status", "data_import_files", ["status"])

    with op.batch_alter_table("data_import_rows") as batch_op:
        batch_op.create_unique_constraint(
            "uq_data_import_rows_scope_id",
            ["org_id", "account_id", "batch_id", "id"],
        )

    with op.batch_alter_table("data_import_batches") as batch_op:
        batch_op.add_column(sa.Column("job_id", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("job_file_id", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("sheet_name", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("dataset_ordinal", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("confirmed_sequence", sa.BigInteger(), nullable=True))
        batch_op.create_foreign_key(
            "fk_data_import_batches_job_scope",
            "data_import_jobs",
            ["org_id", "account_id", "job_id"],
            ["org_id", "account_id", "id"],
            ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            "fk_data_import_batches_job_file_scope",
            "data_import_files",
            ["org_id", "account_id", "job_id", "job_file_id"],
            ["org_id", "account_id", "job_id", "id"],
            ondelete="CASCADE",
        )
        batch_op.create_unique_constraint(
            "uq_data_import_batches_file_dataset_ordinal",
            ["job_file_id", "dataset_ordinal"],
        )
        batch_op.create_index("ix_data_import_batches_job_id", ["job_id"])
        batch_op.create_index("ix_data_import_batches_job_file_id", ["job_file_id"])
        batch_op.create_index(
            "ix_data_import_batches_confirmed_sequence",
            ["confirmed_sequence"],
        )

    op.create_table(
        "data_field_observations",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("import_batch_id", sa.BigInteger(), nullable=False),
        sa.Column("import_row_id", sa.BigInteger(), nullable=True),
        sa.Column("domain", sa.String(length=80), nullable=False),
        sa.Column("entity_key", sa.String(length=500), nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("field_name", sa.String(length=120), nullable=False),
        sa.Column(
            "value",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("source_kind", data_source_kind, nullable=False),
        sa.Column("source_priority", sa.Integer(), nullable=False),
        sa.Column("confirmed_sequence", sa.BigInteger(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "confirmed_sequence >= 0",
            name="ck_data_field_observations_confirmation_nonnegative",
        ),
        sa.CheckConstraint(
            "source_priority >= 0",
            name="ck_data_field_observations_source_priority_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "account_id", "import_batch_id"],
            [
                "data_import_batches.org_id",
                "data_import_batches.account_id",
                "data_import_batches.id",
            ],
            name="fk_data_field_observations_batch_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "account_id", "import_batch_id", "import_row_id"],
            [
                "data_import_rows.org_id",
                "data_import_rows.account_id",
                "data_import_rows.batch_id",
                "data_import_rows.id",
            ],
            name="fk_data_field_observations_row_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "import_batch_id",
            "import_row_id",
            "domain",
            "entity_key",
            "stat_date",
            "field_name",
            name="uq_data_field_observations_source_field",
        ),
    )
    op.create_index(
        "ix_data_field_observations_active_field",
        "data_field_observations",
        ["account_id", "domain", "entity_key", "stat_date", "field_name", "active"],
    )
    op.create_index(
        "ix_data_field_observations_account_id",
        "data_field_observations",
        ["account_id"],
    )
    op.create_index(
        "ix_data_field_observations_import_batch_id",
        "data_field_observations",
        ["import_batch_id"],
    )
    op.create_index(
        "ix_data_field_observations_import_row_id",
        "data_field_observations",
        ["import_row_id"],
    )
    op.create_index("ix_data_field_observations_org_id", "data_field_observations", ["org_id"])
    op.create_index(
        "ix_data_field_observations_stat_date",
        "data_field_observations",
        ["stat_date"],
    )
    op.create_table(
        "account_data_backfill_checkpoints",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "checkpoint_name",
            sa.String(length=120),
            server_default="field_observation_v1",
            nullable=False,
        ),
        sa.Column("last_committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_batch_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "processed_batch_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "processed_batch_count >= 0",
            name="ck_account_data_backfill_processed_nonnegative",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "account_id",
            "checkpoint_name",
            name="uq_account_data_backfill_checkpoint_scope",
        ),
    )
    op.create_index(
        "ix_account_data_backfill_checkpoints_account_id",
        "account_data_backfill_checkpoints",
        ["account_id"],
    )
    op.create_index(
        "ix_account_data_backfill_checkpoints_org_id",
        "account_data_backfill_checkpoints",
        ["org_id"],
    )


def downgrade() -> None:
    op.drop_table("account_data_backfill_checkpoints")
    op.drop_table("data_field_observations")

    with op.batch_alter_table("data_import_batches") as batch_op:
        batch_op.drop_index("ix_data_import_batches_confirmed_sequence")
        batch_op.drop_index("ix_data_import_batches_job_file_id")
        batch_op.drop_index("ix_data_import_batches_job_id")
        batch_op.drop_constraint(
            "uq_data_import_batches_file_dataset_ordinal",
            type_="unique",
        )
        batch_op.drop_constraint(
            "fk_data_import_batches_job_file_scope",
            type_="foreignkey",
        )
        batch_op.drop_constraint("fk_data_import_batches_job_scope", type_="foreignkey")
        batch_op.drop_column("confirmed_sequence")
        batch_op.drop_column("dataset_ordinal")
        batch_op.drop_column("sheet_name")
        batch_op.drop_column("job_file_id")
        batch_op.drop_column("job_id")

    with op.batch_alter_table("data_import_rows") as batch_op:
        batch_op.drop_constraint("uq_data_import_rows_scope_id", type_="unique")

    op.drop_table("data_import_files")
    op.drop_table("data_import_jobs")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        import_file_status.drop(bind, checkfirst=True)
        import_job_status.drop(bind, checkfirst=True)
