"""Approval-bound publishing Skill contract.

The Skill never treats a generated handoff as a successful publication.  A
platform callback is the only source that can move a receipt to ``published``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.skills import SkillDefinition


class ContentPublishingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved_publish_artifact_id: int = Field(gt=0)
    scheduled_at: datetime | None = None
    visibility: Literal["public", "friends", "private"] = "public"
    allow_comment: bool = True


class ContentPublishingReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: Literal["platform_publish_receipt"] = "platform_publish_receipt"
    account_id: int = Field(gt=0)
    source_artifact_id: int = Field(gt=0)
    source_artifact_version: int = Field(gt=0)
    platform_receipt_id: int | None = Field(default=None, gt=0)
    status: Literal[
        "handoff_ready",
        "waiting_platform_confirmation",
        "published",
        "blocked",
        "failed",
    ]
    published_at: datetime | None = None
    retryable: bool = False
    connection_state: Literal["connected", "needs_connection"] = "connected"
    reason: str | None = None


CONTENT_PUBLISHING_SKILL = SkillDefinition(
    code="content_publishing",
    version=1,
    name="内容发布",
    description="将已审批且版本未变化的发布包交给官方平台通道，并返回可核验的平台回执状态。",
    supported_platforms=frozenset({"douyin"}),
    input_model=ContentPublishingInput,
    output_model=ContentPublishingReceipt,
    expert_codes=(),
    expert_stages=(),
    tool_codes=("platform.content_publish",),
    critic_policy="none",
    risk_level="high",
    approval_policy="before_tools",
    artifact_type="platform_publish_receipt",
)


__all__ = [
    "CONTENT_PUBLISHING_SKILL",
    "ContentPublishingInput",
    "ContentPublishingReceipt",
]
