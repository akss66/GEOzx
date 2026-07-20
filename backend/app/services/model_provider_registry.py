"""Trusted model-provider templates and organization-scoped provider governance."""

from __future__ import annotations

import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.credential_crypto import (
    CredentialEncryptionError,
    decrypt_provider_key,
    encrypt_provider_key,
)
from app.core.outbound_url import (
    OutboundRequestError,
    OutboundRequestTimeoutError,
    UnsafeOutboundURLError,
    bounded_outbound_request,
    validate_public_https_url,
)
from app.models import Event, ModelConfig, ModelProvider
from app.services.model_infrastructure import AGENT_NAMES

_MAX_PROVIDER_CODE_LENGTH = 64
_FINGERPRINT_PREFIX_LENGTH = 12
_UPSTREAM_ERROR_CODES = frozenset(
    {
        "authentication_failed",
        "endpoint_unreachable",
        "protocol_incompatible",
        "timeout",
        "model_unavailable",
        "discovery_unsupported",
    }
)


@dataclass(frozen=True)
class ProviderTemplate:
    code: str
    display_name: str
    base_url: str
    models: tuple[str, ...]
    protocol: str = "openai_compatible"


PROVIDER_TEMPLATES: Mapping[str, ProviderTemplate] = MappingProxyType(
    {
        "deepseek": ProviderTemplate(
            code="deepseek",
            display_name="DeepSeek",
            base_url="https://api.deepseek.com",
            models=("deepseek-chat", "deepseek-reasoner"),
        ),
        "openai": ProviderTemplate(
            code="openai",
            display_name="OpenAI",
            base_url="https://api.openai.com/v1",
            models=("gpt-4.1", "gpt-4.1-mini"),
        ),
        "qwen": ProviderTemplate(
            code="qwen",
            display_name="Qwen",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            models=("qwen-max", "qwen-plus", "qwen-turbo"),
        ),
        "doubao": ProviderTemplate(
            code="doubao",
            display_name="Doubao",
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            models=("doubao-seed-1-6", "doubao-1-5-pro-32k"),
        ),
        "zhipu": ProviderTemplate(
            code="zhipu",
            display_name="Zhipu AI",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            models=("glm-4-plus", "glm-4-air"),
        ),
        "moonshot": ProviderTemplate(
            code="moonshot",
            display_name="Moonshot AI",
            base_url="https://api.moonshot.cn/v1",
            models=("moonshot-v1-8k", "moonshot-v1-32k"),
        ),
    }
)


class ProviderDeleteConflictError(RuntimeError):
    """Raised when a provider is still referenced by agent routes."""

    def __init__(self, affected_agents: list[str]):
        super().__init__("model provider is still referenced")
        self.affected_agents = affected_agents


class ProviderUpstreamError(RuntimeError):
    """Raised when verification or discovery cannot complete cleanly."""

    def __init__(self, code: str):
        if code not in _UPSTREAM_ERROR_CODES:
            raise ValueError(f"unsupported provider upstream error code: {code}")
        super().__init__(code)
        self.code = code


