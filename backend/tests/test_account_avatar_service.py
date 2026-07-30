import socket

import httpx
import pytest

from app.core.outbound_url import UnsafeOutboundURLError
from app.services.account_avatar import (
    UnsupportedAccountAvatarError,
    fetch_account_avatar,
    normalize_douyin_avatar_url,
)


def _public_dns_result():
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/avatar.jpeg",
        "https://douyinpic.com.evil.example/avatar.jpeg",
        "http://p3.douyinpic.com/avatar.jpeg",
        "https://127.0.0.1/avatar.jpeg",
    ],
)
def test_avatar_url_rejects_non_douyin_or_unsafe_hosts(url: str) -> None:
    with pytest.raises(UnsafeOutboundURLError):
        normalize_douyin_avatar_url(url)


def test_avatar_url_removes_remote_query_before_fetching() -> None:
    assert normalize_douyin_avatar_url(
        "https://p3.douyinpic.com/aweme/100x100/avatar.jpeg?from=secret#fragment"
    ) == "https://p3.douyinpic.com/aweme/100x100/avatar.jpeg"


@pytest.mark.asyncio
async def test_fetch_account_avatar_accepts_only_bounded_image_content(monkeypatch) -> None:
    requests: list[httpx.Request] = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: _public_dns_result())

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=b"image-bytes",
            headers={"content-type": "image/webp"},
            request=request,
        )

    image = await fetch_account_avatar(
        "https://p3.douyinpic.com/avatar.webp?from=profile",
        _transport=httpx.MockTransport(handler),
    )

    assert image.content == b"image-bytes"
    assert image.content_type == "image/webp"
    assert requests[0].url == httpx.URL("https://93.184.216.34/avatar.webp")
    assert requests[0].headers["host"] == "p3.douyinpic.com"


@pytest.mark.asyncio
async def test_fetch_account_avatar_rejects_non_image_content(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: _public_dns_result())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"<html>not an image</html>",
            headers={"content-type": "text/html"},
            request=request,
        )

    with pytest.raises(UnsupportedAccountAvatarError):
        await fetch_account_avatar(
            "https://p3.douyinpic.com/avatar.jpeg",
            _transport=httpx.MockTransport(handler),
        )
