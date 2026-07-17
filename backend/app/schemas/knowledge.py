"""Typed contracts for the client-scoped knowledge workspace."""

from datetime import datetime
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from app.models.enums import KnowledgeCategory

KnowledgeSourceType = Literal["manual", "agent", "deliverable", "external"]
KnowledgeStatus = Literal["active", "archived"]
SuggestionStatus = Literal["pending", "approved", "rejected"]


class CreateKnowledgeRequest(BaseModel):
    client_id: int = Field(gt=0)
    project_id: int | None = Field(default=None, gt=0)
    category: KnowledgeCategory
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=50_000)
    payload: dict = Field(default_factory=dict)
    tags: list[str] | None = None
    source_type: KnowledgeSourceType = "manual"
    source_label: str = Field(min_length=1, max_length=300)
    source_url: AnyHttpUrl | None = None


class UpdateKnowledgeRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    content: str | None = Field(default=None, min_length=1, max_length=50_000)
    payload: dict | None = None
    tags: list[str] | None = None
    source_type: KnowledgeSourceType | None = None
    source_label: str | None = Field(default=None, min_length=1, max_length=300)
    source_url: AnyHttpUrl | None = None
    status: KnowledgeStatus | None = None


class KnowledgeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    client_id: int
    project_id: int | None
    category: KnowledgeCategory
    title: str
    content: str
    payload: dict
    tags: list[str] | None
    source_type: str
    source_label: str
    source_url: str | None
    version: int
    status: str
    created_by_id: int | None
    created_at: datetime
    updated_at: datetime


class CreateKnowledgeSuggestionRequest(BaseModel):
    client_id: int = Field(gt=0)
    project_id: int | None = Field(default=None, gt=0)
    category: KnowledgeCategory
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=50_000)
    payload: dict = Field(default_factory=dict)
    tags: list[str] | None = None
    source_agent_code: str = Field(min_length=1, max_length=64)
    source_label: str = Field(min_length=1, max_length=300)
    source_task_id: int | None = Field(default=None, gt=0)
    source_deliverable_id: int | None = Field(default=None, gt=0)


class ReviewKnowledgeSuggestionRequest(BaseModel):
    review_note: str | None = Field(default=None, max_length=2000)


class KnowledgeSuggestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    client_id: int
    project_id: int | None
    category: KnowledgeCategory
    title: str
    content: str
    payload: dict
    tags: list[str] | None
    source_agent_code: str
    source_label: str
    source_task_id: int | None
    source_deliverable_id: int | None
    status: str
    reviewed_by_id: int | None
    reviewed_at: datetime | None
    review_note: str | None
    accepted_entry_id: int | None
    created_at: datetime


class KnowledgeSuggestionApprovalOut(BaseModel):
    suggestion: KnowledgeSuggestionOut
    entry: KnowledgeOut


class KnowledgeCitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entry_id: int
    project_id: int | None
    task_id: int | None
    invocation_id: int | None
    agent_code: str
    context: str
    created_at: datetime
