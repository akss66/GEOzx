"""Secondary-password controls for destructive administrator actions."""

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models import AdminSecurityCredential, User

COOLDOWN_MINUTES = 10
LOCKOUT_MINUTES = 15
MAX_FAILED_ATTEMPTS = 5


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def _credential_for(
    session: AsyncSession, actor: User
) -> AdminSecurityCredential | None:
    return await session.scalar(
        select(AdminSecurityCredential).where(AdminSecurityCredential.user_id == actor.id)
    )


async def set_secondary_password(
    session: AsyncSession,
    actor: User,
    current_password: str,
    secondary_password: str,
) -> AdminSecurityCredential:
    if not verify_password(current_password, actor.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid current password"
        )

    now = datetime.now(UTC)
    credential = await _credential_for(session, actor)
    if credential is None:
        credential = AdminSecurityCredential(
            user_id=actor.id,
            password_hash=hash_password(secondary_password),
            changed_at=now,
            delete_available_at=now + timedelta(minutes=COOLDOWN_MINUTES),
        )
        session.add(credential)
    else:
        credential.password_hash = hash_password(secondary_password)
        credential.changed_at = now
        credential.delete_available_at = now + timedelta(minutes=COOLDOWN_MINUTES)

    credential.failed_attempts = 0
    credential.locked_until = None
    await session.commit()
    await session.refresh(credential)
    return credential


async def verify_secondary_password(
    session: AsyncSession, actor: User, secondary_password: str
) -> None:
    credential = await _credential_for(session, actor)
    if credential is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Secondary password is not configured"
        )

    now = datetime.now(UTC)
    if credential.locked_until is not None:
        if _utc(credential.locked_until) > now:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Secondary password is temporarily locked",
            )
        credential.failed_attempts = 0
        credential.locked_until = None

    if _utc(credential.delete_available_at) > now:
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Secondary password cooldown is active"
        )

    if verify_password(secondary_password, credential.password_hash):
        credential.failed_attempts = 0
        credential.locked_until = None
        await session.commit()
        return

    credential.failed_attempts += 1
    if credential.failed_attempts == MAX_FAILED_ATTEMPTS:
        credential.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Secondary password is temporarily locked",
        )

    await session.commit()
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid secondary password"
    )


async def get_secondary_password_status(
    session: AsyncSession, actor: User
) -> tuple[AdminSecurityCredential | None, bool]:
    credential = await _credential_for(session, actor)
    deletion_available = credential is not None and (
        _utc(credential.delete_available_at) <= datetime.now(UTC)
    )
    return credential, deletion_available
