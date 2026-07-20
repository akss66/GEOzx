from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models import AdminSecurityCredential, Event


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _service():
    from app.services.admin_security import set_secondary_password, verify_secondary_password

    return set_secondary_password, verify_secondary_password


async def _login(client) -> str:
    response = await client.post(
        "/auth/login", json={"email": "admin@test.com", "password": "admin-pw-123"}
    )
    assert response.status_code == 200
    return response.json()["access_token"]


async def _set_secondary_password(client, token: str):
    return await client.put(
        "/users/me/secondary-password",
        headers=_auth(token),
        json={
            "current_password": "admin-pw-123",
            "secondary_password": "delete-pass-123",
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
    assert response.json()["detail"] == "Invalid current password"


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
