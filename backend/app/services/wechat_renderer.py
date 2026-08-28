"""Deterministic WeChat HTML rendering from the strict ArticleDocument AST."""

from __future__ import annotations

from hashlib import sha256
from html import escape
from urllib.parse import urlsplit

from app.schemas.wechat_article import (
    ArticleDocument,
    CalloutBlock,
    CtaBlock,
    DividerBlock,
    HeadingBlock,
    ImageSlotBlock,
    ListBlock,
    ParagraphBlock,
    QuoteBlock,
)

MAX_TITLE_CODE_POINTS = 32
MAX_AUTHOR_CODE_POINTS = 16
MAX_DIGEST_CODE_POINTS = 120
MAX_CONTENT_CHARACTERS = 20_000
MAX_CONTENT_UTF8_BYTES = 1_048_576

ALLOWED_ELEMENTS = frozenset(
    {
        "section",
        "h2",
        "h3",
        "h4",
        "p",
        "blockquote",
        "cite",
        "ol",
        "ul",
        "li",
        "aside",
        "img",
        "hr",
        "a",
    }
)
ALLOWED_ATTRIBUTES = {
    "section": frozenset(),
    "h2": frozenset(),
    "h3": frozenset(),
    "h4": frozenset(),
    "p": frozenset(),
    "blockquote": frozenset(),
    "cite": frozenset(),
    "ol": frozenset(),
    "ul": frozenset(),
    "li": frozenset(),
    "aside": frozenset({"style"}),
    "img": frozenset({"src", "alt"}),
    "hr": frozenset(),
    "a": frozenset({"href"}),
}
ALLOWED_STYLES = frozenset({"border-left", "padding-left"})
ALLOWED_URL_SCHEMES = frozenset({"https"})
ALLOWED_IMAGE_HOSTS = frozenset({"mmbiz.qpic.cn"})


class WechatRenderError(ValueError):
    """The structured document cannot be rendered within the safe WeChat contract."""


class RenderedWechatArticle:
    """Stable rendering result; ``html`` is retained as a convenient read alias."""

    __slots__ = ("normalized_html", "content_hash")

    def __init__(self, normalized_html: str, content_hash: str) -> None:
        self.normalized_html = normalized_html
        self.content_hash = content_hash

    @property
    def html(self) -> str:
        return self.normalized_html

    def __eq__(self, other: object) -> bool:
        return isinstance(other, RenderedWechatArticle) and (
            self.normalized_html,
            self.content_hash,
        ) == (other.normalized_html, other.content_hash)


def render_wechat_article(
    document: ArticleDocument | dict, asset_map: dict[str, str]
) -> RenderedWechatArticle:
    """Render only allowlisted AST blocks; raw/provider HTML is never an input contract."""
    article = ArticleDocument.model_validate(document)
    _validate_metadata(article)
    normalized_html = _render_blocks(article, asset_map)
    if len(normalized_html) >= MAX_CONTENT_CHARACTERS:
        raise WechatRenderError("rendered WeChat content must contain fewer than 20,000 characters")
    if len(normalized_html.encode("utf-8")) >= MAX_CONTENT_UTF8_BYTES:
        raise WechatRenderError("rendered WeChat content must be smaller than 1 MiB")
    return RenderedWechatArticle(
        normalized_html=normalized_html,
        content_hash=sha256(normalized_html.encode("utf-8")).hexdigest(),
    )


def _validate_metadata(document: ArticleDocument) -> None:
    if len(document.title) > MAX_TITLE_CODE_POINTS:
        raise WechatRenderError("WeChat title exceeds 32 Unicode code points")
    if document.author is not None and len(document.author) > MAX_AUTHOR_CODE_POINTS:
        raise WechatRenderError("WeChat author exceeds 16 Unicode code points")
    if len(document.digest) > MAX_DIGEST_CODE_POINTS:
        raise WechatRenderError("WeChat digest exceeds 120 Unicode code points")


def _render_blocks(document: ArticleDocument, asset_map: dict[str, str]) -> str:
    required_slots = {
        block.slot_key for block in document.blocks if isinstance(block, ImageSlotBlock)
    }
    if set(asset_map) != required_slots:
        raise WechatRenderError("asset_map must resolve exactly the article image slots")
    rendered: list[str] = []
    for block in document.blocks:
        if isinstance(block, HeadingBlock):
            rendered.append(_element(f"h{block.level}", text=block.text))
        elif isinstance(block, ParagraphBlock):
            rendered.append(_element("p", text=block.text))
        elif isinstance(block, QuoteBlock):
            body = escape(block.text, quote=True)
            if block.attribution is not None:
                body += _element("cite", text=block.attribution)
            rendered.append(_element("blockquote", content=body))
        elif isinstance(block, ListBlock):
            tag = "ol" if block.style == "ordered" else "ul"
            rendered.append(
                _element(tag, content="".join(_element("li", text=item) for item in block.items))
            )
        elif isinstance(block, CalloutBlock):
            rendered.append(
                _element(
                    "aside",
                    text=block.text,
                    attributes={"style": "border-left:4px solid #d9d9d9;padding-left:12px"},
                )
            )
        elif isinstance(block, ImageSlotBlock):
            source = asset_map[block.slot_key]
            _require_wechat_image_url(source)
            rendered.append(_element("img", attributes={"src": source, "alt": ""}, void=True))
        elif isinstance(block, DividerBlock):
            rendered.append(_element("hr", void=True))
        elif isinstance(block, CtaBlock):
            href = str(block.url)
            _require_https_url(href)
            rendered.append(_element("a", text=block.label, attributes={"href": href}))
        else:  # pragma: no cover - ArticleDocument's discriminated union is exhaustive
            raise WechatRenderError("unsupported article block")
    return "".join(rendered)


def _element(
    tag: str,
    *,
    text: str | None = None,
    content: str = "",
    attributes: dict[str, str] | None = None,
    void: bool = False,
) -> str:
    if tag not in ALLOWED_ELEMENTS:
        raise WechatRenderError("element is not allowlisted")
    attributes = attributes or {}
    if not set(attributes) <= ALLOWED_ATTRIBUTES[tag]:
        raise WechatRenderError("attribute is not allowlisted")
    if "style" in attributes:
        declarations = attributes["style"].split(";")
        properties = {item.split(":", 1)[0] for item in declarations if item}
        if not properties <= ALLOWED_STYLES:
            raise WechatRenderError("style property is not allowlisted")
    serialized = "".join(
        f' {name}="{escape(value, quote=True)}"' for name, value in sorted(attributes.items())
    )
    if void:
        return f"<{tag}{serialized}>"
    safe_content = escape(text, quote=True) if text is not None else content
    return f"<{tag}{serialized}>{safe_content}</{tag}>"


def _require_https_url(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ALLOWED_URL_SCHEMES
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise WechatRenderError("only absolute HTTPS URLs are permitted")


def _require_wechat_image_url(value: str) -> None:
    _require_https_url(value)
    if (urlsplit(value).hostname or "").lower() not in ALLOWED_IMAGE_HOSTS:
        raise WechatRenderError("image URL is not a trusted WeChat material URL")
