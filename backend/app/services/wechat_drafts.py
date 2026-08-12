"""Typed server-side boundary for WeChat article assets and drafts."""

from __future__ import annotations

import hashlib
import json
import re
from html import escape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from app.schemas.wechat_article import WechatDraftArticle, WechatRemoteDraft
from app.services.wechat_component import WechatIntegrationError

WECHAT_API_BASE_URL = "https://api.weixin.qq.com"
DRAFT_ADD_ENDPOINT = "/cgi-bin/draft/add"
DRAFT_GET_ENDPOINT = "/cgi-bin/draft/get"
DRAFT_UPDATE_ENDPOINT = "/cgi-bin/draft/update"
UPLOAD_ARTICLE_IMAGE_ENDPOINT = "/cgi-bin/media/uploadimg"
ADD_PERMANENT_MATERIAL_ENDPOINT = "/cgi-bin/material/add_material"
_RETRYABLE_WECHAT_CODES = frozenset({-1, 45009})
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
        response = await self._post(
            DRAFT_ADD_ENDPOINT,
            access_token=access_token,
            json={"articles": [article.model_dump(mode="json", exclude_none=True)]},
        )
        payload = _decode_response(response, endpoint=DRAFT_ADD_ENDPOINT)
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
        response = await self._request(
            UPLOAD_ARTICLE_IMAGE_ENDPOINT,
            access_token=access_token,
            files={"media": (filename, content, media_type)},
        )
        payload = _decode_response(response, endpoint=UPLOAD_ARTICLE_IMAGE_ENDPOINT)
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
        response = await self._request(
            ADD_PERMANENT_MATERIAL_ENDPOINT,
            access_token=access_token,
            params={"type": "image"},
            files={"media": (filename, content, media_type)},
        )
        payload = _decode_response(response, endpoint=ADD_PERMANENT_MATERIAL_ENDPOINT)
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
        response = await self._post(
            DRAFT_GET_ENDPOINT,
            access_token=access_token,
            json={"media_id": media_id},
        )
        payload = _decode_response(response, endpoint=DRAFT_GET_ENDPOINT)
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
        response = await self._post(
            DRAFT_UPDATE_ENDPOINT,
            access_token=access_token,
            json={
                "media_id": media_id,
                "index": index,
                "articles": article.model_dump(mode="json", exclude_none=True),
            },
        )
        payload = _decode_response(response, endpoint=DRAFT_UPDATE_ENDPOINT)
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

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.parts.append(self._start(tag, attrs, closed=False))
        if tag not in {"img", "hr", "br"}:
            self.stack.append(tag)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.parts.append(self._start(tag, attrs, closed=True))

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")
        if tag in self.stack:
            index = len(self.stack) - 1 - self.stack[::-1].index(tag)
            del self.stack[index:]

    def handle_data(self, data: str) -> None:
        is_formatting_whitespace = not data.strip() and any(
            character in data for character in "\r\n\t"
        )
        if data.strip() or (self.stack and not is_formatting_whitespace):
            self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")

    def handle_pi(self, data: str) -> None:
        self.parts.append(f"<?{data}>")

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
