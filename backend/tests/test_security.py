"""安全工具单测（纯单元，无需 DB）。"""

import jwt
import pytest

from app.core.security import (
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_password_hash_roundtrip() -> None:
    h = hash_password("s3cret-pw")
    assert h != "s3cret-pw"
    assert verify_password("s3cret-pw", h) is True
    assert verify_password("wrong", h) is False


def test_token_roundtrip() -> None:
    token = create_access_token("42", "admin")
    payload = decode_token(token)
    assert payload["sub"] == "42"
    assert payload["role"] == "admin"


def test_decode_invalid_token_raises() -> None:
    with pytest.raises(jwt.PyJWTError):
        decode_token("not-a-real-token")
