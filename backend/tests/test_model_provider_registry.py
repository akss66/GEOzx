from dataclasses import FrozenInstanceError

import pytest

from app.models import ModelProvider
from app.services.model_provider_registry import (
    PROVIDER_TEMPLATES,
    normalize_provider_code,
    provider_public_row,
    remove_provider_key,
    replace_provider_key,
)


@pytest.fixture
def encryption_key(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(
        settings,
        "credential_encryption_key",
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )


def _provider() -> ModelProvider:
    return ModelProvider(
        id=7,
        org_id=11,
        code="openai",
        display_name="OpenAI",
        provider_type="preset",
        template_code="openai",
        protocol="openai_compatible",
        base_url="https://api.openai.com/v1",
        credential_source="none",
        verification_status="verified",
        verification_error_code="old_error",
    )


def test_builtin_templates_are_complete_trusted_and_immutable():
    assert tuple(PROVIDER_TEMPLATES) == (
        "deepseek",
        "openai",
        "qwen",
        "doubao",
        "zhipu",
        "moonshot",
    )
    for code, template in PROVIDER_TEMPLATES.items():
        assert template.code == code
        assert template.base_url.startswith("https://")
        assert template.protocol == "openai_compatible"
        assert template.models
        assert isinstance(template.models, tuple)

    with pytest.raises(TypeError):
        PROVIDER_TEMPLATES["other"] = PROVIDER_TEMPLATES["openai"]  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        PROVIDER_TEMPLATES["openai"].display_name = "Changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" My Provider ", "my-provider"),
        ("ACME_OpenAI Compatible", "acme-openai-compatible"),
    ],
)
def test_custom_provider_codes_are_normalized_to_lowercase_slugs(raw, expected):
    assert normalize_provider_code(raw) == expected


def test_replace_provider_key_resets_verification_and_public_row_is_safe(encryption_key):
    provider = _provider()
    plaintext = "sk-sensitive-provider-key-4321"

    replace_provider_key(provider, plaintext)
    public = provider_public_row(provider)

    assert provider.credential_source == "encrypted"
    assert provider.encrypted_api_key != plaintext
    assert provider.verification_status == "pending"
    assert provider.verified_at is None
    assert provider.verification_error_code is None
    assert public["key_configured"] is True
    assert public["key_last_four"] == "4321"
    assert "encrypted_api_key" not in public
    assert plaintext not in repr(public)
    assert provider.encrypted_api_key not in repr(public)


def test_remove_provider_key_clears_metadata_and_verification(encryption_key):
    provider = _provider()
    replace_provider_key(provider, "sk-sensitive-provider-key-4321")
    provider.verification_status = "verified"

    remove_provider_key(provider)

    assert provider.credential_source == "none"
    assert provider.encrypted_api_key is None
    assert provider.key_last_four is None
    assert provider.key_fingerprint is None
    assert provider.verification_status == "pending"
    assert provider.verified_at is None
    assert provider.verification_error_code is None
    assert provider_public_row(provider)["key_configured"] is False
