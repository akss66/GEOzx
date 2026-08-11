"""Typed contracts for the client-scoped knowledge workspace."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator

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


KnowledgeBaseKind = Literal["brand", "organization_shared"]
KnowledgeEntryKind = Literal[
    "document", "product_fact", "policy", "case", "brand_voice", "asset_reference"
]
KnowledgeVerificationStatus = Literal["draft", "verified", "rejected", "expired"]


class KnowledgeBaseCreateRequest(BaseModel):
    kind: KnowledgeBaseKind
    client_id: int | None = Field(default=None, gt=0)
    name: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=50_000)

    @model_validator(mode="after")
    def validate_scope(self) -> "KnowledgeBaseCreateRequest":
        if (self.kind == "brand") != (self.client_id is not None):
            raise ValueError("brand bases require client_id; organization_shared bases forbid it")
        return self


class KnowledgeBaseUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=50_000)


class KnowledgeBaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    org_id: int
    client_id: int | None
    kind: KnowledgeBaseKind
    name: str
    description: str | None
    status: str
    version: int
    created_by_id: int | None
    created_at: datetime
    updated_at: datetime


class PaginationOut(BaseModel):
    limit: int
    offset: int
    total: int


class KnowledgeBaseListOut(BaseModel):
    data: list[KnowledgeBaseOut]
    pagination: PaginationOut


class ProductFactPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    kind: Literal["product_fact"]
    product_code: str = Field(min_length=1, max_length=128)
    fact_key: str = Field(min_length=1, max_length=128)
    value: int | float
    unit: str | None = Field(default=None, min_length=1, max_length=64)
    claim_text: str = Field(min_length=1, max_length=2000)
    allowed_for_external_claim: bool = False


class PolicyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    kind: Literal["policy"]


class CasePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    kind: Literal["case"]


class BrandVoicePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    kind: Literal["brand_voice"]


class GenericKnowledgePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    kind: Literal["document", "asset_reference"]


KnowledgeEntryPayload = Annotated[
    ProductFactPayload | PolicyPayload | CasePayload | BrandVoicePayload | GenericKnowledgePayload,
    Field(discriminator="kind"),
]


class CreateKnowledgeBaseEntryRequest(BaseModel):
    category: KnowledgeCategory
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=50_000)
    payload: KnowledgeEntryPayload
    source_label: str = Field(min_length=1, max_length=300)
    source_url: AnyHttpUrl | None = None
    tags: list[str] | None = None
    entry_kind: KnowledgeEntryKind
    source_attachment_id: int | None = Field(default=None, gt=0)
    effective_at: datetime | None = None
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_kind_matches_payload(self) -> "CreateKnowledgeBaseEntryRequest":
        if self.entry_kind != self.payload.kind:
            raise ValueError("entry_kind must match payload.kind")
        return self


class UpdateKnowledgeBaseEntryRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    content: str | None = Field(default=None, min_length=1, max_length=50_000)
    payload: KnowledgeEntryPayload | None = None
    source_label: str | None = Field(default=None, min_length=1, max_length=300)
    source_url: AnyHttpUrl | None = None
    tags: list[str] | None = None
    verification_status: KnowledgeVerificationStatus | None = None
    effective_at: datetime | None = None
    expires_at: datetime | None = None
    allowed_for_external_claim: bool | None = None


class KnowledgeBaseEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    knowledge_base_id: int | None
    category: KnowledgeCategory
    title: str
    content: str
    payload: dict
    tags: list[str] | None
    source_label: str
    source_url: str | None
    entry_kind: str
    verification_status: str
    source_attachment_id: int | None
    effective_at: datetime | None
    expires_at: datetime | None
    allowed_for_external_claim: bool
    version: int
    created_by_id: int | None
    verified_by_id: int | None
    verified_at: datetime | None
    created_at: datetime
    updated_at: datetime


class KnowledgeBaseEntryListOut(BaseModel):
    data: list[KnowledgeBaseEntryOut]
    pagination: PaginationOut


class BindAccountKnowledgeRequest(BaseModel):
    knowledge_base_id: int = Field(gt=0)


class AccountKnowledgeBindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    knowledge_base_id: int
    knowledge_base_kind: str
    client_id: int | None
    binding_type: str
    status: str
    bound_by_id: int | None
    bound_at: datetime
