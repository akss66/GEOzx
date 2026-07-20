"""Configuration schemas for model routing and provider governance."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ModelConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_code: str
    primary_provider_id: int | None
    fallback_provider_id: int | None
    primary_model: str
    fallback_model: str | None


class UpdateModelConfigRequest(BaseModel):
    primary_model: str | None = Field(default=None, min_length=1, max_length=128)
    fallback_model: str | None = Field(default=None, max_length=128)


ProviderCode = str
CallStatus = Literal["ok", "error"]


class ModelProviderOut(BaseModel):
    code: ProviderCode
    name: str
    kind: Literal["direct", "router"]
    enabled: bool
    credential_ref: str | None
    credential_configured: bool | None
    runtime_ready: bool
    endpoint: str | None
    supported_models: list[str]
    note: str
    updated_at: datetime | None


class UpdateModelProviderRequest(BaseModel):
    enabled: bool
    credential_ref: str | None = Field(default=None, max_length=160)


class ModelRouteOut(BaseModel):
    id: int | None
    agent_code: str
    agent_name: str
    primary_model: str
    fallback_model: str | None
    temperature: float
    max_tokens: int
    timeout_seconds: int
    updated_at: datetime | None


class UpdateModelRouteRequest(BaseModel):
    primary_model: str = Field(min_length=1, max_length=128)
    fallback_model: str | None = Field(default=None, max_length=128)
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(ge=256, le=32768)
    timeout_seconds: int = Field(ge=5, le=300)

    @model_validator(mode="after")
    def reject_same_fallback(self):
        if self.fallback_model and self.fallback_model == self.primary_model:
            raise ValueError("fallback model must differ from primary model")
        return self


class ModelInfrastructureSummaryOut(BaseModel):
    providers_total: int
    providers_ready: int
    routes_total: int
    routes_with_fallback: int
    calls_24h: int
    failures_24h: int


class ModelInfrastructureOut(BaseModel):
    summary: ModelInfrastructureSummaryOut
    providers: list[ModelProviderOut]
    routes: list[ModelRouteOut]


class ModelCallOut(BaseModel):
    id: int
    agent_code: str | None
    agent_name: str
    provider: str
    model: str
    total_tokens: int
    cost_usd: float
    latency_ms: int
    status: CallStatus
    error_summary: str | None
    created_at: datetime


class ModelCallPageOut(BaseModel):
    total: int
    items: list[ModelCallOut]


class ModelProviderTemplateOut(BaseModel):
    code: str
    display_name: str
    base_url: str
    protocol: str
    models: list[str]


class ModelProviderRouteRefOut(BaseModel):
    agent_code: str
    agent_name: str


class ModelProviderDetailOut(BaseModel):
    id: int
    code: str
    display_name: str
    provider_type: str
    template_code: str | None
    protocol: str
    base_url: str | None
    enabled: bool
    sort_order: int
    credential_source: str
    key_configured: bool
    key_last_four: str | None
    key_fingerprint: str | None
    verification_status: str
    verified_at: datetime | None
    verification_error_code: str | None
    models: list[str] | None
    models_updated_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None
    referenced_agents: list[ModelProviderRouteRefOut] = Field(default_factory=list)


class CreateModelProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_code: str | None = Field(default=None, min_length=1, max_length=64)
    provider_type: Literal["custom_openai"] | None = None
    code: str | None = Field(default=None, min_length=1, max_length=128)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, min_length=8, max_length=1000)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_shape(self):
        if self.template_code:
            return self
        if self.provider_type != "custom_openai":
            raise ValueError("provider_type must be custom_openai when template_code is absent")
        if not self.display_name or not self.base_url:
            raise ValueError("custom providers require display_name and base_url")
        return self


class PatchModelProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, min_length=8, max_length=1000)
    enabled: bool | None = None

    @model_validator(mode="after")
    def require_change(self):
        if self.display_name is None and self.base_url is None and self.enabled is None:
            raise ValueError("at least one field must be provided")
        return self


class PutModelProviderCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(min_length=1, max_length=4096)


class PutModelProviderModelsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: list[str] = Field(min_length=1, max_length=128)

    @field_validator("models")
    @classmethod
    def normalize_models(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            candidate = item.strip()
            if not candidate:
                raise ValueError("model names must not be empty")
            if len(candidate) > 128:
                raise ValueError("model names must be at most 128 characters")
            if candidate not in seen:
                seen.add(candidate)
                normalized.append(candidate)
        return normalized


class ModelProviderVerifyOut(BaseModel):
    provider_id: int
    verification_status: str
    verification_error_code: str | None
    verified_at: datetime | None
    latency_ms: int


class ModelProviderDiscoveryOut(BaseModel):
    provider_id: int
    models: list[str]
    models_updated_at: datetime | None
    error_code: str | None
