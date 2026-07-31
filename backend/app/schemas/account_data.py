from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import ConflictStatus, DataSourceKind, ImportBatchStatus, ImportRowStatus


class ResolveImportRowRequest(BaseModel):
    selected_content_id: int | None = None
    confirmed: bool | None = None


class ManualAccountMetrics(BaseModel):
    follower_count: int | None = Field(default=None, ge=0)
    follower_delta: int | None = None
    total_play: int | None = Field(default=None, ge=0)
    total_exposure: int | None = Field(default=None, ge=0)
    engagement_rate: float | None = Field(default=None, ge=0, le=1)


class ManualAudienceItem(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=120)
    ratio: float | None = Field(default=None, ge=0, le=1)


class ManualBenchmarkMetric(BaseModel):
    metric_code: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_]+$")
    metric_value: float | None = None
    sample_size: int | None = Field(default=None, ge=0)


class ManualPreviewRequest(BaseModel):
    data_domain: Literal["account_period_totals", "audience_dimension", "benchmark"]
    stat_date: date
    period_start: date | None = None
    period_end: date | None = None
    account_metrics: ManualAccountMetrics | None = None
    dimension: str | None = Field(default=None, min_length=1, max_length=80)
    total_audience: int | None = Field(default=None, ge=0)
    audience_items: list[ManualAudienceItem] = Field(default_factory=list, max_length=100)
    benchmark_code: str | None = Field(default=None, min_length=1, max_length=80)
    benchmark_metrics: list[ManualBenchmarkMetric] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_domain_payload(self):
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError("period_start must not be after period_end")
        if self.data_domain == "account_period_totals":
            if self.account_metrics is None:
                raise ValueError("account_metrics is required for account_period_totals")
            if all(value is None for value in self.account_metrics.model_dump().values()):
                raise ValueError("at least one account metric is required")
        elif self.data_domain == "audience_dimension":
            if not self.dimension or not self.audience_items:
                raise ValueError("dimension and audience_items are required for audience_dimension")
        elif not self.benchmark_code or not self.benchmark_metrics:
            raise ValueError("benchmark_code and benchmark_metrics are required for benchmark")
        return self


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
    created_by_id: int | None = None
    created_by_name: str | None = None
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


ImportRowView = Literal["all", "ready", "needs_work"]


class ImportRowPageOut(BaseModel):
    items: list[ImportRowOut] = Field(default_factory=list)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=200)
    total_count: int = Field(ge=0)
    filtered_count: int = Field(ge=0)
    ready_count: int = Field(ge=0)
    blocking_count: int = Field(ge=0)
    total_pages: int = Field(ge=0)


class AccountDataStatusSourceOut(BaseModel):
    batch_id: int
    source_kind: DataSourceKind
    template_code: str
    data_domain: str
    committed_at: datetime
    period_start: date | None = None
    period_end: date | None = None


class AccountDataDatasetStatusOut(BaseModel):
    data_domain: str
    status: Literal["not_imported", "available", "stale", "processing", "failed"]
    confirmed_period_start: date | None = None
    confirmed_period_end: date | None = None
    latest_source: AccountDataStatusSourceOut | None = None


class AccountDataStatusOut(BaseModel):
    account_id: int
    latest_confirmed_at: datetime | None = None
    coverage: dict[str, str]
    sources: list[AccountDataStatusSourceOut] = Field(default_factory=list)
    dataset_inventory: list[AccountDataDatasetStatusOut] = Field(default_factory=list)
