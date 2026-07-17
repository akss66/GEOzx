"""Administrator-only model infrastructure policies and safe presentation."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Event, IntegrationConfig, LLMCall, ModelConfig
from app.models.enums import AgentCode

AGENT_NAMES: dict[str, str] = {
    AgentCode.DECISION.value: "运营大脑",
    AgentCode.POSITIONING.value: "账号定位专家",
    AgentCode.CONTENT_DIRECTOR.value: "编导文案专家",
    AgentCode.ART_DIRECTOR.value: "美术提示词专家",
    AgentCode.VIDEO_CREATOR.value: "视频创作专家",
    AgentCode.EDITOR.value: "剪辑专家",
    AgentCode.OPERATOR.value: "账号运营专家",
    AgentCode.ADVERTISER.value: "投流专家",
    AgentCode.CUSTOMER_SERVICE.value: "客服反馈专家",
}

PROVIDERS: dict[str, dict[str, Any]] = {
    "deepseek": {
        "name": "DeepSeek",
        "kind": "direct",
        "default_enabled": True,
        "default_ref": "env:DEEPSEEK_API_KEY",
        "allowed_refs": {
            "env:DEEPSEEK_API_KEY",
            "vault://dyflow/llm/deepseek-api-key",
        },
        "endpoint": settings.deepseek_base_url,
        "supported_models": ["deepseek-chat", "deepseek-reasoner"],
        "note": "直接连接 DeepSeek API；密钥值仅由服务器运行时解析。",
    },
    "litellm": {
        "name": "LiteLLM 路由",
        "kind": "router",
        "default_enabled": False,
        "default_ref": None,
        "allowed_refs": set(),
        "endpoint": None,
        "supported_models": ["litellm:<provider>/<model>"],
        "note": "用于路由其他模型；目标供应商凭证由服务器环境管理。",
    },
}

ROUTING_DEFAULTS = {
    "temperature": 0.4,
    "max_tokens": 4096,
    "timeout_seconds": 90,
}

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{4,}", re.IGNORECASE),
    re.compile(r"(?i)(bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;]+"),
)


def provider_code_for_model(model: str) -> str:
    return "litellm" if model.startswith("litellm:") else "deepseek"


def require_provider(provider: str) -> dict[str, Any]:
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型供应商不存在")
    return spec


async def provider_rows(session: AsyncSession, org_id: int) -> list[dict[str, Any]]:
    stored = {
        row.provider: row
        for row in (
            await session.scalars(
                select(IntegrationConfig).where(IntegrationConfig.org_id == org_id)
            )
        ).all()
    }
    return [_provider_row(code, stored.get(code)) for code in PROVIDERS]


def _provider_row(code: str, stored: IntegrationConfig | None) -> dict[str, Any]:
    spec = PROVIDERS[code]
    enabled = stored.enabled if stored is not None else spec["default_enabled"]
    credentials = dict(stored.credentials or {}) if stored is not None else {}
    credential_ref = credentials.get("api_key_ref", spec["default_ref"])
    configured = _credential_configured(code, credential_ref)
    runtime_ready = enabled and (configured is not False)
    return {
        "code": code,
        "name": spec["name"],
        "kind": spec["kind"],
        "enabled": enabled,
        "credential_ref": credential_ref,
        "credential_configured": configured,
        "runtime_ready": runtime_ready,
        "endpoint": spec["endpoint"],
        "supported_models": spec["supported_models"],
        "note": spec["note"],
        "updated_at": stored.updated_at if stored is not None else None,
    }


def _credential_configured(provider: str, credential_ref: str | None) -> bool | None:
    if PROVIDERS[provider]["kind"] == "router":
        return None
    return bool(resolve_provider_secret(provider, credential_ref, required=False))


def resolve_provider_secret(
    provider: str, credential_ref: str | None, *, required: bool = True
) -> str | None:
    spec = require_provider(provider)
    if spec["kind"] == "router":
        return None
    ref = credential_ref or spec["default_ref"]
    if ref not in spec["allowed_refs"]:
        if required:
            raise RuntimeError("model provider credential reference is not allowed")
        return None
    value: str | None = None
    if ref == "env:DEEPSEEK_API_KEY":
        value = os.environ.get("DEEPSEEK_API_KEY") or settings.deepseek_api_key
    elif ref == "vault://dyflow/llm/deepseek-api-key":
        value = os.environ.get("DYFLOW_LLM_DEEPSEEK_API_KEY") or settings.deepseek_api_key
    if not value and required:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")
    return value or None


async def provider_runtime(
    session: AsyncSession, org_id: int | None, provider: str
) -> dict[str, str | None]:
    spec = require_provider(provider)
    stored = None
    if org_id is not None:
        stored = await session.scalar(
            select(IntegrationConfig).where(
                IntegrationConfig.org_id == org_id,
                IntegrationConfig.provider == provider,
            )
        )
    row = _provider_row(provider, stored)
    if not row["enabled"]:
        raise RuntimeError(f"model provider {provider} is disabled")
    return {
        "api_key": resolve_provider_secret(provider, row["credential_ref"]),
        "base_url": spec["endpoint"],
    }


async def save_provider(
    session: AsyncSession,
    *,
    org_id: int,
    user_id: int,
    provider: str,
    enabled: bool,
    credential_ref: str | None,
) -> dict[str, Any]:
    spec = require_provider(provider)
    if credential_ref not in spec["allowed_refs"] and credential_ref is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="只允许保存平台认可的服务器密钥引用，不能提交明文密钥",
        )
    if spec["kind"] == "direct" and not credential_ref:
        credential_ref = spec["default_ref"]
    row = await session.scalar(
        select(IntegrationConfig).where(
            IntegrationConfig.org_id == org_id,
            IntegrationConfig.provider == provider,
        )
    )
    if row is None:
        row = IntegrationConfig(org_id=org_id, provider=provider)
        session.add(row)
    row.enabled = enabled
    row.credentials = {"api_key_ref": credential_ref} if credential_ref else None
    session.add(
        Event(
            type="model.provider.updated",
            payload={
                "org_id": org_id,
                "provider": provider,
                "enabled": enabled,
                "credential_ref": credential_ref,
                "updated_by": user_id,
            },
        )
    )
    await session.commit()
    await session.refresh(row)
    return _provider_row(provider, row)


async def route_rows(session: AsyncSession, org_id: int) -> list[dict[str, Any]]:
    stored = {
        row.agent_code: row
        for row in (
            await session.scalars(select(ModelConfig).where(ModelConfig.org_id == org_id))
        ).all()
    }
    return [_route_row(code, stored.get(code)) for code in AGENT_NAMES]


def _route_row(code: str, stored: ModelConfig | None) -> dict[str, Any]:
    params = dict(stored.params or {}) if stored is not None else {}
    routing = {**ROUTING_DEFAULTS, **dict(params.get("routing_config") or {})}
    return {
        "id": stored.id if stored is not None else None,
        "agent_code": code,
        "agent_name": AGENT_NAMES[code],
        "primary_model": stored.primary_model if stored is not None else settings.llm_default_model,
        "fallback_model": stored.fallback_model if stored is not None else None,
        "temperature": float(routing["temperature"]),
        "max_tokens": int(routing["max_tokens"]),
        "timeout_seconds": int(routing["timeout_seconds"]),
        "updated_at": stored.updated_at if stored is not None else None,
    }


def validate_model_name(model: str | None) -> None:
    if model is None:
        return
    if model in {"deepseek-chat", "deepseek-reasoner"}:
        return
    if model.startswith("litellm:") and len(model.removeprefix("litellm:").strip()) >= 3:
        return
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="模型必须是已支持的 DeepSeek 模型或 litellm:<provider>/<model>",
    )


async def save_route(
    session: AsyncSession,
    *,
    org_id: int,
    user_id: int,
    agent_code: str,
    primary_model: str,
    fallback_model: str | None,
    temperature: float,
    max_tokens: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    if agent_code not in AGENT_NAMES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="专家不存在")
    validate_model_name(primary_model)
    validate_model_name(fallback_model)
    row = await session.scalar(
        select(ModelConfig).where(
            ModelConfig.org_id == org_id,
            ModelConfig.agent_code == agent_code,
        )
    )
    if row is None:
        row = ModelConfig(org_id=org_id, agent_code=agent_code, primary_model=primary_model)
        session.add(row)
    row.primary_model = primary_model
    row.fallback_model = fallback_model
    params = dict(row.params or {})
    params["routing_config"] = {
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout_seconds": timeout_seconds,
    }
    row.params = params
    session.add(
        Event(
            type="model.route.updated",
            payload={
                "org_id": org_id,
                "agent_code": agent_code,
                "primary_model": primary_model,
                "fallback_model": fallback_model,
                "routing_config": params["routing_config"],
                "updated_by": user_id,
            },
        )
    )
    await session.commit()
    await session.refresh(row)
    return _route_row(agent_code, row)


async def infrastructure_overview(session: AsyncSession, org_id: int) -> dict[str, Any]:
    providers = await provider_rows(session, org_id)
    routes = await route_rows(session, org_id)
    since = datetime.now(UTC) - timedelta(hours=24)
    calls_24h = await session.scalar(
        select(func.count()).select_from(LLMCall).where(
            LLMCall.org_id == org_id, LLMCall.created_at >= since
        )
    )
    failures_24h = await session.scalar(
        select(func.count()).select_from(LLMCall).where(
            LLMCall.org_id == org_id,
            LLMCall.created_at >= since,
            LLMCall.status == "error",
        )
    )
    return {
        "summary": {
            "providers_total": len(providers),
            "providers_ready": sum(1 for row in providers if row["runtime_ready"]),
            "routes_total": len(routes),
            "routes_with_fallback": sum(1 for row in routes if row["fallback_model"]),
            "calls_24h": calls_24h or 0,
            "failures_24h": failures_24h or 0,
        },
        "providers": providers,
        "routes": routes,
    }


def redact_error(error: str | None) -> str | None:
    if not error:
        return None
    value = error
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            value = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", value)
        else:
            value = pattern.sub("[REDACTED]", value)
    return value[:240]


async def recent_calls(
    session: AsyncSession,
    *,
    org_id: int,
    limit: int,
    call_status: str | None,
) -> dict[str, Any]:
    conditions = [LLMCall.org_id == org_id]
    if call_status:
        conditions.append(LLMCall.status == call_status)
    total = await session.scalar(
        select(func.count()).select_from(LLMCall).where(*conditions)
    )
    rows = (
        await session.scalars(
            select(LLMCall)
            .where(*conditions)
            .order_by(LLMCall.created_at.desc(), LLMCall.id.desc())
            .limit(limit)
        )
    ).all()
    return {
        "total": total or 0,
        "items": [
            {
                "id": row.id,
                "agent_code": row.agent_code,
                "agent_name": AGENT_NAMES.get(row.agent_code or "", "系统调用"),
                "provider": row.provider,
                "model": row.model,
                "total_tokens": row.total_tokens,
                "cost_usd": row.cost_usd,
                "latency_ms": row.latency_ms,
                "status": row.status,
                "error_summary": redact_error(row.error),
                "created_at": row.created_at,
            }
            for row in rows
        ],
    }
