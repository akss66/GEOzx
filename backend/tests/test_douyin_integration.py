from datetime import UTC, date, datetime

import httpx
import pytest

from app.integrations.douyin import (
    fetch_douyin_video_list,
    normalize_douyin_user_profile,
    normalize_douyin_video_metrics,
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
