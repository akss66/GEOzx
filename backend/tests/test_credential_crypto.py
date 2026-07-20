import pytest

from app.config import settings
from app.core.credential_crypto import (
    CredentialEncryptionError,
    decrypt_credential,
    decrypt_provider_key,
    encrypt_credential,
    encrypt_provider_key,
)


def test_credential_round_trip_uses_ciphertext(monkeypatch):
    monkeypatch.setattr(
        settings,
        "credential_encryption_key",
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )

    encrypted = encrypt_credential("douyin-access-token")

    assert encrypted != "douyin-access-token"
    assert "douyin-access-token" not in encrypted
    assert decrypt_credential(encrypted) == "douyin-access-token"


def test_credential_encryption_fails_closed_without_key(monkeypatch):
    monkeypatch.setattr(settings, "credential_encryption_key", "")

    with pytest.raises(CredentialEncryptionError, match="CREDENTIAL_ENCRYPTION_KEY"):
        encrypt_credential("douyin-access-token")


def test_provider_key_encryption_stores_only_safe_metadata(monkeypatch):
    monkeypatch.setattr(
        settings,
        "credential_encryption_key",
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )
    plaintext = "sk-provider-super-secret-9876"

    material = encrypt_provider_key(plaintext)

    assert material.encrypted_api_key.startswith("fernet:v1:")
    assert plaintext not in material.encrypted_api_key
    assert material.key_last_four == "9876"
    assert len(material.key_fingerprint) == 64
    assert plaintext not in material.key_fingerprint
    assert decrypt_provider_key(material.encrypted_api_key) == plaintext


def test_provider_key_fingerprint_is_stable_keyed_and_domain_separated(monkeypatch):
    monkeypatch.setattr(
        settings,
        "credential_encryption_key",
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )

    first = encrypt_provider_key("same-provider-key")
    second = encrypt_provider_key("same-provider-key")
    ordinary_credential = encrypt_credential("same-provider-key")

    assert first.encrypted_api_key != second.encrypted_api_key
    assert first.key_fingerprint == second.key_fingerprint
    assert first.key_fingerprint not in ordinary_credential


def test_provider_key_does_not_reveal_short_secret(monkeypatch):
    monkeypatch.setattr(
        settings,
        "credential_encryption_key",
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )

    material = encrypt_provider_key("abc")

    assert material.key_last_four is None


def test_provider_key_errors_never_echo_plaintext(monkeypatch):
    monkeypatch.setattr(settings, "credential_encryption_key", "")
    plaintext = "sk-never-echo-this-value"

    with pytest.raises(CredentialEncryptionError) as exc_info:
        encrypt_provider_key(plaintext)

    assert plaintext not in str(exc_info.value)
