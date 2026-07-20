"""SSRF-resistant URL validation and bounded outbound HTTP requests."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

import httpx

_MAX_URL_LENGTH = 2048
_METADATA_ADDRESSES = frozenset(
    {
        ipaddress.ip_address("168.63.129.16"),
        ipaddress.ip_address("169.254.169.254"),
    }
)


class UnsafeOutboundURLError(ValueError):
    """Raised when an endpoint cannot be proven safe for public outbound access."""


class OutboundRequestError(RuntimeError):
    """Raised when a bounded outbound request cannot complete safely."""


class OutboundRequestTimeoutError(OutboundRequestError):
    """Raised when the total outbound operation deadline is exceeded."""


class OutboundRedirectError(OutboundRequestError):
    """Raised when an upstream attempts to redirect a protected request."""


class OutboundResponseTooLargeError(OutboundRequestError):
    """Raised before an upstream response can exceed the configured memory bound."""


@dataclass(frozen=True)
class ValidatedOutboundTarget:
    """A public HTTPS target whose connection address has already been vetted."""

    original_url: str
    hostname: str
    port: int
    addresses: tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]
    pinned_url: str
    host_header: str


@dataclass(frozen=True)
class OutboundRequestPolicy:
    connect_timeout: float = 5.0
    read_timeout: float = 15.0
    write_timeout: float = 10.0
    pool_timeout: float = 5.0
    total_timeout: float = 20.0
    max_response_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        timeout_values = (
            self.connect_timeout,
            self.read_timeout,
            self.write_timeout,
            self.pool_timeout,
            self.total_timeout,
        )
        if any(value <= 0 for value in timeout_values) or self.max_response_bytes <= 0:
            raise ValueError("outbound request policy limits must be positive")

    def httpx_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect_timeout,
            read=self.read_timeout,
            write=self.write_timeout,
            pool=self.pool_timeout,
        )


@dataclass
class BoundedOutboundStream:
    """Streaming response wrapper that enforces a cumulative byte limit."""

    _response: httpx.Response
    _max_response_bytes: int
    _bytes_read: int = 0

    @property
    def status_code(self) -> int:
        return self._response.status_code

    @property
    def headers(self) -> httpx.Headers:
        return self._response.headers

    @property
    def request(self) -> httpx.Request:
        return self._response.request

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        async for chunk in self._response.aiter_bytes():
            self._bytes_read += len(chunk)
            if self._bytes_read > self._max_response_bytes:
                raise OutboundResponseTooLargeError("outbound response exceeded size limit")
            yield chunk


DEFAULT_OUTBOUND_REQUEST_POLICY = OutboundRequestPolicy()


def _parse_public_https_url(url: str) -> tuple[SplitResult, str, int]:
    if not isinstance(url, str) or not url or len(url) > _MAX_URL_LENGTH:
        raise UnsafeOutboundURLError("outbound URL is invalid")
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port or 443
    except (TypeError, ValueError, UnicodeError) as exc:
        raise UnsafeOutboundURLError("outbound URL is invalid") from exc
    if parsed.scheme.lower() != "https" or hostname is None or not parsed.netloc:
        raise UnsafeOutboundURLError("outbound URL must use HTTPS with a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeOutboundURLError("outbound URL must not contain user information")
    if parsed.query:
        raise UnsafeOutboundURLError("outbound URL must not contain a query string")
    if "#" in url:
        raise UnsafeOutboundURLError("outbound URL must not contain a fragment")

    try:
        hostname = hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise UnsafeOutboundURLError("outbound URL hostname is invalid") from exc
    if not hostname or hostname == "localhost" or hostname.endswith(".localhost"):
        raise UnsafeOutboundURLError("outbound URL hostname is not public")
    try:
        ipaddress.ip_address(hostname.split("%", 1)[0])
    except ValueError:
        pass
    else:
        raise UnsafeOutboundURLError("outbound URL must use a DNS hostname")
    return parsed, hostname, port


def _resolved_addresses(
    results: list[tuple[Any, ...]],
) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for family, _socktype, _proto, _canonname, sockaddr in results:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        try:
            addresses.add(ipaddress.ip_address(sockaddr[0].split("%", 1)[0]))
        except (ValueError, TypeError, IndexError) as exc:
            raise UnsafeOutboundURLError("outbound hostname returned an invalid address") from exc
    return addresses


def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return address.is_global and not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def _build_pinned_url(
    parsed: SplitResult,
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    port: int,
) -> str:
    address_text = f"[{address}]" if address.version == 6 else str(address)
    netloc = address_text if port == 443 else f"{address_text}:{port}"
    return urlunsplit(("https", netloc, parsed.path, "", ""))


async def _resolve_public_https_target(url: str) -> ValidatedOutboundTarget:
    parsed, hostname, port = _parse_public_https_url(url)
    try:
        results = await asyncio.to_thread(
            socket.getaddrinfo,
            hostname,
            port,
            socket.AF_UNSPEC,
            socket.SOCK_STREAM,
        )
    except (OSError, UnicodeError) as exc:
        raise UnsafeOutboundURLError("outbound hostname could not be resolved") from exc

    addresses = _resolved_addresses(results)
    if not addresses:
        raise UnsafeOutboundURLError("outbound hostname did not resolve to a public address")
    if any(
        address in _METADATA_ADDRESSES or not _is_public_address(address)
        for address in addresses
    ):
        raise UnsafeOutboundURLError("outbound hostname resolved to a non-public address")
    ordered_addresses = tuple(sorted(addresses, key=lambda item: (item.version, item.packed)))
    selected_address = ordered_addresses[0]
    return ValidatedOutboundTarget(
        original_url=url,
        hostname=hostname,
        port=port,
        addresses=ordered_addresses,
        pinned_url=_build_pinned_url(parsed, selected_address, port),
        host_header=hostname if port == 443 else f"{hostname}:{port}",
    )


async def validate_public_https_url(url: str) -> str:
    """Resolve and accept a HTTPS hostname only when every address is globally routable."""
    target = await _resolve_public_https_target(url)
    return target.original_url


def _validated_request_options(
    target: ValidatedOutboundTarget,
    request_options: dict[str, Any],
) -> dict[str, Any]:
    if "follow_redirects" in request_options or "timeout" in request_options:
        raise ValueError("redirect and timeout behavior is controlled by the outbound policy")
    if "params" in request_options:
        raise ValueError("outbound query parameters are not allowed")
    options = dict(request_options)
    headers = httpx.Headers(options.pop("headers", None))
    headers["Host"] = target.host_header
    extensions = dict(options.pop("extensions", None) or {})
    extensions["sni_hostname"] = target.hostname
    options["headers"] = headers
    options["extensions"] = extensions
    return options


def _validate_response_headers(response: httpx.Response, policy: OutboundRequestPolicy) -> None:
    if 300 <= response.status_code < 400:
        raise OutboundRedirectError("outbound redirects are not allowed")
    content_length = response.headers.get("content-length")
    if content_length is None:
        return
    try:
        if int(content_length) > policy.max_response_bytes:
            raise OutboundResponseTooLargeError("outbound response exceeded size limit")
    except ValueError:
        return


@asynccontextmanager
async def bounded_outbound_stream(
    method: str,
    url: str,
    *,
    _transport: httpx.AsyncBaseTransport | None = None,
    policy: OutboundRequestPolicy = DEFAULT_OUTBOUND_REQUEST_POLICY,
    **request_options: Any,
) -> AsyncIterator[BoundedOutboundStream]:
    """Revalidate, pin, and stream a bounded response without redirects or proxies."""
    try:
        async with asyncio.timeout(policy.total_timeout):
            target = await _resolve_public_https_target(url)
            options = _validated_request_options(target, request_options)
            async with httpx.AsyncClient(
                follow_redirects=False,
                proxy=None,
                transport=_transport,
                trust_env=False,
            ) as client:
                async with client.stream(
                    method,
                    target.pinned_url,
                    follow_redirects=False,
                    timeout=policy.httpx_timeout(),
                    **options,
                ) as response:
                    _validate_response_headers(response, policy)
                    yield BoundedOutboundStream(response, policy.max_response_bytes)
    except TimeoutError:
        raise OutboundRequestTimeoutError("outbound request timed out") from None
    except httpx.TimeoutException:
        raise OutboundRequestTimeoutError("outbound request timed out") from None
    except httpx.RequestError:
        raise OutboundRequestError("outbound request failed") from None


async def bounded_outbound_request(
    method: str,
    url: str,
    *,
    _transport: httpx.AsyncBaseTransport | None = None,
    policy: OutboundRequestPolicy = DEFAULT_OUTBOUND_REQUEST_POLICY,
    **request_options: Any,
) -> httpx.Response:
    """Revalidate, send without redirects, and consume a bounded response."""
    async with bounded_outbound_stream(
        method,
        url,
        _transport=_transport,
        policy=policy,
        **request_options,
    ) as response:
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            content=bytes(content),
            request=response.request,
        )
