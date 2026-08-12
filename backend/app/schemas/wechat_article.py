"""Fail-closed contracts for WeChat article planning and document content."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

StrictText = Annotated[str, Field(min_length=1, max_length=20_000)]
BlockId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]
SlotKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,127}$")]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArticleObjective(_StrictModel):
    kind: Literal["awareness", "education", "lead_generation", "conversion", "event"]
    description: Annotated[str, Field(min_length=1, max_length=1_000)]


class ArticleAudience(_StrictModel):
    segments: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        min_length=1, max_length=12
    )
    scenarios: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        min_length=1, max_length=12
    )


class ArticleCta(_StrictModel):
    action: Literal["consult", "contact", "visit", "register", "learn_more"]
    label: Annotated[str, Field(min_length=1, max_length=120)]
    url: AnyHttpUrl


class ArticleBrandRequirements(_StrictModel):
    tone: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        min_length=1, max_length=8
    )
    must_include: list[Annotated[str, Field(min_length=1, max_length=1_000)]] = Field(
        default_factory=list, max_length=30
    )
    forbidden_expressions: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        default_factory=list, max_length=30
    )


class ArticleBrief(_StrictModel):
    objective: ArticleObjective
    target_audience: ArticleAudience
    topic_or_product: Annotated[str, Field(min_length=1, max_length=500)]
    primary_cta: ArticleCta
    brand_requirements: ArticleBrandRequirements
    core_selling_points: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=20
    )
    target_length: int | None = Field(default=None, ge=200, le=30_000)
    reference_urls: list[AnyHttpUrl] = Field(default_factory=list, max_length=20)
    source_material_ids: list[int] = Field(default_factory=list, max_length=50)
    image_style: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    author_name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    content_source_url: AnyHttpUrl | None = None
    comment_policy: Literal["default", "open", "closed"] | None = None


class _ArticleBlock(_StrictModel):
    block_id: BlockId


class HeadingBlock(_ArticleBlock):
    type: Literal["heading"]
    level: Literal[2, 3, 4]
    text: Annotated[str, Field(min_length=1, max_length=500)]


class ParagraphBlock(_ArticleBlock):
    type: Literal["paragraph"]
    text: StrictText


class QuoteBlock(_ArticleBlock):
    type: Literal["quote"]
    text: Annotated[str, Field(min_length=1, max_length=4_000)]
    attribution: Annotated[str, Field(min_length=1, max_length=300)] | None = None


class ListBlock(_ArticleBlock):
    type: Literal["list"]
    style: Literal["ordered", "unordered"]
    items: list[Annotated[str, Field(min_length=1, max_length=2_000)]] = Field(
        min_length=1, max_length=30
    )


class CalloutBlock(_ArticleBlock):
    type: Literal["callout"]
    tone: Literal["info", "tip", "warning"]
    text: Annotated[str, Field(min_length=1, max_length=4_000)]


class ImageSlotBlock(_ArticleBlock):
    type: Literal["imageSlot"]
    slot_key: SlotKey


class DividerBlock(_ArticleBlock):
    type: Literal["divider"]


class CtaBlock(_ArticleBlock):
    type: Literal["cta"]
    label: Annotated[str, Field(min_length=1, max_length=120)]
    action: Literal["consult", "contact", "visit", "register", "learn_more"]
    url: AnyHttpUrl


ArticleBlock = Annotated[
    HeadingBlock
    | ParagraphBlock
    | QuoteBlock
    | ListBlock
    | CalloutBlock
    | ImageSlotBlock
    | DividerBlock
    | CtaBlock,
    Field(discriminator="type"),
]


class ArticleDocument(_StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=64)]
    digest: Annotated[str, Field(min_length=1, max_length=120)]
    author: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    blocks: list[ArticleBlock] = Field(min_length=1, max_length=500)

    @field_validator("title", "digest", "author", mode="after")
    @classmethod
    def _reject_html(cls, value: str | None) -> str | None:
        if value is not None and ("<" in value or ">" in value):
            raise ValueError("HTML is not permitted in article documents")
        return value

    @model_validator(mode="after")
    def _validate_document_invariants(self) -> ArticleDocument:
        block_ids = [block.block_id for block in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("article block_id values must be unique")
        for block in self.blocks:
            for value in _block_text_values(block):
                if "<" in value or ">" in value:
                    raise ValueError("HTML is not permitted in article documents")
        return self


def _block_text_values(block: ArticleBlock) -> tuple[str, ...]:
    if isinstance(block, ListBlock):
        return tuple(block.items)
    return tuple(
        value
        for value in (
            getattr(block, "text", None),
            getattr(block, "attribution", None),
            getattr(block, "label", None),
        )
        if value is not None
    )
