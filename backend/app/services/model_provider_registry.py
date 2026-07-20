"""Trusted model-provider templates and write-only credential lifecycle helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from app.core.credential_crypto import encrypt_provider_key
from app.models import ModelProvider

_MAX_PROVIDER_CODE_LENGTH = 64
_FINGERPRINT_PREFIX_LENGTH = 12


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
        "key_configured": provider.credential_source == "environment"
        or bool(provider.encrypted_api_key),
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
