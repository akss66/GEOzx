import hashlib
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.integrations.douyin import (
    DouyinIntegrationError,
    build_douyin_h5_publish_schema,
    create_douyin_h5_signature,
    create_douyin_share_id,
    fetch_douyin_open_ticket,
)


def test_h5_signature_matches_official_ascii_key_order_example() -> None:
    nonce_str = "Wm3WZYTPz0wzccnW"
    ticket = (
        "@ml6sqYBGgTKmQNajnKNkaj8yksCAY++adIhlGIqfTiKyvBqOIkzdJ6WRgP+"
        "nO+wtVItqKbX4iZ+mFIYkyPJjpQ=="
    )
    timestamp = "1650941858"
    plain = f"nonce_str={nonce_str}&ticket={ticket}&timestamp={timestamp}"

    assert create_douyin_h5_signature(
        nonce_str=nonce_str,
        ticket=ticket,
        timestamp=timestamp,
    ) == hashlib.md5(plain.encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_fetch_open_ticket_uses_client_token_header() -> None:
    captured: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        return httpx.Response(
            200,
            json={
                "data": {
                    "error_code": 0,
                    "ticket": "open-ticket",
                    "expires_in": 7200,
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_douyin_open_ticket(
            client_token="client-token",
            client=client,
        )

    assert captured is not None
    assert captured.method == "GET"
    assert str(captured.url) == "https://open.douyin.com/open/getticket/"
    assert captured.headers["access-token"] == "client-token"
    assert result["ticket"] == "open-ticket"


@pytest.mark.asyncio
async def test_create_share_id_requests_callback_and_keeps_platform_log_id() -> None:
    captured: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        return httpx.Response(
            200,
            json={
                "data": {
                    "share_id": "share-123",
                    "error_code": 0,
                    "description": "",
                },
                "extra": {
                    "error_code": 0,
                    "logid": "log-123",
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await create_douyin_share_id(
            client_token="client-token",
            default_hashtag="品牌案例",
            client=client,
        )

    assert captured is not None
    assert captured.method == "POST"
    assert captured.headers["access-token"] == "client-token"
    assert captured.url.params["need_callback"] == "true"
    assert captured.url.params["default_hashtag"] == "品牌案例"
    assert result == {"share_id": "share-123", "log_id": "log-123"}


def test_build_video_h5_schema_contains_signed_publish_contract() -> None:
    schema = build_douyin_h5_publish_schema(
        client_key="client-key",
        ticket="open-ticket",
        share_id="share-123",
        video_path="https://cdn.test/video.mp4",
        title="一条正式内容",
        topics=["品牌案例", "玻璃贴膜"],
        visibility="public",
        allow_download=False,
        nonce_str="nonce-1",
        timestamp="1650941858",
    )
    parsed = urlparse(schema)
    query = parse_qs(parsed.query)

    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "snssdk1128://openplatform/share"
    )
    assert query["share_type"] == ["h5"]
    assert query["client_key"] == ["client-key"]
    assert query["state"] == ["share-123"]
    assert query["video_path"] == ["https://cdn.test/video.mp4"]
    assert query["share_to_publish"] == ["1"]
    assert query["share_to_type"] == ["0"]
    assert query["private_status"] == ["0"]
    assert query["download_type"] == ["2"]
    assert query["hashtag_list"] == ['["品牌案例","玻璃贴膜"]']
    assert query["signature"] == [
        create_douyin_h5_signature(
            nonce_str="nonce-1",
            ticket="open-ticket",
            timestamp="1650941858",
        )
    ]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({}, "exactly one media path"),
        (
            {
                "video_path": "https://cdn.test/video.mp4",
                "image_path": "https://cdn.test/image.jpg",
            },
            "exactly one media path",
        ),
        (
            {"image_path": "https://cdn.test/image.jpg", "direct_to_publish": True},
            "direct publish only supports video",
        ),
        (
            {"video_path": "https://cdn.test/video.mp4", "visibility": "organization"},
            "unsupported visibility",
        ),
    ],
)
def test_h5_schema_rejects_unsupported_media_contract(kwargs, message) -> None:
    with pytest.raises(DouyinIntegrationError, match=message):
        build_douyin_h5_publish_schema(
            client_key="client-key",
            ticket="open-ticket",
            share_id="share-123",
            nonce_str="nonce-1",
            timestamp="1650941858",
            **kwargs,
        )


@pytest.mark.asyncio
async def test_share_id_error_exposes_safe_code_log_and_retryability() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "error_code": 28001006,
                    "description": "network call failed",
                },
                "extra": {
                    "logid": "log-failed",
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DouyinIntegrationError) as captured:
            await create_douyin_share_id(
                client_token="client-token",
                client=client,
            )

    assert captured.value.error_code == "28001006"
    assert captured.value.log_id == "log-failed"
    assert captured.value.retryable is True
