"""Typed contracts for durable platform publishing jobs."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import Platform
from app.models.publishing import PlatformPublishJobStatus
from app.schemas.orchestrator import PublishPackageOut


class CreatePublishJobRequest(BaseModel):
    account_id: int
    active_client_id: int | None = None
    active_project_id: int | None = None
    tool_call_id: int
    idempotency_key: str = Field(min_length=8, max_length=160)
    publish_package: PublishPackageOut


class PublishJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    org_id: int
    account_id: int
    active_client_id: int | None
    active_project_id: int | None
    created_by_id: int | None
    brain_task_id: int | None
    tool_call_id: int | None
    platform_content_record_id: int | None
    platform: Platform
    status: PlatformPublishJobStatus
    idempotency_key: str
    publish_package: dict[str, Any]
    capabilities_snapshot: dict[str, Any]
    approval_snapshot: dict[str, Any]
    share_id: str | None
    posting_task_id: str | None
    external_video_id: str | None
    external_item_id: str | None
    expires_at: datetime | None
    handoff_started_at: datetime | None
    bound_at: datetime | None
    retry_count: int
    next_retry_at: datetime | None
    last_error_code: str | None
    last_error_message: str | None
    last_platform_log_id: str | None
    created_at: datetime
    updated_at: datetime


class PublishHandoffOut(BaseModel):
    job: PublishJobOut
    schema_url: str
    expires_at: datetime


class DouyinCreateVideoContent(BaseModel):
    share_id: str = Field(min_length=1, max_length=160)
    item_id: str | None = Field(default=None, max_length=160)
    video_id: str | None = Field(default=None, max_length=160)
    has_default_hashtag: bool | None = None

    @field_validator("item_id", "video_id")
    @classmethod
    def normalize_optional_id(cls, value: str | None) -> str | None:
        normalized = (value or "").strip()
        return normalized or None


class DouyinCreateVideoCallback(BaseModel):
    event: Literal["create_video"] = "create_video"
    from_user_id: str = Field(min_length=1, max_length=160)
    client_key: str = Field(min_length=1, max_length=160)
    log_id: str | None = Field(default=None, max_length=200)
    content: DouyinCreateVideoContent
    event_time: datetime | None = None
