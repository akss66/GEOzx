from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ConflictStatus, DataSourceKind, ImportBatchStatus, ImportRowStatus


class ResolveImportRowRequest(BaseModel):
    selected_content_id: int | None = None


class ImportArtifactOut(BaseModel):
    id: int
    filename: str
    content_type: str
    byte_size: int
    sha256: str
    download_url: str


class ImportConflictOut(BaseModel):
    id: int
    row_number: int
    status: ConflictStatus
    field_name: str
    conflict_code: str
    message: str
    candidate_content_ids: list[int] = Field(default_factory=list)
    resolved_by_id: int | None = None
    resolved_at: datetime | None = None


class ImportRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    row_number: int
    status: ImportRowStatus
    raw_values: dict = Field(default_factory=dict)
    normalized_values: dict = Field(default_factory=dict)
    field_errors: list[dict] = Field(default_factory=list)
    warnings: list[dict] = Field(default_factory=list)
    candidate_content_ids: list[int] = Field(default_factory=list)
    projected_target_ids: list[dict] = Field(default_factory=list)
    platform_content_record_id: int | None = None
    resolution_outcome: str | None = None
    resolved_by_id: int | None = None
    resolved_at: datetime | None = None


class ImportBatchSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: ImportBatchStatus
    source_kind: DataSourceKind
    template_code: str
    row_count: int
    period_start: date | None = None
    period_end: date | None = None
    committed_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime


class ImportBatchOut(ImportBatchSummaryOut):
    artifacts: list[ImportArtifactOut] = Field(default_factory=list)
    rows: list[ImportRowOut] = Field(default_factory=list)
    conflicts: list[ImportConflictOut] = Field(default_factory=list)


class ImportBatchListOut(BaseModel):
    items: list[ImportBatchSummaryOut] = Field(default_factory=list)


class AccountDataStatusSourceOut(BaseModel):
    batch_id: int
    source_kind: DataSourceKind
    template_code: str
    data_domain: str
    committed_at: datetime
    period_start: date | None = None
    period_end: date | None = None


class AccountDataStatusOut(BaseModel):
    account_id: int
    latest_confirmed_at: datetime | None = None
    coverage: dict[str, str]
    sources: list[AccountDataStatusSourceOut] = Field(default_factory=list)
