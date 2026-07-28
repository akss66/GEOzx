import socket

import httpx
import pytest

from app.core.outbound_url import (
    OutboundRedirectError,
    OutboundRequestPolicy,
    OutboundResponseTooLargeError,
    UnsafeOutboundURLError,
    bounded_outbound_request,
    bounded_outbound_stream,
    validate_public_https_url,
)


def _dns_results(*addresses: str):
    results = []
    for address in addresses:
        if ":" in address:
            results.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, 443, 0, 0)))
        else:
            results.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)))
    return results


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com/v1",
        "https://127.0.0.1/v1",
        "https://[2606:4700:4700::1111]/v1",
        "https://169.254.169.254/latest/meta-data",
        "https://user:password@api.example.com/v1",
        "https://api.example.com/v1?api_key=secret",
        "https://api.example.com/v1#fragment",
    ],
)
async def test_rejects_unsafe_provider_url_syntax_without_dns(monkeypatch, url):
    def unexpected_dns(*args, **kwargs):
        raise AssertionError("unsafe URL should fail before DNS")

    monkeypatch.setattr(socket, "getaddrinfo", unexpected_dns)

    with pytest.raises(UnsafeOutboundURLError):
        await validate_public_https_url(url)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.8",
        "172.16.2.4",
        "192.168.1.9",
        "169.254.169.254",
        "168.63.129.16",
        "::1",
        "fc00::1",
        "fe80::1",
        "ff02::1",
    ],
)
async def test_rejects_any_non_global_or_metadata_dns_result(monkeypatch, address):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: _dns_results(address))

    with pytest.raises(UnsafeOutboundURLError):
        await validate_public_https_url("https://api.example.com/v1")


async def test_rejects_mixed_public_and_private_dns_answers(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _dns_results("93.184.216.34", "10.0.0.8"),
    )

    with pytest.raises(UnsafeOutboundURLError):
        await validate_public_https_url("https://api.example.com/v1")


async def test_trusted_request_can_filter_mixed_dns_and_pin_public_answer(monkeypatch):
    requests: list[httpx.Request] = []

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _dns_results("93.184.216.34", "fd00::8"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": []}, request=request)

    response = await bounded_outbound_request(
        "GET",
        "https://api.example.com/v1/models",
        _transport=httpx.MockTransport(handler),
        _allow_mixed_dns=True,
    )

    assert response.status_code == 200
    assert requests[0].url == httpx.URL("https://93.184.216.34/v1/models")
    assert requests[0].headers["host"] == "api.example.com"


async def test_accepts_hostname_only_when_every_dns_answer_is_global(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _dns_results("93.184.216.34", "2606:4700:4700::1111"),
    )
    url = "https://api.example.com/v1"

    assert await validate_public_https_url(url) == url


async def test_validation_errors_do_not_echo_url_query_secrets(monkeypatch):
    secret = "sk-query-secret"

    def unexpected_dns(*args, **kwargs):
        raise AssertionError("query-bearing URL should fail before DNS")

    monkeypatch.setattr(socket, "getaddrinfo", unexpected_dns)

    with pytest.raises(UnsafeOutboundURLError) as exc_info:
        await validate_public_https_url(f"https://api.example.com/v1?api_key={secret}")

    assert secret not in str(exc_info.value)


async def test_outbound_request_pins_validated_ip_and_preserves_host_and_sni(monkeypatch):
    dns_calls = 0
    requests: list[httpx.Request] = []

    def public_dns(*args, **kwargs):
        nonlocal dns_calls
        dns_calls += 1
        return _dns_results("93.184.216.34")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b'{"data": []}', request=request)

    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    policy = OutboundRequestPolicy(
        connect_timeout=1.0,
        read_timeout=2.0,
        write_timeout=3.0,
        pool_timeout=4.0,
        total_timeout=5.0,
        max_response_bytes=1024,
    )
    transport = httpx.MockTransport(handler)
    first = await bounded_outbound_request(
        "GET",
        "https://api.example.com/v1/models",
        _transport=transport,
        policy=policy,
    )
    second = await bounded_outbound_request(
        "GET",
        "https://api.example.com/v1/models",
        _transport=transport,
        policy=policy,
    )

    assert first.json() == {"data": []}
    assert second.status_code == 200
    assert dns_calls == 2
    assert len(requests) == 2
    assert requests[0].url == httpx.URL("https://93.184.216.34/v1/models")
    assert requests[0].headers["host"] == "api.example.com"
    assert requests[0].extensions["sni_hostname"] == "api.example.com"
    assert requests[0].extensions["timeout"] == {
        "connect": 1.0,
        "read": 2.0,
        "write": 3.0,
        "pool": 4.0,
    }


async def test_outbound_request_rejects_redirect_without_following(monkeypatch):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            302,
            headers={"location": "https://internal.example/latest"},
            request=request,
        )

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _dns_results("93.184.216.34"),
    )
    with pytest.raises(OutboundRedirectError):
        await bounded_outbound_request(
            "GET",
            "https://api.example.com/v1/models",
            _transport=httpx.MockTransport(handler),
        )

    assert calls == 1


async def test_outbound_request_bounds_streamed_response_consumption(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 9, request=request)

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _dns_results("93.184.216.34"),
    )
    policy = OutboundRequestPolicy(max_response_bytes=8)
    with pytest.raises(OutboundResponseTooLargeError):
        await bounded_outbound_request(
            "GET",
            "https://api.example.com/v1/models",
            _transport=httpx.MockTransport(handler),
            policy=policy,
        )


class _AsyncChunks(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


async def test_outbound_stream_pins_validated_ip_preserves_host_and_sni_and_closes_stream(
    monkeypatch,
):
    requests: list[httpx.Request] = []
    stream = _AsyncChunks([b"data: hello\n\n", b"data: [DONE]\n\n"])

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, stream=stream, request=request)

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _dns_results("93.184.216.34"),
    )
    async with bounded_outbound_stream(
        "POST",
        "https://api.example.com/v1/chat/completions",
        headers={"authorization": "Bearer sk-test"},
        json={"stream": True},
        _transport=httpx.MockTransport(handler),
    ) as response:
        body = b"".join([chunk async for chunk in response.aiter_bytes()])

    assert body == b"data: hello\n\ndata: [DONE]\n\n"
    assert requests[0].url == httpx.URL("https://93.184.216.34/v1/chat/completions")
    assert requests[0].headers["host"] == "api.example.com"
    assert requests[0].extensions["sni_hostname"] == "api.example.com"
    assert stream.closed is True


async def test_outbound_stream_enforces_cumulative_byte_limit(monkeypatch):
    stream = _AsyncChunks([b"1234", b"56789"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream, request=request)

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _dns_results("93.184.216.34"),
    )
    with pytest.raises(OutboundResponseTooLargeError):
        async with bounded_outbound_stream(
            "GET",
            "https://api.example.com/v1/models",
            _transport=httpx.MockTransport(handler),
            policy=OutboundRequestPolicy(max_response_bytes=8),
        ) as response:
            async for _chunk in response.aiter_bytes():
                pass

    assert stream.closed is True