def normalize_provider_code(value: str) -> str:
    """Normalize a custom provider name into an organization-scoped ASCII slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not slug or len(slug) > _MAX_PROVIDER_CODE_LENGTH:
        raise ValueError("provider code must form a non-empty slug of at most 64 characters")
    return slug


def _reset_verification(provider: ModelProvider) -> None:
    provider.verification_status = "pending"
    provider.verified_at = None
    provider.verification_error_code = None


def _is_trusted_deepseek_template(provider: ModelProvider) -> bool:
    template = PROVIDER_TEMPLATES["deepseek"]
    return (
        provider.code == template.code
        and provider.template_code == template.code
        and provider.base_url == template.base_url
    )


def replace_provider_key(provider: ModelProvider, api_key: str) -> None:
    """Replace a provider key in memory; the caller controls transaction commit."""
    material = encrypt_provider_key(api_key)
    provider.credential_source = "encrypted"
    provider.encrypted_api_key = material.encrypted_api_key
    provider.key_last_four = material.key_last_four
    provider.key_fingerprint = material.key_fingerprint
    _reset_verification(provider)


def remove_provider_key(provider: ModelProvider, *, credential_source: str = "none") -> None:
    """Remove persisted key material and invalidate prior verification."""
    provider.credential_source = credential_source
    provider.encrypted_api_key = None
    provider.key_last_four = None
    provider.key_fingerprint = None
    _reset_verification(provider)


def provider_public_row(provider: ModelProvider) -> dict[str, Any]:
    """Serialize a provider without exposing encrypted or reconstructable key data."""
    fingerprint = provider.key_fingerprint
    return {
        "id": provider.id,
        "code": provider.code,
        "display_name": provider.display_name,
        "provider_type": provider.provider_type,
        "template_code": provider.template_code,
        "protocol": provider.protocol,
        "base_url": provider.base_url,
        "enabled": provider.enabled,
        "sort_order": provider.sort_order,
        "credential_source": provider.credential_source,
        "key_configured": bool(provider.encrypted_api_key)
        or bool(_environment_provider_key(provider)),
        "key_last_four": provider.key_last_four,
        "key_fingerprint": (
            fingerprint[:_FINGERPRINT_PREFIX_LENGTH] if fingerprint is not None else None
        ),
        "verification_status": provider.verification_status,
        "verified_at": provider.verified_at,
        "verification_error_code": provider.verification_error_code,
        "models": list(provider.models) if provider.models is not None else None,
        "models_updated_at": provider.models_updated_at,
        "created_at": provider.created_at,
        "updated_at": provider.updated_at,
    }


def list_provider_templates() -> list[dict[str, Any]]:
    return [
        {
            "code": template.code,
            "display_name": template.display_name,
            "base_url": template.base_url,
            "protocol": template.protocol,
            "models": list(template.models),
        }
        for template in PROVIDER_TEMPLATES.values()
    ]


async def list_model_providers(session: AsyncSession, *, org_id: int) -> list[dict[str, Any]]:
    providers = (
        await session.scalars(
            select(ModelProvider)
            .where(ModelProvider.org_id == org_id)
            .order_by(ModelProvider.sort_order.asc(), ModelProvider.id.asc())
        )
    ).all()
    references = await _provider_route_references(session, org_id=org_id)
    return [_provider_detail(provider, references.get(provider.id, [])) for provider in providers]


async def get_model_provider_detail(
    session: AsyncSession,
    *,
    org_id: int,
    provider_id: int,
) -> dict[str, Any]:
    provider = await _get_provider(session, org_id=org_id, provider_id=provider_id)
    references = await _provider_route_references(
        session,
        org_id=org_id,
        provider_ids={provider.id},
    )
    return _provider_detail(provider, references.get(provider.id, []))


async def create_model_provider(
    session: AsyncSession,
    *,
    org_id: int,
    user_id: int,
    template_code: str | None,
    provider_type: str | None,
    code: str | None,
    display_name: str | None,
    base_url: str | None,
    enabled: bool,
) -> dict[str, Any]:
    template = None
    credential_source = "none"
    normalized_base_url = None
    provider_models = None
    normalized_code = code.strip() if code else None
    normalized_display_name = display_name.strip() if display_name else None

    if template_code:
        template = PROVIDER_TEMPLATES.get(template_code)
        if template is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="unknown provider template",
            )
        normalized_code = template.code
        normalized_display_name = template.display_name
        normalized_base_url = template.base_url
        provider_type = "preset"
        provider_models = list(template.models)
        if template.code == "deepseek":
            credential_source = "environment"
    else:
        if provider_type != "custom_openai":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="custom providers must declare provider_type=custom_openai",
            )
        if normalized_display_name is None or base_url is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="custom providers require display_name and base_url",
            )
        try:
            normalized_code = normalize_provider_code(normalized_code or normalized_display_name)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
        try:
            normalized_base_url = await validate_public_https_url(base_url.strip())
        except UnsafeOutboundURLError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="provider endpoint must be a public HTTPS URL",
            ) from exc

    provider = ModelProvider(
        org_id=org_id,
        code=normalized_code or "",
        display_name=normalized_display_name or "",
        provider_type=provider_type or "custom_openai",
        template_code=template.code if template is not None else None,
        protocol="openai_compatible",
        base_url=normalized_base_url,
        enabled=enabled,
        sort_order=await _next_sort_order(session, org_id=org_id),
        credential_source=credential_source,
        verification_status="pending",
        models=provider_models,
        created_by_id=user_id,
        updated_by_id=user_id,
    )
    session.add(provider)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="provider code already exists in this organization",
        ) from exc
    _audit(
        session,
        "model_provider.created",
        {
            "org_id": org_id,
            "provider_id": provider.id,
            "code": provider.code,
            "provider_type": provider.provider_type,
            "template_code": provider.template_code,
            "created_by": user_id,
        },
    )
    return await _commit_and_detail(session, provider, org_id=org_id)


async def patch_model_provider(
    session: AsyncSession,
    *,
    org_id: int,
    user_id: int,
    provider_id: int,
    display_name: str | None,
    base_url: str | None,
    enabled: bool | None,
) -> dict[str, Any]:
    provider = await _get_provider(session, org_id=org_id, provider_id=provider_id)
    if display_name is not None:
        provider.display_name = display_name.strip()
    if base_url is not None:
        try:
            validated_base_url = await validate_public_https_url(base_url.strip())
        except UnsafeOutboundURLError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="provider endpoint must be a public HTTPS URL",
            ) from exc
        if validated_base_url != provider.base_url:
            if _is_trusted_deepseek_template(provider):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="the server-managed DeepSeek endpoint cannot be changed",
                )
            provider.base_url = validated_base_url
            _reset_verification(provider)
    if enabled is not None:
        provider.enabled = enabled
    provider.updated_by_id = user_id
    _audit(
        session,
        "model_provider.updated",
        {
            "org_id": org_id,
            "provider_id": provider.id,
            "enabled": provider.enabled,
            "updated_by": user_id,
        },
    )
    return await _commit_and_detail(session, provider, org_id=org_id)


async def put_model_provider_credential(
    session: AsyncSession,
    *,
    org_id: int,
    user_id: int,
    provider_id: int,
    api_key: str,
) -> dict[str, Any]:
    provider = await _get_provider(session, org_id=org_id, provider_id=provider_id)
    try:
        replace_provider_key(provider, api_key.strip())
    except CredentialEncryptionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    provider.updated_by_id = user_id
    _audit(
        session,
        "model_provider.credential_set",
        {
            "org_id": org_id,
            "provider_id": provider.id,
            "credential_source": provider.credential_source,
            "key_last_four": provider.key_last_four,
            "updated_by": user_id,
        },
    )
    return await _commit_and_detail(session, provider, org_id=org_id)


async def delete_model_provider_credential(
    session: AsyncSession,
    *,
    org_id: int,
    user_id: int,
    provider_id: int,
) -> dict[str, Any]:
    provider = await _get_provider(session, org_id=org_id, provider_id=provider_id)
    credential_source = "environment" if _is_trusted_deepseek_template(provider) else "none"
    remove_provider_key(provider, credential_source=credential_source)
    provider.updated_by_id = user_id
    _audit(
        session,
        "model_provider.credential_deleted",
        {
            "org_id": org_id,
            "provider_id": provider.id,
            "credential_source": provider.credential_source,
            "updated_by": user_id,
        },
    )
    return await _commit_and_detail(session, provider, org_id=org_id)


async def verify_model_provider(
    session: AsyncSession,
    *,
    org_id: int,
    user_id: int,
    provider_id: int,
) -> dict[str, Any]:
    provider = await _get_provider(session, org_id=org_id, provider_id=provider_id)
    started = time.perf_counter()
    try:
        remote_models = await _fetch_remote_models(provider)
        if provider.models and not (set(provider.models) & set(remote_models)):
            raise ProviderUpstreamError("model_unavailable")
        provider.verification_status = "verified"
        provider.verification_error_code = None
        provider.verified_at = datetime.now(UTC)
    except ProviderUpstreamError as exc:
        provider.verification_status = "error"
        provider.verification_error_code = exc.code
        provider.verified_at = None
    provider.updated_by_id = user_id
    latency_ms = max(0, int((time.perf_counter() - started) * 1000))
    _audit(
        session,
        "model_provider.verify",
        {
            "org_id": org_id,
            "provider_id": provider.id,
            "verification_status": provider.verification_status,
            "verification_error_code": provider.verification_error_code,
            "latency_ms": latency_ms,
            "updated_by": user_id,
        },
    )
    await session.commit()
    await session.refresh(provider)
    return {
        "provider_id": provider.id,
        "verification_status": provider.verification_status,
        "verification_error_code": provider.verification_error_code,
        "verified_at": provider.verified_at,
        "latency_ms": latency_ms,
    }


async def discover_model_provider_models(
    session: AsyncSession,
    *,
    org_id: int,
    user_id: int,
    provider_id: int,
) -> dict[str, Any]:
    provider = await _get_provider(session, org_id=org_id, provider_id=provider_id)
    try:
        remote_models = await _fetch_remote_models(provider)
        provider.models = remote_models
        provider.models_updated_at = datetime.now(UTC)
        provider.updated_by_id = user_id
        _audit(
            session,
            "model_provider.discover_models",
            {
                "org_id": org_id,
                "provider_id": provider.id,
                "model_count": len(remote_models),
                "error_code": None,
                "updated_by": user_id,
            },
        )
        await session.commit()
        await session.refresh(provider)
        return {
            "provider_id": provider.id,
            "models": remote_models,
            "models_updated_at": provider.models_updated_at,
            "error_code": None,
        }
    except ProviderUpstreamError as exc:
        error_code = "discovery_unsupported" if exc.code == "protocol_incompatible" else exc.code
        _audit(
            session,
            "model_provider.discover_models",
            {
                "org_id": org_id,
                "provider_id": provider.id,
                "model_count": len(provider.models or []),
                "error_code": error_code,
                "updated_by": user_id,
            },
        )
        await session.commit()
        return {
            "provider_id": provider.id,
            "models": list(provider.models or []),
            "models_updated_at": provider.models_updated_at,
            "error_code": error_code,
        }


async def put_model_provider_models(
    session: AsyncSession,
    *,
    org_id: int,
    user_id: int,
    provider_id: int,
    models: list[str],
) -> dict[str, Any]:
    provider = await _get_provider(session, org_id=org_id, provider_id=provider_id)
    normalized_models = list(models)
    if normalized_models != list(provider.models or []):
        provider.models = normalized_models
        _reset_verification(provider)
    provider.models_updated_at = datetime.now(UTC)
    provider.updated_by_id = user_id
    _audit(
        session,
        "model_provider.models_updated",
        {
            "org_id": org_id,
            "provider_id": provider.id,
            "model_count": len(models),
            "updated_by": user_id,
        },
    )
    return await _commit_and_detail(session, provider, org_id=org_id)


async def delete_model_provider(
    session: AsyncSession,
    *,
    org_id: int,
    user_id: int,
    provider_id: int,
) -> None:
    provider = await _get_provider(session, org_id=org_id, provider_id=provider_id)
    persisted_provider_id = provider.id
    references = await _provider_route_references(
        session,
        org_id=org_id,
        provider_ids={persisted_provider_id},
    )
    if references.get(persisted_provider_id):
        raise ProviderDeleteConflictError(
            [entry["agent_name"] for entry in references[persisted_provider_id]]
        )
    _audit(
        session,
        "model_provider.deleted",
        {
            "org_id": org_id,
            "provider_id": provider.id,
            "code": provider.code,
            "deleted_by": user_id,
        },
    )
    await session.delete(provider)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        references = await _provider_route_references(
            session,
            org_id=org_id,
            provider_ids={persisted_provider_id},
        )
        if references.get(persisted_provider_id):
            raise ProviderDeleteConflictError(
                [entry["agent_name"] for entry in references[persisted_provider_id]]
            ) from exc
        raise


async def _commit_and_detail(
    session: AsyncSession,
    provider: ModelProvider,
    *,
    org_id: int,
) -> dict[str, Any]:
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="provider code already exists in this organization",
        ) from exc
    await session.refresh(provider)
    return await get_model_provider_detail(session, org_id=org_id, provider_id=provider.id)


async def _get_provider(
    session: AsyncSession,
    *,
    org_id: int,
    provider_id: int,
) -> ModelProvider:
    provider = await session.scalar(
        select(ModelProvider).where(
            ModelProvider.org_id == org_id,
            ModelProvider.id == provider_id,
        )
    )
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="model provider not found",
        )
    return provider


def _provider_detail(
    provider: ModelProvider,
    referenced_agents: list[dict[str, str]],
) -> dict[str, Any]:
    row = provider_public_row(provider)
    row["referenced_agents"] = referenced_agents
    return row


def _audit(session: AsyncSession, event_type: str, payload: dict[str, Any]) -> None:
    session.add(Event(type=event_type, payload=payload))


async def _provider_route_references(
    session: AsyncSession,
    *,
    org_id: int,
    provider_ids: set[int] | None = None,
) -> dict[int, list[dict[str, str]]]:
    query = select(ModelConfig).where(ModelConfig.org_id == org_id)
    if provider_ids:
        query = query.where(
            or_(
                ModelConfig.primary_provider_id.in_(provider_ids),
                ModelConfig.fallback_provider_id.in_(provider_ids),
            )
        )
    rows = (await session.scalars(query)).all()
    references: dict[int, list[dict[str, str]]] = {}
    for row in rows:
        entry = {
            "agent_code": row.agent_code,
            "agent_name": AGENT_NAMES.get(row.agent_code, row.agent_code),
        }
        for provider_id in (row.primary_provider_id, row.fallback_provider_id):
            if provider_id is None:
                continue
            references.setdefault(provider_id, [])
            if entry not in references[provider_id]:
                references[provider_id].append(entry)
    return references


async def _next_sort_order(session: AsyncSession, *, org_id: int) -> int:
    max_sort = await session.scalar(
        select(func.max(ModelProvider.sort_order)).where(ModelProvider.org_id == org_id)
    )
    return int(max_sort or 0) + 1


def _environment_provider_key(provider: ModelProvider) -> str | None:
    if provider.credential_source != "environment" or not _is_trusted_deepseek_template(provider):
        return None
    value = os.environ.get("DEEPSEEK_API_KEY") or settings.deepseek_api_key
    return value or None


def _provider_api_key(provider: ModelProvider) -> str | None:
    if provider.credential_source == "encrypted" and provider.encrypted_api_key:
        try:
            return decrypt_provider_key(provider.encrypted_api_key)
        except CredentialEncryptionError as exc:
            raise ProviderUpstreamError("authentication_failed") from exc
    return _environment_provider_key(provider)


async def _fetch_remote_models(provider: ModelProvider) -> list[str]:
    if not provider.base_url:
        raise ProviderUpstreamError("protocol_incompatible")
    api_key = _provider_api_key(provider)
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    url = f"{provider.base_url.rstrip('/')}/models"
    try:
        response = await bounded_outbound_request("GET", url, headers=headers)
    except OutboundRequestTimeoutError as exc:
        raise ProviderUpstreamError("timeout") from exc
    except (OutboundRequestError, UnsafeOutboundURLError) as exc:
        raise ProviderUpstreamError("endpoint_unreachable") from exc

    if response.status_code in {401, 403}:
        raise ProviderUpstreamError("authentication_failed")
    if response.status_code in {404, 405}:
        raise ProviderUpstreamError("protocol_incompatible")
    if response.status_code >= 500:
        raise ProviderUpstreamError("endpoint_unreachable")
    if response.status_code >= 400:
        raise ProviderUpstreamError("protocol_incompatible")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderUpstreamError("protocol_incompatible") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ProviderUpstreamError("protocol_incompatible")
    models: list[str] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if isinstance(model_id, str):
            candidate = model_id.strip()
            if candidate and len(candidate) <= 128 and candidate not in seen:
                seen.add(candidate)
                models.append(candidate)
    if not models:
        raise ProviderUpstreamError("protocol_incompatible")
    return models
