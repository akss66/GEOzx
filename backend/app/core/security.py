"""安全工具：密码哈希（bcrypt）与 JWT 令牌签发/校验。"""

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.config import settings


def hash_password(password: str) -> str:
    """bcrypt 哈希（含随机盐）。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(subject: str, role: str) -> str:
    """签发 JWT。subject=用户 id（字符串），role=角色值。"""
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    """校验并解码 JWT；失败抛 jwt.PyJWTError 子类。"""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
