"""Typed, secret-safe WeChat draft boundary contracts."""

from __future__ import annotations

import json
import socket
from copy import deepcopy

import httpx
import pytest
from pydantic import ValidationError

from app.schemas.wechat_article import WechatDraftArticle
from app.services.wechat_component import WechatIntegrationError
from app.services.wechat_drafts import (
    WechatDraftClient,
    compute_remote_hash,
    normalize_remote_draft,
)


def _article(**overrides: object) -> WechatDraftArticle:
    values: dict[str, object] = {
        "title": "夏季隔热指南",
        "author": "悠护",
        "digest": "一篇有事实依据的夏季隔热指南",
        "content": '<p>正文</p><img alt="" src="https://mmbiz.qpic.cn/article/body.jpg">',
        "thumb_media_id": "cover-media-id",
        "need_open_comment": 1,
        "only_fans_can_comment": 0,
        "content_source_url": "https://example.com/source",
    }
    values.update(overrides)
    return WechatDraftArticle.model_validate(values)


@pytest.mark.asyncio
async def test_add_draft_rejects_success_without_media_id() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errcode": 0})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(WechatIntegrationError) as captured:
            await WechatDraftClient(client=http_client).add_draft(
                access_token="authorizer-secret",
                article=_article(),
            )

    assert captured.value.code == "missing_media_id"
    assert captured.value.endpoint == "/cgi-bin/draft/add"
    assert captured.value.retryable is False
    assert "authorizer-secret" not in str(captured.value)


@pytest.mark.asyncio
async def test_platform_error_is_typed_retryable_and_secret_free() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "errcode": -1,
                "errmsg": "system busy access_token=leaked-token\nretry",
                "rid": "rid-123",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(WechatIntegrationError) as captured:
            await WechatDraftClient(client=http_client).add_draft(
                access_token="authorizer-secret",
                article=_article(),
            )

    error = captured.value
    assert error.code == -1
    assert error.retryable is True
    assert error.rid == "rid-123"
    assert error.endpoint == "/cgi-bin/draft/add"
    assert error.errmsg == "system busy access_token=[redacted] retry"
    assert "leaked-token" not in str(error)
    assert "authorizer-secret" not in str(error)
    assert "leaked-token" not in repr(error)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("errmsg", "expected"),
    [
        (
            'platform said {"access_token":"leak-json-123","status":"busy"}',
            'platform said {"access_token":"[redacted]","status":"busy"}',
        ),
        (
            "authorizer_access_token: leak-colon-456, retry later",
            "authorizer_access_token: [redacted], retry later",
        ),
        (
            "refresh_token = 'leak-equals-789' while token_count: 3",
            "refresh_token = '[redacted]' while token_count: 3",
        ),
    ],
)
async def test_platform_error_redacts_common_secret_assignment_styles(
    errmsg: str,
    expected: str,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errcode": 40001, "errmsg": errmsg})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(WechatIntegrationError) as captured:
            await WechatDraftClient(client=http_client).add_draft(
                access_token="authorizer-secret",
                article=_article(),
            )

    assert captured.value.errmsg == expected
    assert "leak-" not in captured.value.errmsg
    assert "token_count: 3" in expected or "token_count" not in errmsg


@pytest.mark.asyncio
async def test_platform_error_redacts_quoted_secret_before_bounding_message() -> None:
    long_secret = "s" * 80
    errmsg = f'{"x" * 270} {{"access_token":"{long_secret}"}}'

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errcode": 40001, "errmsg": errmsg})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(WechatIntegrationError) as captured:
            await WechatDraftClient(client=http_client).add_draft(
                access_token="authorizer-secret",
                article=_article(),
            )

    assert long_secret not in captured.value.errmsg
    assert "ssss" not in captured.value.errmsg
    assert '"access_token":"[redacted]"' in captured.value.errmsg
    assert len(captured.value.errmsg) <= 300


@pytest.mark.asyncio
async def test_platform_error_redacts_url_query_and_json_token_in_errmsg_and_rid() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "errcode": 40001,
                "errmsg": "failed https://api.example.test/?access_token=query-leak&mode=1",
                "rid": "trace {'token':'json-leak','token_count':3}",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(WechatIntegrationError) as captured:
            await WechatDraftClient(client=http_client).add_draft(
                access_token="authorizer-secret",
                article=_article(),
            )

    error = captured.value
    assert error.errmsg == ("failed https://api.example.test/?access_token=[redacted]&mode=1")
    assert error.rid == "trace {'token':'[redacted]','token_count':3}"
    for leaked in ("query-leak", "json-leak", "authorizer-secret"):
        assert leaked not in error.errmsg
        assert leaked not in error.rid
        assert leaked not in str(error)
        assert leaked not in repr(error)
    assert error.__cause__ is None


@pytest.mark.asyncio
async def test_platform_rid_is_sanitized() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "errcode": 40001,
                "errmsg": "invalid credential",
                "rid": "rid access_token=leaked-token\nnext",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(WechatIntegrationError) as captured:
            await WechatDraftClient(client=http_client).add_draft(
                access_token="authorizer-secret",
                article=_article(),
            )

    assert captured.value.rid == "rid access_token=[redacted] next"
    assert "leaked-token" not in repr(captured.value)


@pytest.mark.asyncio
async def test_malformed_json_has_stable_secret_free_category() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json authorizer-secret")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(WechatIntegrationError) as captured:
            await WechatDraftClient(client=http_client).add_draft(
                access_token="authorizer-secret",
                article=_article(),
            )

    assert captured.value.code == "malformed_json"
    assert captured.value.endpoint == "/cgi-bin/draft/add"
    assert captured.value.retryable is False
    assert "not-json" not in str(captured.value)
    assert "authorizer-secret" not in str(captured.value)
    assert captured.value.__cause__ is None


@pytest.mark.asyncio
async def test_non_object_json_is_rejected() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"media_id": "wrong-shape"}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(WechatIntegrationError) as captured:
            await WechatDraftClient(client=http_client).add_draft(
                access_token="authorizer-secret",
                article=_article(),
            )

    assert captured.value.code == "invalid_response"
    assert captured.value.retryable is False


