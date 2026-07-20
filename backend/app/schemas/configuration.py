"""模型基础设施 schema：供应商、Agent 路由策略和调用账本。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
            raise ValueError("兜底模型不能与首选模型相同")
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
