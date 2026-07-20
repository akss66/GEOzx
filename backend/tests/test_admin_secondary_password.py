import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import AdminSecurityCredential, Event, User


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _service():
    from app.services.admin_security import set_secondary_password, verify_secondary_password

    return set_secondary_password, verify_secondary_password


async def _login(
    client, email: str = "admin@test.com", password: str = "admin-pw-123"
) -> str:
    response = await client.post(
        "/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200
    return response.json()["access_token"]


async def _set_secondary_password(client, token: str, secondary_password: str = "delete-pass-123"):
    return await client.put(
        "/users/me/secondary-password",
        headers=_auth(token),
        json={
            "current_password": "admin-pw-123",
            "secondary_password": secondary_password,
        },
    )


async def test_secondary_password_requires_current_password(client, admin):
    admin_token = await _login(client)

    response = await client.put(
        "/users/me/secondary-password",
        headers=_auth(admin_token),
        json={"current_password": "wrong-password", "secondary_password": "delete-pass-123"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == {
        "code": "CURRENT_PASSWORD_INVALID",
        "message": "Invalid current password",
    }


async def test_secondary_password_validation_redacts_secret_inputs(client, admin):
    admin_token = await _login(client)
    current_password = "current-password-must-not-appear-" + ("x" * 130)
    secondary_password = "secondary-password-must-not-appear-" + ("y" * 130)

    response = await client.put(
        "/users/me/secondary-password",
        headers=_auth(admin_token),
        json={
            "current_password": current_password,
            "secondary_password": secondary_password,
        },
    )

    assert response.status_code == 422
    assert current_password not in response.text
    assert secondary_password not in response.text


async def test_secondary_password_accepts_at_most_72_utf8_bytes(client, admin):
    admin_token = await _login(client)
    accepted_password = "中" * 24
    rejected_password = "中" * 25

    accepted = await _set_secondary_password(client, admin_token, accepted_password)
    rejected = await _set_secondary_password(client, admin_token, rejected_password)

    assert accepted.status_code == 200
    assert rejected.status_code == 422
    assert rejected_password not in rejected.text


async def test_non_admin_cannot_access_secondary_password_endpoints(client, admin, member):
    member_token = await _login(client, "user@test.com", "user-pw-123")

    update_response = await _set_secondary_password(client, member_token)
    status_response = await client.get(
        "/users/me/secondary-password/status", headers=_auth(member_token)
    )

    assert update_response.status_code == 403
    assert status_response.status_code == 403


async def test_secondary_password_status_has_ten_minute_cooldown(client, admin):
    admin_token = await _login(client)

    response = await _set_secondary_password(client, admin_token)
    assert response.status_code == 200

    status_response = await client.get(
        "/users/me/secondary-password/status", headers=_auth(admin_token)
    )
    payload = status_response.json()
    assert status_response.status_code == 200
    assert payload["configured"] is True
    assert payload["deletion_available"] is False
    assert payload["delete_available_at"] is not None
    assert payload["locked_until"] is None


async def test_reset_secondary_password_restarts_cooldown(client, session, admin):
    admin_token = await _login(client)
    first_response = await _set_secondary_password(client, admin_token)
    credential = await session.scalar(
        select(AdminSecurityCredential).where(AdminSecurityCredential.user_id == admin.id)
    )
    assert first_response.status_code == 200
    assert credential is not None
    credential.delete_available_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    reset_response = await _set_secondary_password(
        client, admin_token, "replacement-delete-pass-123"
    )

    assert reset_response.status_code == 200
    assert reset_response.json()["deletion_available"] is False
    await session.refresh(credential)
    assert credential.delete_available_at.replace(tzinfo=UTC) > datetime.now(UTC)


async def test_verify_secondary_password_rejects_absent_configuration(session, admin):
    _, verify_secondary_password = _service()

    with pytest.raises(HTTPException) as exc:
        await verify_secondary_password(session, admin, "delete-pass-123")

    assert exc.value.status_code == 409
    assert exc.value.detail == "Secondary password is not configured"


async def test_verify_secondary_password_rejects_active_cooldown(session, admin):
    set_secondary_password, verify_secondary_password = _service()

    await set_secondary_password(session, admin, "admin-pw-123", "delete-pass-123")

    with pytest.raises(HTTPException) as exc:
        await verify_secondary_password(session, admin, "delete-pass-123")

    assert exc.value.status_code == 409
    assert exc.value.detail == "Secondary password cooldown is active"


async def test_fifth_wrong_secondary_password_starts_lockout(session, admin):
    set_secondary_password, verify_secondary_password = _service()

    await set_secondary_password(session, admin, "admin-pw-123", "delete-pass-123")
    credential = await session.scalar(
        select(AdminSecurityCredential).where(AdminSecurityCredential.user_id == admin.id)
    )
    assert credential is not None
    credential.delete_available_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    for _ in range(4):
        with pytest.raises(HTTPException) as exc:
            await verify_secondary_password(session, admin, "wrong-secondary-password")
        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid secondary password"

    with pytest.raises(HTTPException) as exc:
        await verify_secondary_password(session, admin, "wrong-secondary-password")

    assert exc.value.status_code == 429
    assert exc.value.detail == "Secondary password is temporarily locked"
    await session.refresh(credential)
    assert credential.failed_attempts == 5
    assert credential.locked_until is not None


async def test_same_session_correct_secondary_password_cannot_bypass_active_lock(session, admin):
    set_secondary_password, verify_secondary_password = _service()

    await set_secondary_password(session, admin, "admin-pw-123", "delete-pass-123")
    credential = await session.scalar(
        select(AdminSecurityCredential).where(AdminSecurityCredential.user_id == admin.id)
    )
    assert credential is not None
    credential.delete_available_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    for _ in range(5):
        with pytest.raises(HTTPException):
            await verify_secondary_password(session, admin, "wrong-secondary-password")

    with pytest.raises(HTTPException) as exc:
        await verify_secondary_password(session, admin, "delete-pass-123")

    assert exc.value.status_code == 429
    assert exc.value.detail == "Secondary password is temporarily locked"
    await session.refresh(credential)
    assert credential.failed_attempts == 5
    assert credential.locked_until is not None


async def test_concurrent_wrong_attempts_start_lockout_on_exactly_fifth_attempt(
    session, admin, monkeypatch
):
    set_secondary_password, verify_secondary_password = _service()
    import app.services.admin_security as admin_security

    await set_secondary_password(session, admin, "admin-pw-123", "delete-pass-123")
    credential = await session.scalar(
        select(AdminSecurityCredential).where(AdminSecurityCredential.user_id == admin.id)
    )
    assert credential is not None
    credential.delete_available_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    original_credential_for = admin_security._credential_for
    barrier = asyncio.Barrier(5)

    async def synchronized_credential_for(concurrent_session, actor):
        result = await original_credential_for(concurrent_session, actor)
        await barrier.wait()
        return result

    monkeypatch.setattr(admin_security, "_credential_for", synchronized_credential_for)
    session_factory = async_sessionmaker(session.bind, expire_on_commit=False)

    async def wrong_attempt() -> int:
        async with session_factory() as concurrent_session:
            actor = await concurrent_session.get(User, admin.id)
            assert actor is not None
            with pytest.raises(HTTPException) as exc:
                await verify_secondary_password(
                    concurrent_session, actor, "wrong-secondary-password"
                )
            return exc.value.status_code

    status_codes = await asyncio.gather(*(wrong_attempt() for _ in range(5)))

    await session.refresh(credential)
    assert sorted(status_codes) == [401, 401, 401, 401, 429]
    assert credential.failed_attempts == 5
    assert credential.locked_until is not None


async def test_concurrent_wrong_attempts_from_expired_lock_lock_on_fifth_attempt(
    session, admin, monkeypatch
):
    set_secondary_password, verify_secondary_password = _service()
    import app.services.admin_security as admin_security

    await set_secondary_password(session, admin, "admin-pw-123", "delete-pass-123")
    credential = await session.scalar(
        select(AdminSecurityCredential).where(AdminSecurityCredential.user_id == admin.id)
    )
    assert credential is not None
    credential.delete_available_at = datetime.now(UTC) - timedelta(seconds=1)
    credential.failed_attempts = 5
    credential.locked_until = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    original_credential_for = admin_security._credential_for
    barrier = asyncio.Barrier(5)

    async def synchronized_credential_for(concurrent_session, actor):
        result = await original_credential_for(concurrent_session, actor)
        await barrier.wait()
        return result

    monkeypatch.setattr(admin_security, "_credential_for", synchronized_credential_for)
    session_factory = async_sessionmaker(session.bind, expire_on_commit=False)

    async def wrong_attempt() -> int:
        async with session_factory() as concurrent_session:
            actor = await concurrent_session.get(User, admin.id)
            assert actor is not None
            with pytest.raises(HTTPException) as exc:
                await verify_secondary_password(
                    concurrent_session, actor, "wrong-secondary-password"
                )
            return exc.value.status_code

    status_codes = await asyncio.gather(*(wrong_attempt() for _ in range(5)))

    await session.refresh(credential)
    assert sorted(status_codes) == [401, 401, 401, 401, 429]
    assert credential.failed_attempts == 5
    assert credential.locked_until is not None


async def test_concurrent_initial_secondary_password_set_creates_one_credential(session, admin):
    set_secondary_password, _ = _service()
    barrier = asyncio.Barrier(2)
    session_factory = async_sessionmaker(session.bind, expire_on_commit=False)

    async def set_password() -> AdminSecurityCredential:
        async with session_factory() as concurrent_session:
            actor = await concurrent_session.get(User, admin.id)
            assert actor is not None
            await barrier.wait()
            return await set_secondary_password(
                concurrent_session, actor, "admin-pw-123", "delete-pass-123"
            )

    credentials = await asyncio.gather(set_password(), set_password())
    persisted = (
        await session.scalars(
            select(AdminSecurityCredential).where(AdminSecurityCredential.user_id == admin.id)
        )
    ).all()

    assert len(credentials) == 2
    assert len(persisted) == 1
    assert persisted[0].failed_attempts == 0
    assert persisted[0].locked_until is None


async def test_successful_secondary_password_verification_resets_failure_state(session, admin):
    set_secondary_password, verify_secondary_password = _service()

    await set_secondary_password(session, admin, "admin-pw-123", "delete-pass-123")
    credential = await session.scalar(
        select(AdminSecurityCredential).where(AdminSecurityCredential.user_id == admin.id)
    )
    assert credential is not None
    credential.delete_available_at = datetime.now(UTC) - timedelta(seconds=1)
    credential.failed_attempts = 4
    credential.locked_until = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    await verify_secondary_password(session, admin, "delete-pass-123")

    await session.refresh(credential)
    assert credential.failed_attempts == 0
    assert credential.locked_until is None


async def test_setting_secondary_password_resets_failure_state_without_logging_passwords(
    session, admin
):
    set_secondary_password, _ = _service()

    await set_secondary_password(session, admin, "admin-pw-123", "delete-pass-123")
    credential = await session.scalar(
        select(AdminSecurityCredential).where(AdminSecurityCredential.user_id == admin.id)
    )
    assert credential is not None
    credential.failed_attempts = 4
    credential.locked_until = datetime.now(UTC) + timedelta(minutes=5)
    await session.commit()

    await set_secondary_password(session, admin, "admin-pw-123", "new-delete-pass-123")

    await session.refresh(credential)
    events = (await session.scalars(select(Event))).all()
    assert credential.failed_attempts == 0
    assert credential.locked_until is None
    assert all(
        "admin-pw-123" not in str(event.payload)
        and "new-delete-pass-123" not in str(event.payload)
        for event in events
    )
