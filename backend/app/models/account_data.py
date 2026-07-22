"""Append-only import ledger and normalized account data projections."""

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    and_,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import BigIntPK, JSONVariant, TimestampMixin, pg_enum
from app.models.enums import (
    ConflictStatus,
    ContentIdentityConfidence,
    DataSourceKind,
    ImportBatchStatus,
    ImportRowStatus,
    Platform,
)


class DataImportBatch(Base, TimestampMixin):
    __tablename__ = "data_import_batches"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "account_id",
            "id",
            name="uq_data_import_batches_org_account_id",
        ),
        Index(
            "uq_data_import_batches_active_preview_identity",
            "org_id",
            "account_id",
            "source_kind",
            "template_code",
            "content_sha256",
            unique=True,
            sqlite_where=text("committed_at IS NULL AND revoked_at IS NULL"),
            postgresql_where=text("committed_at IS NULL AND revoked_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    source_kind: Mapped[DataSourceKind] = mapped_column(
        pg_enum(DataSourceKind, "data_source_kind"), nullable=False
    )
    status: Mapped[ImportBatchStatus] = mapped_column(
        pg_enum(ImportBatchStatus, "import_batch_status"), index=True, nullable=False
    )
    template_code: Mapped[str] = mapped_column(String(80), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    artifacts: Mapped[list["DataArtifact"]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="DataArtifact.id",
        primaryjoin=lambda: and_(
            DataImportBatch.org_id == DataArtifact.org_id,
            DataImportBatch.account_id == DataArtifact.account_id,
            DataImportBatch.id == DataArtifact.batch_id,
        ),
        foreign_keys=lambda: [
            DataArtifact.org_id,
            DataArtifact.account_id,
            DataArtifact.batch_id,
        ],
    )
    rows: Mapped[list["DataImportRow"]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="DataImportRow.row_number",
        primaryjoin=lambda: and_(
            DataImportBatch.org_id == DataImportRow.org_id,
            DataImportBatch.account_id == DataImportRow.account_id,
            DataImportBatch.id == DataImportRow.batch_id,
        ),
        foreign_keys=lambda: [
            DataImportRow.org_id,
            DataImportRow.account_id,
            DataImportRow.batch_id,
        ],
    )
    conflicts: Mapped[list["DataConflict"]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="DataConflict.id",
        primaryjoin=lambda: and_(
            DataImportBatch.org_id == DataConflict.org_id,
            DataImportBatch.account_id == DataConflict.account_id,
            DataImportBatch.id == DataConflict.batch_id,
        ),
        foreign_keys=lambda: [
            DataConflict.org_id,
            DataConflict.account_id,
            DataConflict.batch_id,
        ],
    )


class DataArtifact(Base, TimestampMixin):
    __tablename__ = "data_artifacts"
    __table_args__ = (
        ForeignKeyConstraint(
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

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    batch_id: Mapped[int] = mapped_column(BigIntPK, index=True, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)

    batch: Mapped[DataImportBatch] = relationship(
        back_populates="artifacts",
        primaryjoin=lambda: and_(
            DataArtifact.org_id == DataImportBatch.org_id,
            DataArtifact.account_id == DataImportBatch.account_id,
            DataArtifact.batch_id == DataImportBatch.id,
        ),
        foreign_keys=lambda: [
            DataArtifact.org_id,
            DataArtifact.account_id,
            DataArtifact.batch_id,
        ],
    )


class PlatformContentRecord(Base, TimestampMixin):
    __tablename__ = "platform_content_records"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "account_id",
            "id",
            name="uq_platform_content_records_org_account_id",
        ),
        Index(
            "uq_platform_content_records_account_external_content_id",
            "account_id",
            "platform",
            "external_content_id",
            unique=True,
            sqlite_where=text("external_content_id IS NOT NULL"),
            postgresql_where=text("external_content_id IS NOT NULL"),
        ),
        Index(
            "uq_platform_content_records_account_canonical_share_url",
            "account_id",
            "platform",
            "canonical_share_url",
            unique=True,
            sqlite_where=text("canonical_share_url IS NOT NULL"),
            postgresql_where=text("canonical_share_url IS NOT NULL"),
        ),
        ForeignKeyConstraint(
            ["org_id", "account_id", "canonical_import_batch_id"],
            [
                "data_import_batches.org_id",
                "data_import_batches.account_id",
                "data_import_batches.id",
            ],
            name="fk_platform_content_records_canonical_batch_scope",
        ),
        Index(
            "ix_platform_content_records_weak_fingerprint",
            "weak_fingerprint",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    platform: Mapped[Platform] = mapped_column(
        pg_enum(Platform, "platform"), index=True, nullable=False
    )
    canonical_import_batch_id: Mapped[int | None] = mapped_column(
        BigIntPK, index=True, nullable=True
    )
    external_content_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    share_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    canonical_share_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    identity_confidence: Mapped[ContentIdentityConfidence] = mapped_column(
        pg_enum(ContentIdentityConfidence, "content_identity_confidence"),
        default=ContentIdentityConfidence.UNRESOLVED,
        nullable=False,
    )
    weak_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    canonical_import_batch: Mapped[DataImportBatch | None] = relationship(
        primaryjoin=lambda: and_(
            PlatformContentRecord.org_id == DataImportBatch.org_id,
            PlatformContentRecord.account_id == DataImportBatch.account_id,
            PlatformContentRecord.canonical_import_batch_id == DataImportBatch.id,
        ),
        foreign_keys=lambda: [
            PlatformContentRecord.org_id,
            PlatformContentRecord.account_id,
            PlatformContentRecord.canonical_import_batch_id,
        ],
        viewonly=True,
    )


class DataImportRow(Base, TimestampMixin):
    __tablename__ = "data_import_rows"
    __table_args__ = (
        UniqueConstraint("batch_id", "row_number", name="uq_data_import_rows_batch_row"),
        ForeignKeyConstraint(
            ["org_id", "account_id", "batch_id"],
            [
                "data_import_batches.org_id",
                "data_import_batches.account_id",
                "data_import_batches.id",
            ],
            name="fk_data_import_rows_batch_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "account_id", "platform_content_record_id"],
            [
                "platform_content_records.org_id",
                "platform_content_records.account_id",
                "platform_content_records.id",
            ],
            name="fk_data_import_rows_content_scope",
        ),
        ForeignKeyConstraint(
            ["resolved_by_id"],
            ["users.id"],
            name="fk_data_import_rows_resolved_by_id_users",
            ondelete="SET NULL",
        ),
        Index("ix_data_import_rows_weak_fingerprint", "weak_fingerprint"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    batch_id: Mapped[int] = mapped_column(BigIntPK, index=True, nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ImportRowStatus] = mapped_column(
        pg_enum(ImportRowStatus, "import_row_status"), index=True, nullable=False
    )
    raw_values: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    normalized_values: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    field_errors: Mapped[list[dict]] = mapped_column(JSONVariant, default=list, nullable=False)
    warnings: Mapped[list[dict]] = mapped_column(JSONVariant, default=list, nullable=False)
    candidate_content_ids: Mapped[list[int]] = mapped_column(
        JSONVariant, default=list, nullable=False
    )
    projected_target_ids: Mapped[list[dict]] = mapped_column(
        JSONVariant, default=list, nullable=False
    )
    platform_content_record_id: Mapped[int | None] = mapped_column(
        BigIntPK, index=True, nullable=True
    )
    resolution_outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolved_by_id: Mapped[int | None] = mapped_column(BigIntPK, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    weak_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True)

    batch: Mapped[DataImportBatch] = relationship(
        back_populates="rows",
        primaryjoin=lambda: and_(
            DataImportRow.org_id == DataImportBatch.org_id,
            DataImportRow.account_id == DataImportBatch.account_id,
            DataImportRow.batch_id == DataImportBatch.id,
        ),
        foreign_keys=lambda: [
            DataImportRow.org_id,
            DataImportRow.account_id,
            DataImportRow.batch_id,
        ],
    )
    platform_content_record: Mapped[PlatformContentRecord | None] = relationship(
        primaryjoin=lambda: and_(
            DataImportRow.org_id == PlatformContentRecord.org_id,
            DataImportRow.account_id == PlatformContentRecord.account_id,
            DataImportRow.platform_content_record_id == PlatformContentRecord.id,
        ),
        foreign_keys=lambda: [
            DataImportRow.org_id,
            DataImportRow.account_id,
            DataImportRow.platform_content_record_id,
        ],
        viewonly=True,
    )


class AccountMetricSnapshot(Base, TimestampMixin):
    __tablename__ = "account_metric_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "import_batch_id",
            "stat_date",
            name="uq_account_metric_snapshots_batch_date",
        ),
        ForeignKeyConstraint(
            ["org_id", "account_id", "import_batch_id"],
            [
                "data_import_batches.org_id",
                "data_import_batches.account_id",
                "data_import_batches.id",
            ],
            name="fk_account_metric_snapshots_batch_scope",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    import_batch_id: Mapped[int] = mapped_column(BigIntPK, index=True, nullable=False)
    source_kind: Mapped[DataSourceKind] = mapped_column(
        pg_enum(DataSourceKind, "data_source_kind"), nullable=False
    )
    stat_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    follower_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    follower_delta: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_play: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_exposure: Mapped[int | None] = mapped_column(Integer, nullable=True)
    engagement_rate: Mapped[float | None] = mapped_column(Float, nullable=True)


class AudienceProfileSnapshot(Base, TimestampMixin):
    __tablename__ = "audience_profile_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "import_batch_id",
            "stat_date",
            "dimension",
            name="uq_audience_profile_snapshots_batch_dimension",
        ),
        UniqueConstraint(
            "org_id",
            "account_id",
            "id",
            name="uq_audience_profile_snapshots_org_account_id",
        ),
        ForeignKeyConstraint(
            ["org_id", "account_id", "import_batch_id"],
            [
                "data_import_batches.org_id",
                "data_import_batches.account_id",
                "data_import_batches.id",
            ],
            name="fk_audience_profile_snapshots_batch_scope",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    import_batch_id: Mapped[int] = mapped_column(BigIntPK, index=True, nullable=False)
    source_kind: Mapped[DataSourceKind] = mapped_column(
        pg_enum(DataSourceKind, "data_source_kind"), nullable=False
    )
    stat_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    dimension: Mapped[str] = mapped_column(String(80), nullable=False)
    total_audience: Mapped[int | None] = mapped_column(Integer, nullable=True)

    items: Mapped[list["AudienceProfileItem"]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan", order_by="AudienceProfileItem.rank"
    )


class AudienceProfileItem(Base, TimestampMixin):
    __tablename__ = "audience_profile_items"
    __table_args__ = (
        ForeignKeyConstraint(
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

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    snapshot_id: Mapped[int] = mapped_column(BigIntPK, index=True, nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    value: Mapped[str] = mapped_column(String(120), nullable=False)
    ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    rank: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    meta: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)

    snapshot: Mapped[AudienceProfileSnapshot] = relationship(
        back_populates="items",
        primaryjoin=lambda: and_(
            AudienceProfileItem.org_id == AudienceProfileSnapshot.org_id,
            AudienceProfileItem.account_id == AudienceProfileSnapshot.account_id,
            AudienceProfileItem.snapshot_id == AudienceProfileSnapshot.id,
        ),
        foreign_keys=lambda: [
            AudienceProfileItem.org_id,
            AudienceProfileItem.account_id,
            AudienceProfileItem.snapshot_id,
        ],
    )


class BenchmarkSnapshot(Base, TimestampMixin):
    __tablename__ = "benchmark_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "import_batch_id",
            "stat_date",
            "benchmark_code",
            "metric_code",
            name="uq_benchmark_snapshots_batch_metric",
        ),
        ForeignKeyConstraint(
            ["org_id", "account_id", "import_batch_id"],
            [
                "data_import_batches.org_id",
                "data_import_batches.account_id",
                "data_import_batches.id",
            ],
            name="fk_benchmark_snapshots_batch_scope",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    import_batch_id: Mapped[int] = mapped_column(BigIntPK, index=True, nullable=False)
    source_kind: Mapped[DataSourceKind] = mapped_column(
        pg_enum(DataSourceKind, "data_source_kind"), nullable=False
    )
    stat_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    benchmark_code: Mapped[str] = mapped_column(String(80), nullable=False)
    metric_code: Mapped[str] = mapped_column(String(80), nullable=False)
    metric_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    meta: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)


class DataConflict(Base, TimestampMixin):
    __tablename__ = "data_conflicts"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "row_number",
            "field_name",
            name="uq_data_conflicts_batch_row_field",
        ),
        ForeignKeyConstraint(
            ["org_id", "account_id", "batch_id"],
            [
                "data_import_batches.org_id",
                "data_import_batches.account_id",
                "data_import_batches.id",
            ],
            name="fk_data_conflicts_batch_scope",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    batch_id: Mapped[int] = mapped_column(BigIntPK, index=True, nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ConflictStatus] = mapped_column(
        pg_enum(ConflictStatus, "conflict_status"), index=True, nullable=False
    )
    field_name: Mapped[str] = mapped_column(String(120), nullable=False)
    conflict_code: Mapped[str] = mapped_column(String(80), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    existing_value: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    incoming_value: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    candidate_content_ids: Mapped[list[int]] = mapped_column(
        JSONVariant, default=list, nullable=False
    )
    resolved_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    batch: Mapped[DataImportBatch] = relationship(
        back_populates="conflicts",
        primaryjoin=lambda: and_(
            DataConflict.org_id == DataImportBatch.org_id,
            DataConflict.account_id == DataImportBatch.account_id,
            DataConflict.batch_id == DataImportBatch.id,
        ),
        foreign_keys=lambda: [
            DataConflict.org_id,
            DataConflict.account_id,
            DataConflict.batch_id,
        ],
    )
