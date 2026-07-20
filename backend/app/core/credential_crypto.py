"""Application-layer encryption for persisted platform credentials."""

import base64
import hashlib
import hmac
from typing import NamedTuple

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

_PREFIX = "fernet:v1:"
_PROVIDER_FINGERPRINT_DOMAIN = b"dyflow:model-provider-api-key:fingerprint:v1\x00"


class CredentialEncryptionError(RuntimeError):
    """Raised when credential encryption is unavailable or ciphertext is invalid."""


class ProviderKeyMaterial(NamedTuple):
    """Persistable provider-key fields without retaining plaintext."""

    encrypted_api_key: str
    key_last_four: str | None
    key_fingerprint: str


def _fernet() -> Fernet:
    key = settings.credential_encryption_key.strip()
    if not key:
        raise CredentialEncryptionError("CREDENTIAL_ENCRYPTION_KEY is not configured")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise CredentialEncryptionError(
            "CREDENTIAL_ENCRYPTION_KEY must be a valid Fernet key"
        ) from exc


def encrypt_credential(value: str) -> str:
    if not value:
        raise CredentialEncryptionError("credential value is empty")
    token = _fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return f"{_PREFIX}{token}"


def decrypt_credential(value: str) -> str:
    if not value.startswith(_PREFIX):
        raise CredentialEncryptionError("unsupported credential ciphertext format")
    try:
        plaintext = _fernet().decrypt(value.removeprefix(_PREFIX).encode("ascii"))
    except (InvalidToken, ValueError, UnicodeEncodeError) as exc:
        raise CredentialEncryptionError("credential ciphertext cannot be decrypted") from exc
    return plaintext.decode("utf-8")


def _provider_fingerprint_key() -> bytes:
    key = settings.credential_encryption_key.strip()
    _fernet()
    try:
        raw_key = base64.urlsafe_b64decode(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise CredentialEncryptionError(
            "CREDENTIAL_ENCRYPTION_KEY must be a valid Fernet key"
        ) from exc
    return hmac.new(raw_key, _PROVIDER_FINGERPRINT_DOMAIN, hashlib.sha256).digest()


def encrypt_provider_key(value: str) -> ProviderKeyMaterial:
    """Encrypt a provider API key and derive non-reversible display metadata."""
    if not value:
        raise CredentialEncryptionError("provider credential value is empty")
    fingerprint = hmac.new(
        _provider_fingerprint_key(),
        _PROVIDER_FINGERPRINT_DOMAIN + value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return ProviderKeyMaterial(
        encrypted_api_key=encrypt_credential(value),
        key_last_four=value[-4:] if len(value) >= 4 else None,
        key_fingerprint=fingerprint,
    )


def decrypt_provider_key(value: str) -> str:
    """Decrypt a provider API key through the shared Fernet boundary."""
    return decrypt_credential(value)
