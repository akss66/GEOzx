import pytest

from app.config import settings
from app.core.credential_crypto import (
    CredentialEncryptionError,
    decrypt_credential,
    encrypt_credential,
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
