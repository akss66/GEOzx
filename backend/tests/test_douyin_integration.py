from datetime import UTC, date, datetime

import httpx
import pytest

from app.integrations.douyin import (
    DouyinIntegrationError,
    fetch_douyin_user_info,
    fetch_douyin_video_list,
    normalize_douyin_user_profile,
    normalize_douyin_video_metrics,
    refresh_douyin_access_token,
)
from app.models.enums import MetricSource


def test_normalize_douyin_user_profile_keeps_safe_public_fields():
    payload = {
        "open_id": "_open_1",
        "union_id": "_union_1",
        "nickname": "同舟行测试号",
        "avatar": "https://example.com/avatar.png",
        "city": "杭州",
        "province": "浙江",
        "country": "中国",
        "gender": 1,
        "extra": "kept in raw profile only",
    }

    profile = normalize_douyin_user_profile(payload)

    assert profile == {
        "external_open_id": "_open_1",
        "union_id": "_union_1",
        "nickname": "同舟行测试号",
        "avatar": "https://example.com/avatar.png",
        "raw_profile": payload,
    }


def test_normalize_douyin_video_metrics_maps_statistics_to_review_snapshot_fields():
    created_at = int(datetime(2026, 7, 6, 12, 0, tzinfo=UTC).timestamp())
    items = [
        {
            "item_id": "video-1",
            "title": "从一句话，到一整套执行",
            "create_time": created_at,
            "statistics": {
                "play_count": 1000,
                "digg_count": 80,
                "comment_count": 25,
                "share_count": 10,
                "forward_count": 5,
            },
        }
    ]

    snapshots = normalize_douyin_video_metrics(items, account_id=7)

    assert snapshots == [
        {
            "account_id": 7,
            "source": MetricSource.DOUYIN,
            "stat_date": date(2026, 7, 6),
            "title": "从一句话，到一整套执行",
            "play": 1000,
            "exposure": 1000,
            "completion_rate": 0.0,
            "like_rate": 0.08,
            "comment_rate": 0.025,
            "share_rate": 0.01,
            "follower_delta": 0,
            "external_item_id": "video-1",
        }
    ]


@pytest.mark.asyncio
async def test_fetch_douyin_user_info_uses_official_post_form_contract():
    captured_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(
            200,
            json={
                "data": {
                    "error_code": 0,
                    "open_id": "open-id",
                    "nickname": "Test account",
                }
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_douyin_user_info(
            access_token="access-token",
            open_id="open-id",
            client=client,
        )

    assert captured_request is not None
    assert captured_request.method == "POST"
    assert str(captured_request.url) == "https://open.douyin.com/oauth/userinfo/"
    assert captured_request.content.decode() == "access_token=access-token&open_id=open-id"
    assert result["nickname"] == "Test account"


@pytest.mark.asyncio
async def test_refresh_douyin_access_token_uses_official_form_contract():
    captured_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(
            200,
            json={
                "data": {
                    "error_code": 0,
                    "access_token": "new-access-token",
                    "refresh_token": "new-refresh-token",
                    "expires_in": 7200,
                }
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await refresh_douyin_access_token(
            client_key="client-key",
            refresh_token="refresh-token",
            client=client,
        )

    assert captured_request is not None
    assert captured_request.method == "POST"
    assert str(captured_request.url) == "https://open.douyin.com/oauth/refresh_token/"
    assert captured_request.content.decode() == (
        "client_key=client-key&grant_type=refresh_token&refresh_token=refresh-token"
    )
    assert result["access_token"] == "new-access-token"


@pytest.mark.asyncio
async def test_douyin_transport_errors_are_exposed_as_integration_errors():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network unavailable", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(DouyinIntegrationError, match="request failed"):
            await fetch_douyin_user_info(
                access_token="access-token",
                open_id="open-id",
                client=client,
            )


@pytest.mark.asyncio
async def test_fetch_douyin_video_list_uses_official_endpoint_and_validates_response():
    captured_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(
            200,
            json={
                "data": {
                    "error_code": 0,
                    "cursor": 0,
                    "has_more": False,
                    "list": [{"item_id": "video-1", "statistics": {"play_count": 1}}],
                }
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        data = await fetch_douyin_video_list(
            access_token="access-token",
            open_id="open-id",
            cursor=0,
            count=20,
            client=client,
        )

    assert captured_request is not None
    assert str(captured_request.url).startswith("https://open.douyin.com/video/list")
    assert captured_request.url.params["access_token"] == "access-token"
    assert captured_request.url.params["open_id"] == "open-id"
    assert data["list"][0]["item_id"] == "video-1"
