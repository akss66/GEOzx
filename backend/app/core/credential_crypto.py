"""Application-layer encryption for persisted platform credentials."""

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

_PREFIX = "fernet:v1:"


class CredentialEncryptionError(RuntimeError):
    """Raised when credential encryption is unavailable or ciphertext is invalid."""


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
