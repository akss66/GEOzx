from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    ImportBatchStatus,
    ImportFileStatus,
    ImportJobStatus,
)


class ImportJobDatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    template_code: str
    sheet_name: str | None = None
    dataset_ordinal: int | None = None
    status: ImportBatchStatus
    row_count: int


class ImportJobFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    retry_of_file_id: int | None = None
    ordinal: int
    filename: str
    content_type: str
    byte_size: int
    sha256: str
    status: ImportFileStatus
    error_payload: dict = Field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    datasets: list[ImportJobDatasetOut] = Field(default_factory=list)


class ImportJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    client_request_id: str
    status: ImportJobStatus
    file_count: int
    completed_file_count: int
    failed_file_count: int
    started_at: datetime | None = None
    completed_at: datetime | None = None
    files: list[ImportJobFileOut] = Field(default_factory=list)
