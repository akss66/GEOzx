from unittest.mock import AsyncMock

import pytest

from app.models import PlatformAccountAuth
from app.services.account_avatar import AccountAvatarImage


async def _token(client, email: str, password: str) -> str:
    response = await client.post("/auth/login", json={"email": email, "password": password})
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_account_with_avatar(client, session, admin, token: str) -> dict:
    account = (
        await client.post(
            "/accounts",
            headers=_auth(token),
            json={"nickname": "Avatar account", "platform": "douyin"},
        )
    ).json()
    session.add(
        PlatformAccountAuth(
            org_id=admin.org_id,
            account_id=account["id"],
            platform="douyin",
            raw_profile={
                "avatar": (
                    "https://p3.douyinpic.com/aweme/100x100/avatar.jpeg"
                    "?from=3782654143"
                )
            },
        )
    )
    await session.commit()
    return account


@pytest.mark.asyncio
async def test_account_avatar_returns_authenticated_same_origin_image(
    client,
    session,
    admin,
    monkeypatch,
) -> None:
    token = await _token(client, "admin@test.com", "admin-pw-123")
    account = await _create_account_with_avatar(client, session, admin, token)
    fetch = AsyncMock(
        return_value=AccountAvatarImage(content=b"jpeg-bytes", content_type="image/jpeg")
    )
    monkeypatch.setattr("app.api.accounts.fetch_account_avatar", fetch)

    response = await client.get(
        f"/accounts/{account['id']}/avatar",
        headers=_auth(token),
    )

    assert response.status_code == 200
    assert response.content == b"jpeg-bytes"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["x-content-type-options"] == "nosniff"
    fetch.assert_awaited_once_with(
        "https://p3.douyinpic.com/aweme/100x100/avatar.jpeg?from=3782654143"
    )


@pytest.mark.asyncio
async def test_account_avatar_preserves_workspace_permissions(
    client,
    session,
    admin,
    member,
    monkeypatch,
) -> None:
    admin_token = await _token(client, "admin@test.com", "admin-pw-123")
    member_token = await _token(client, "user@test.com", "user-pw-123")
    account = await _create_account_with_avatar(client, session, admin, admin_token)
    fetch = AsyncMock(
        return_value=AccountAvatarImage(content=b"jpeg-bytes", content_type="image/jpeg")
    )
    monkeypatch.setattr("app.api.accounts.fetch_account_avatar", fetch)

    response = await client.get(
        f"/accounts/{account['id']}/avatar",
        headers=_auth(member_token),
    )

    assert response.status_code == 404
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_account_avatar_returns_not_found_without_synchronized_avatar(
    client,
    admin,
) -> None:
    token = await _token(client, "admin@test.com", "admin-pw-123")
    account = (
        await client.post(
            "/accounts",
            headers=_auth(token),
            json={"nickname": "No avatar", "platform": "douyin"},
        )
    ).json()

    response = await client.get(
        f"/accounts/{account['id']}/avatar",
        headers=_auth(token),
    )

    assert response.status_code == 404