@pytest.mark.asyncio
async def test_timeout_is_retryable_and_secret_free() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("authorizer-secret", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(WechatIntegrationError) as captured:
            await WechatDraftClient(client=http_client).add_draft(
                access_token="authorizer-secret",
                article=_article(),
            )

    assert captured.value.code == "request_timeout"
    assert captured.value.endpoint == "/cgi-bin/draft/add"
    assert captured.value.retryable is True
    assert "authorizer-secret" not in str(captured.value)
    assert captured.value.__cause__ is None


@pytest.mark.asyncio
async def test_network_error_is_retryable_and_secret_free() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("authorizer-secret", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(WechatIntegrationError) as captured:
            await WechatDraftClient(client=http_client).add_draft(
                access_token="authorizer-secret",
                article=_article(),
            )

    assert captured.value.code == "network_error"
    assert captured.value.retryable is True
    assert captured.value.__cause__ is None
    assert "authorizer-secret" not in repr(captured.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "retryable"),
    [(400, False), (429, True), (503, True)],
)
async def test_http_error_retryability(status_code: int, retryable: bool) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"message": "authorizer-secret"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(WechatIntegrationError) as captured:
            await WechatDraftClient(client=http_client).add_draft(
                access_token="authorizer-secret",
                article=_article(),
            )

    assert captured.value.code == f"http_{status_code}"
    assert captured.value.retryable is retryable
    assert "authorizer-secret" not in str(captured.value)


@pytest.mark.asyncio
async def test_malformed_5xx_still_uses_retryable_http_category() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream authorizer-secret")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(WechatIntegrationError) as captured:
            await WechatDraftClient(client=http_client).add_draft(
                access_token="authorizer-secret",
                article=_article(),
            )

    assert captured.value.code == "http_503"
    assert captured.value.retryable is True
    assert captured.value.__cause__ is None


@pytest.mark.asyncio
async def test_upload_article_image_uses_multipart_and_returns_wechat_url() -> None:
    observed: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed["path"] = request.url.path
        observed["token"] = request.url.params.get("access_token")
        observed["content_type"] = request.headers.get("content-type")
        observed["body"] = await request.aread()
        return httpx.Response(
            200,
            json={"url": "https://mmbiz.qpic.cn/article/body.png"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        url = await WechatDraftClient(client=http_client).upload_article_image(
            access_token="authorizer-secret",
            filename="body.png",
            content=b"safe-image-bytes",
            media_type="image/png",
        )

    assert url == "https://mmbiz.qpic.cn/article/body.png"
    assert observed["path"] == "/cgi-bin/media/uploadimg"
    assert observed["token"] == "authorizer-secret"
    assert str(observed["content_type"]).startswith("multipart/form-data; boundary=")
    assert b'name="media"; filename="body.png"' in observed["body"]
    assert b"Content-Type: image/png" in observed["body"]
    assert b"safe-image-bytes" in observed["body"]


@pytest.mark.asyncio
async def test_upload_article_image_rejects_non_wechat_or_credentialed_url() -> None:
    responses = iter(
        [
            {"url": "https://objects.example.com/body.png"},
            {"url": "https://user:password@mmbiz.qpic.cn/body.png"},
        ]
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(responses))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = WechatDraftClient(client=http_client)
        for _ in range(2):
            with pytest.raises(WechatIntegrationError) as captured:
                await client.upload_article_image(
                    access_token="authorizer-secret",
                    filename="body.png",
                    content=b"safe-image-bytes",
                    media_type="image/png",
                )
            assert captured.value.code == "invalid_image_url"
            assert "objects.example.com" not in str(captured.value)
            assert "password" not in str(captured.value)


@pytest.mark.asyncio
async def test_add_permanent_cover_uses_multipart_and_requires_media_id() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/cgi-bin/material/add_material"
        assert request.url.params.get("type") == "image"
        assert request.url.params.get("access_token") == "authorizer-secret"
        assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
        return httpx.Response(200, json={"media_id": "cover-id"} if calls == 1 else {})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = WechatDraftClient(client=http_client)
        assert (
            await client.add_permanent_cover(
                access_token="authorizer-secret",
                filename="cover.jpg",
                content=b"cover-bytes",
                media_type="image/jpeg",
            )
            == "cover-id"
        )
        with pytest.raises(WechatIntegrationError) as captured:
            await client.add_permanent_cover(
                access_token="authorizer-secret",
                filename="cover.jpg",
                content=b"cover-bytes",
                media_type="image/jpeg",
            )

    assert captured.value.code == "missing_media_id"


def test_draft_article_rejects_unknown_fields_and_external_image_urls() -> None:
    with pytest.raises(ValidationError):
        _article(platform_private_field="must-not-pass")
    with pytest.raises(ValidationError):
        _article(content='<img src="https://objects.example.com/body.png">')
    with pytest.raises(ValidationError):
        _article(content='<img src="javascript:alert(1)">')
    with pytest.raises(ValidationError):
        _article(content="<img src=https://objects.example.com/body.png>")


@pytest.mark.asyncio
async def test_add_draft_sends_only_typed_article_fields() -> None:
    observed: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.update(json.loads(request.content))
        return httpx.Response(200, json={"media_id": "draft-media-id"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        media_id = await WechatDraftClient(client=http_client).add_draft(
            access_token="authorizer-secret",
            article=_article(),
        )

    assert media_id == "draft-media-id"
    assert observed == {
        "articles": [
            {
                "title": "夏季隔热指南",
                "author": "悠护",
                "digest": "一篇有事实依据的夏季隔热指南",
                "content": '<p>正文</p><img alt="" src="https://mmbiz.qpic.cn/article/body.jpg">',
                "thumb_media_id": "cover-media-id",
                "need_open_comment": 1,
                "only_fans_can_comment": 0,
                "content_source_url": "https://example.com/source",
            }
        ]
    }


@pytest.mark.asyncio
async def test_get_draft_requires_typed_news_item_list() -> None:
    responses = iter(
        [
            {
                "news_item": [
                    {
                        **_article().model_dump(mode="json", exclude_none=True),
                        "remote_read_only_field": "ignored-not-propagated",
                    }
                ]
            },
            {"news_item": "not-a-list"},
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/cgi-bin/draft/get"
        assert json.loads(request.content) == {"media_id": "draft-media-id"}
        return httpx.Response(200, json=next(responses))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = WechatDraftClient(client=http_client)
        result = await client.get_draft(access_token="authorizer-secret", media_id="draft-media-id")
        assert result.news_item[0].title == "夏季隔热指南"
        assert "remote_read_only_field" not in result.news_item[0].model_dump()
        with pytest.raises(WechatIntegrationError) as captured:
            await client.get_draft(access_token="authorizer-secret", media_id="draft-media-id")

    assert captured.value.code == "invalid_news_item"


@pytest.mark.asyncio
async def test_get_draft_normalizes_empty_optional_remote_fields() -> None:
    remote_item = _article().model_dump(mode="json", exclude_none=True)
    remote_item.update({"author": "", "content_source_url": ""})

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"news_item": [remote_item]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        result = await WechatDraftClient(client=http_client).get_draft(
            access_token="authorizer-secret",
            media_id="draft-media-id",
        )

    assert result.news_item[0].author is None
    assert result.news_item[0].content_source_url is None


@pytest.mark.asyncio
async def test_get_draft_rejects_nonempty_invalid_remote_source_url() -> None:
    remote_item = _article().model_dump(mode="json", exclude_none=True)
    remote_item["content_source_url"] = "not-a-url"

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"news_item": [remote_item]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(WechatIntegrationError) as captured:
            await WechatDraftClient(client=http_client).get_draft(
                access_token="authorizer-secret",
                media_id="draft-media-id",
            )

    assert captured.value.code == "invalid_news_item"


@pytest.mark.asyncio
async def test_update_draft_requires_explicit_zero_errcode() -> None:
    observed: list[dict[str, object]] = []
    responses = iter([{"errcode": 0}, {}, {"errcode": 1, "errmsg": "invalid"}])

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/cgi-bin/draft/update"
        observed.append(json.loads(request.content))
        return httpx.Response(200, json=next(responses))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = WechatDraftClient(client=http_client)
        await client.update_draft(
            access_token="authorizer-secret",
            media_id="draft-media-id",
            index=0,
            article=_article(),
        )
        with pytest.raises(WechatIntegrationError) as missing:
            await client.update_draft(
                access_token="authorizer-secret",
                media_id="draft-media-id",
                index=0,
                article=_article(),
            )
        with pytest.raises(WechatIntegrationError) as platform:
            await client.update_draft(
                access_token="authorizer-secret",
                media_id="draft-media-id",
                index=0,
                article=_article(),
            )

    assert observed[0] == {
        "media_id": "draft-media-id",
        "index": 0,
        "articles": _article().model_dump(mode="json", exclude_none=True),
    }
    assert missing.value.code == "missing_success_errcode"
    assert platform.value.code == 1


def test_remote_hash_ignores_attribute_order_and_intertag_edge_whitespace() -> None:
    remote_a = _article(
        content=(
            '  <section><p class="lead" data-kind="intro">正文 空格</p>'
            '<img alt="封面" src="https://mmbiz.qpic.cn/body.png"></section>  '
        )
    )
    remote_b = _article(
        content=(
            '<section>\n  <p data-kind="intro" class="lead">正文 空格</p>\n'
            '<img src="https://mmbiz.qpic.cn/body.png" alt="封面">\n</section>'
        )
    )

    assert normalize_remote_draft(remote_a) == normalize_remote_draft(remote_b)
    assert compute_remote_hash(remote_a) == compute_remote_hash(remote_b)


def test_remote_hash_preserves_significant_whitespace_between_inline_nodes() -> None:
    spaced = _article(
        content=(
            '<p><a href="https://example.com/a">甲</a> <a href="https://example.com/b">乙</a></p>'
        )
    )
    joined = _article(
        content=(
            '<p><a href="https://example.com/a">甲</a><a href="https://example.com/b">乙</a></p>'
        )
    )

    assert compute_remote_hash(spaced) != compute_remote_hash(joined)


def test_remote_hash_preserves_inline_whitespace_under_generic_containers() -> None:
    spaced = _article(content="<div><span>a</span> <span>b</span></div>")
    joined = _article(content="<div><span>a</span><span>b</span></div>")

    assert compute_remote_hash(spaced) != compute_remote_hash(joined)


def test_remote_hash_preserves_visible_space_inside_inline_wrapper() -> None:
    spaced = _article(content=('<p><span> </span><a href="https://example.com/b">b</a></p>'))
    joined = _article(content=('<p><span></span><a href="https://example.com/b">b</a></p>'))

    assert compute_remote_hash(spaced) != compute_remote_hash(joined)


def test_remote_hash_ignores_plain_space_intertag_whitespace_for_block_children() -> None:
    compact = _article(content="<div><p>a</p></div>")
    spaced = _article(content="<div>   <p>a</p></div>")

    assert compute_remote_hash(compact) == compute_remote_hash(spaced)


@pytest.mark.parametrize(
    ("compact", "formatted"),
    [
        (
            "<div><p>a</p><p>b</p></div>",
            "<div>\n  <p>a</p>\n  <p>b</p>\n</div>",
        ),
        (
            "<section><div><p>a</p></div></section>",
            "  <section>\n    <div>\n      <p>a</p>\n    </div>\n  </section>  ",
        ),
        (
            "<ul><li>a</li><li>b</li></ul>",
            "<ul>\n  <li>a</li>\n  <li>b</li>\n</ul>",
        ),
    ],
)
def test_remote_hash_ignores_block_container_formatting_whitespace(
    compact: str,
    formatted: str,
) -> None:
    assert compute_remote_hash(_article(content=compact)) == compute_remote_hash(
        _article(content=formatted)
    )


@pytest.mark.parametrize(
    "change",
    [
        {"content": "<p>正文  空格</p><p>第二段</p>"},
        {"content": "<p>第二段</p><p>正文 空格</p>"},
        {"content": "<h2>正文 空格</h2><p>第二段</p>"},
        {"content": '<p class="changed">正文 空格</p><p>第二段</p>'},
        {"thumb_media_id": "other-cover"},
        {"need_open_comment": 0},
        {"only_fans_can_comment": 1},
        {"content_source_url": "https://example.com/other-source"},
    ],
)
def test_remote_hash_preserves_rendered_and_package_semantics(
    change: dict[str, object],
) -> None:
    baseline = _article(content="<p>正文 空格</p><p>第二段</p>")
    changed_values: dict[str, object] = {"content": "<p>正文 空格</p><p>第二段</p>"}
    changed_values.update(change)
    changed = _article(**changed_values)
    assert compute_remote_hash(baseline) != compute_remote_hash(changed)


def test_remote_normalization_is_pure_and_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    value = _article().model_dump(mode="json", exclude_none=True)
    before = deepcopy(value)

    def forbid_network(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("remote normalization must not access the network")

    monkeypatch.setattr(socket, "create_connection", forbid_network)
    first = normalize_remote_draft(value)
    second = normalize_remote_draft(value)

    assert first == second
    assert value == before
    assert len(compute_remote_hash(value)) == 64
