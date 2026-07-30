"""Securely retrieve the synchronized avatar for a Douyin account."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.core.outbound_url import (
    OutboundRequestPolicy,
    UnsafeOutboundURLError,
    bounded_outbound_request,
)

_ALLOWED_AVATAR_CONTENT_TYPES = frozenset(
    {"image/avif", "image/jpeg", "image/png", "image/webp"}
)
_AVATAR_REQUEST_POLICY = OutboundRequestPolicy(
    connect_timeout=4.0,
    read_timeout=8.0,
    write_timeout=4.0,
    pool_timeout=4.0,
    total_timeout=10.0,
    max_response_bytes=512 * 1024,
)


class UnsupportedAccountAvatarError(ValueError):
    """Raised when an upstream avatar is unavailable or is not a supported image."""


@dataclass(frozen=True)
class AccountAvatarImage:
    content: bytes
    content_type: str


def normalize_douyin_avatar_url(url: str) -> str:
    """Allow only HTTPS Douyin image hosts and discard remote tracking parameters."""
    if not isinstance(url, str) or not url:
        raise UnsafeOutboundURLError("account avatar URL is invalid")
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError, UnicodeError) as exc:
        raise UnsafeOutboundURLError("account avatar URL is invalid") from exc
    if (
        parsed.scheme.lower() != "https"
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise UnsafeOutboundURLError("account avatar URL is not allowed")

    hostname = hostname.rstrip(".").lower()
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise UnsafeOutboundURLError("account avatar URL is not allowed")
    if hostname != "douyinpic.com" and not hostname.endswith(".douyinpic.com"):
        raise UnsafeOutboundURLError("account avatar URL is not allowed")

    netloc = hostname if port is None else f"{hostname}:{port}"
    return urlunsplit(("https", netloc, parsed.path or "/", "", ""))


async def fetch_account_avatar(
    url: str,
    *,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> AccountAvatarImage:
    """Fetch one bounded image after domain validation and DNS pinning."""
    safe_url = normalize_douyin_avatar_url(url)
    response = await bounded_outbound_request(
        "GET",
        safe_url,
        _transport=_transport,
        _allow_mixed_dns=True,
        policy=_AVATAR_REQUEST_POLICY,
        headers={
            "Accept": "image/avif,image/webp,image/png,image/jpeg",
            "User-Agent": "DyFlow-avatar-fetch/1.0",
        },
    )
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if response.status_code != 200 or content_type not in _ALLOWED_AVATAR_CONTENT_TYPES:
        raise UnsupportedAccountAvatarError("account avatar response is unsupported")
    return AccountAvatarImage(content=response.content, content_type=content_type)
