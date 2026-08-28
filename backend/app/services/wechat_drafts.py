"""Typed server-side boundary for WeChat article assets and drafts."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from html import escape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from app.schemas.wechat_article import WechatDraftArticle, WechatRemoteDraft
from app.services.wechat_component import WechatIntegrationError

logger = logging.getLogger(__name__)

WECHAT_API_BASE_URL = "https://api.weixin.qq.com"
DRAFT_ADD_ENDPOINT = "/cgi-bin/draft/add"
DRAFT_GET_ENDPOINT = "/cgi-bin/draft/get"
DRAFT_UPDATE_ENDPOINT = "/cgi-bin/draft/update"
UPLOAD_ARTICLE_IMAGE_ENDPOINT = "/cgi-bin/media/uploadimg"
ADD_PERMANENT_MATERIAL_ENDPOINT = "/cgi-bin/material/add_material"
_RETRYABLE_WECHAT_CODES = frozenset({-1, 45009})
_BLOCK_HTML_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "dd",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }
)
_SECRET_KEY = r"(?:access_token|authorizer_access_token|refresh_token|secret|token)"
_QUOTED_SECRET_ASSIGNMENT = re.compile(
    rf"(?i)(?P<prefix>\b{_SECRET_KEY}\b[\"']?\s*[:=]\s*)"
    rf"(?P<quote>[\"'])(?P<value>.*?)(?P=quote)"
)
_UNQUOTED_SECRET_ASSIGNMENT = re.compile(
    rf"(?i)(?P<prefix>\b{_SECRET_KEY}\b[\"']?\s*[:=]\s*)"
    r"(?P<value>(?![\"'])[^\s,;&}\]]+)"
)


class WechatDraftIntegrationError(WechatIntegrationError):
    """Draft boundary error with a sanitized platform message, when available."""

    errmsg: str | None


def _sanitized_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    one_line = " ".join(value.split())
    quoted_redacted = _QUOTED_SECRET_ASSIGNMENT.sub(
        lambda match: (
            f"{match.group('prefix')}{match.group('quote')}[redacted]{match.group('quote')}"
        ),
        one_line,
    )
    redacted = _UNQUOTED_SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group('prefix')}[redacted]",
        quoted_redacted,
    )
    return redacted[:300]


def _integration_error(
    message: str,
    *,
    code: str | int,
    retryable: bool,
    endpoint: str,
    rid: str | None = None,
    errmsg: str | None = None,
) -> WechatDraftIntegrationError:
    error = WechatDraftIntegrationError(
        message,
        code=code,
        retryable=retryable,
        rid=rid,
        endpoint=endpoint,
    )
    error.errmsg = errmsg
    return error


def _validated_payload(payload: Any, *, endpoint: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise _integration_error(
            "WeChat API returned an invalid response",
            code="invalid_response",
            retryable=False,
            endpoint=endpoint,
        )
    errcode = payload.get("errcode")
    if type(errcode) is int and errcode != 0:
        errmsg = _sanitized_text(payload.get("errmsg"))
        rid = _sanitized_text(payload.get("rid"))
        raise _integration_error(
            f"WeChat API returned error {errcode}",
            code=errcode,
            retryable=errcode in _RETRYABLE_WECHAT_CODES,
            endpoint=endpoint,
            rid=rid,
            errmsg=errmsg,
        )
    if errcode not in (None, 0):
        raise _integration_error(
            "WeChat API returned an invalid response",
            code="invalid_response",
            retryable=False,
            endpoint=endpoint,
        )
    return payload


def _decode_response(response: httpx.Response, *, endpoint: str) -> dict[str, Any]:
    try:
        payload: Any = response.json()
    except ValueError:
        if response.is_error:
            raise _integration_error(
                "WeChat API request failed",
                code=f"http_{response.status_code}",
                retryable=response.status_code == 429 or response.status_code >= 500,
                endpoint=endpoint,
            ) from None
        raise _integration_error(
            "WeChat API returned malformed JSON",
            code="malformed_json",
            retryable=False,
            endpoint=endpoint,
        ) from None
    validated = _validated_payload(payload, endpoint=endpoint)
    if response.is_error:
        raise _integration_error(
            "WeChat API request failed",
            code=f"http_{response.status_code}",
            retryable=response.status_code == 429 or response.status_code >= 500,
            endpoint=endpoint,
        )
    return validated


class WechatDraftClient:
    """Consume a caller-supplied authorizer token without retaining it."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str = WECHAT_API_BASE_URL,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def add_draft(
        self,
        *,
        access_token: str,
        article: WechatDraftArticle,
    ) -> str:
        payload = await self._call_json_endpoint(
            DRAFT_ADD_ENDPOINT,
            access_token=access_token,
            json_body={"articles": [article.model_dump(mode="json", exclude_none=True)]},
        )
        media_id = payload.get("media_id") if isinstance(payload, dict) else None
        if not isinstance(media_id, str) or not media_id.strip():
            raise _integration_error(
                "WeChat draft response is missing media_id",
                code="missing_media_id",
                retryable=False,
                endpoint=DRAFT_ADD_ENDPOINT,
            )
        return media_id

    async def upload_article_image(
        self,
        *,
        access_token: str,
        filename: str,
        content: bytes,
        media_type: str,
    ) -> str:
        payload = await self._call_json_endpoint(
            UPLOAD_ARTICLE_IMAGE_ENDPOINT,
            access_token=access_token,
            files={"media": (filename, content, media_type)},
        )
        url = payload.get("url")
        if not isinstance(url, str) or not _is_wechat_image_url(url):
            raise _integration_error(
                "WeChat article image response contains an invalid URL",
                code="invalid_image_url",
                retryable=False,
                endpoint=UPLOAD_ARTICLE_IMAGE_ENDPOINT,
            )
        return url

    async def add_permanent_cover(
        self,
        *,
        access_token: str,
        filename: str,
        content: bytes,
        media_type: str,
    ) -> str:
        payload = await self._call_json_endpoint(
            ADD_PERMANENT_MATERIAL_ENDPOINT,
            access_token=access_token,
            params={"type": "image"},
            files={"media": (filename, content, media_type)},
        )
        media_id = payload.get("media_id")
        if not isinstance(media_id, str) or not media_id.strip():
            raise _integration_error(
                "WeChat permanent material response is missing media_id",
                code="missing_media_id",
                retryable=False,
                endpoint=ADD_PERMANENT_MATERIAL_ENDPOINT,
            )
        return media_id

    async def get_draft(
        self,
        *,
        access_token: str,
        media_id: str,
    ) -> WechatRemoteDraft:
        payload = await self._call_json_endpoint(
            DRAFT_GET_ENDPOINT,
            access_token=access_token,
            json_body={"media_id": media_id},
        )
        try:
            return WechatRemoteDraft.model_validate(payload)
        except ValidationError:
            raise _integration_error(
                "WeChat draft response contains invalid news_item",
                code="invalid_news_item",
                retryable=False,
                endpoint=DRAFT_GET_ENDPOINT,
            ) from None

    async def update_draft(
        self,
        *,
        access_token: str,
        media_id: str,
        index: int,
        article: WechatDraftArticle,
    ) -> None:
        payload = await self._call_json_endpoint(
            DRAFT_UPDATE_ENDPOINT,
            access_token=access_token,
            json_body={
                "media_id": media_id,
                "index": index,
                "articles": article.model_dump(mode="json", exclude_none=True),
            },
        )
        if payload.get("errcode") != 0:
            raise _integration_error(
                "WeChat draft update did not confirm success",
                code="missing_success_errcode",
                retryable=False,
                endpoint=DRAFT_UPDATE_ENDPOINT,
            )

    async def _post(
        self,
        endpoint: str,
        *,
        access_token: str,
        json: dict[str, Any],
    ) -> httpx.Response:
        return await self._request(
            endpoint,
            access_token=access_token,
            json=json,
        )

    async def _call_json_endpoint(
        self,
        endpoint: str,
        *,
        access_token: str,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        try:
            response = await self._request(
                endpoint,
                access_token=access_token,
                params=params,
                json=json_body,
                files=files,
            )
            payload = _decode_response(response, endpoint=endpoint)
        except WechatDraftIntegrationError as error:
            _log_wechat_api_request(
                endpoint=endpoint,
                outcome="error",
                duration_ms=round(max(0.0, time.monotonic() - started) * 1000),
                error=error,
            )
            raise
        _log_wechat_api_request(
            endpoint=endpoint,
            outcome="success",
            duration_ms=round(max(0.0, time.monotonic() - started) * 1000),
        )
        return payload

    async def _request(
        self,
        endpoint: str,
        *,
        access_token: str,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> httpx.Response:
        query = {"access_token": access_token, **(params or {})}
        try:
            return await self._client.post(
                f"{self._base_url}{endpoint}",
                params=query,
                json=json,
                files=files,
                timeout=10.0,
            )
        except httpx.TimeoutException:
            raise _integration_error(
                "WeChat API request timed out",
                code="request_timeout",
                retryable=True,
                endpoint=endpoint,
            ) from None
        except httpx.RequestError:
            raise _integration_error(
                "WeChat API request failed",
                code="network_error",
                retryable=True,
                endpoint=endpoint,
            ) from None


def _log_wechat_api_request(
    *,
    endpoint: str,
    outcome: str,
    duration_ms: int,
    error: WechatDraftIntegrationError | None = None,
) -> None:
    extra: dict[str, Any] = {
        "event_name": "wechat_api_request",
        "endpoint": endpoint,
        "outcome": outcome,
        "duration_ms": max(0, duration_ms),
    }
    if error is not None:
        extra["error_code"] = error.error_code
        extra["retryable"] = bool(error.retryable)
        if error.rid is not None:
            extra["rid"] = error.rid
    logger.info("wechat_api_request", extra=extra)


def _is_wechat_image_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").lower() == "mmbiz.qpic.cn"
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
    )


class _CanonicalHtmlParser(HTMLParser):
    """Serialize parsed HTML deterministically without changing text nodes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.stack: list[str] = []
        self.last_child: list[str | None] = []
        self.pending_whitespace: tuple[str, str, str | None] | None = None

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._flush_whitespace(next_tag=tag)
        self.parts.append(self._start(tag, attrs, closed=False))
        if self.last_child:
            self.last_child[-1] = tag
        if tag not in {"img", "hr", "br"}:
            self.stack.append(tag)
            self.last_child.append(None)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._flush_whitespace(next_tag=tag)
        self.parts.append(self._start(tag, attrs, closed=True))
        if self.last_child:
            self.last_child[-1] = tag

    def handle_endtag(self, tag: str) -> None:
        self._flush_whitespace(next_end_tag=tag)
        self.parts.append(f"</{tag}>")
        if tag in self.stack:
            index = len(self.stack) - 1 - self.stack[::-1].index(tag)
            del self.stack[index:]
            del self.last_child[index:]

    def handle_data(self, data: str) -> None:
        if not data.strip():
            if not self.stack:
                return
            if self.pending_whitespace is None:
                self.pending_whitespace = (data, self.stack[-1], self.last_child[-1])
            else:
                pending, parent, previous = self.pending_whitespace
                self.pending_whitespace = (pending + data, parent, previous)
            return
        self._flush_whitespace(next_text=True)
        self.parts.append(data)
        if self.last_child:
            self.last_child[-1] = "#text"

    def handle_entityref(self, name: str) -> None:
        self._flush_whitespace(next_text=True)
        self.parts.append(f"&{name};")
        if self.last_child:
            self.last_child[-1] = "#text"

    def handle_charref(self, name: str) -> None:
        self._flush_whitespace(next_text=True)
        self.parts.append(f"&#{name};")
        if self.last_child:
            self.last_child[-1] = "#text"

    def handle_comment(self, data: str) -> None:
        self._flush_whitespace(next_text=True)
        self.parts.append(f"<!--{data}-->")
        if self.last_child:
            self.last_child[-1] = "#text"

    def handle_decl(self, decl: str) -> None:
        self._flush_whitespace(next_text=True)
        self.parts.append(f"<!{decl}>")
        if self.last_child:
            self.last_child[-1] = "#text"

    def handle_pi(self, data: str) -> None:
        self._flush_whitespace(next_text=True)
        self.parts.append(f"<?{data}>")
        if self.last_child:
            self.last_child[-1] = "#text"

    def close(self) -> None:
        super().close()
        self._flush_whitespace()

    def _flush_whitespace(
        self,
        *,
        next_tag: str | None = None,
        next_end_tag: str | None = None,
        next_text: bool = False,
    ) -> None:
        pending = self.pending_whitespace
        if pending is None:
            return
        self.pending_whitespace = None
        data, parent, previous = pending
        next_is_block_boundary = next_tag in _BLOCK_HTML_TAGS or next_end_tag == parent
        previous_is_block_boundary = previous is None or previous in _BLOCK_HTML_TAGS
        if not (
            parent in _BLOCK_HTML_TAGS
            and (previous_is_block_boundary or next_is_block_boundary)
            and not next_text
        ):
            self.parts.append(data)

    @staticmethod
    def _start(
        tag: str,
        attrs: list[tuple[str, str | None]],
        *,
        closed: bool,
    ) -> str:
        if len({name for name, _value in attrs}) != len(attrs):
            raise ValueError("duplicate HTML attributes are not canonicalizable")
        serialized = "".join(
            f' {name}="{escape(value, quote=True)}"' if value is not None else f" {name}"
            for name, value in sorted(attrs)
        )
        ending = "/>" if closed else ">"
        return f"<{tag}{serialized}{ending}"


def _canonicalize_html(content: str) -> str:
    parser = _CanonicalHtmlParser()
    parser.feed(content)
    parser.close()
    return "".join(parser.parts)


def normalize_remote_draft(
    draft: WechatDraftArticle | dict[str, Any],
) -> dict[str, Any]:
    """Return the exact deterministic conflict fields without mutating or fetching."""
    article = WechatDraftArticle.model_validate(draft)
    return {
        "title": article.title,
        "author": article.author,
        "digest": article.digest,
        "content": _canonicalize_html(article.content),
        "thumb_media_id": article.thumb_media_id,
        "need_open_comment": article.need_open_comment,
        "only_fans_can_comment": article.only_fans_can_comment,
        "content_source_url": (
            str(article.content_source_url) if article.content_source_url is not None else None
        ),
    }


def compute_remote_hash(draft: WechatDraftArticle | dict[str, Any]) -> str:
    """Hash UTF-8 canonical JSON independently of locale or process state."""
    encoded = json.dumps(
        normalize_remote_draft(draft),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
