"""Strict WeChat official-account article production Skill contract."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.schemas.skills import SkillDefinition
from app.schemas.wechat_article import ArticleBrief


class WechatArticleProductionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brief: ArticleBrief | None = None
    pending_brief: dict[str, Any] | None = None
    working_copy_id: int | None = Field(default=None, gt=0)
    requested_action: Literal["produce", "generate_images", "sync_draft"] = "produce"
    article_version_id: int | None = Field(default=None, gt=0)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=160)
    sync_confirmed: bool = False

    @model_validator(mode="before")
    @classmethod
    def _preserve_incomplete_brief_for_clarification(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        if value.get("pending_brief") is not None and value.get("brief") is not None:
            raise ValueError("pending_brief is internal runtime state")
        raw_brief = value.get("brief")
        if not isinstance(raw_brief, dict):
            return value
        try:
            ArticleBrief.model_validate(raw_brief)
        except ValidationError as exc:
            if any(error["type"] != "missing" for error in exc.errors()):
                raise
            return {**value, "brief": None, "pending_brief": raw_brief}
        return value


def resolve_missing_primary_cta(
    frozen_input: dict[str, Any],
    resolution: dict[str, Any],
) -> WechatArticleProductionInput | None:
    """Resolve only the server-frozen missing CTA clarification transition."""

    input_payload = {
        key: value
        for key, value in frozen_input.items()
        if key in WechatArticleProductionInput.model_fields
    }
    parsed = WechatArticleProductionInput.model_validate(input_payload)
    if (
        parsed.requested_action != "produce"
        or parsed.brief is not None
        or parsed.pending_brief is None
        or set(resolution) != {"primary_cta"}
    ):
        return None
    brief = ArticleBrief.model_validate(
        {**parsed.pending_brief, "primary_cta": resolution["primary_cta"]}
    )
    return WechatArticleProductionInput.model_validate(
        {
            **parsed.model_dump(mode="json", exclude={"pending_brief", "brief"}),
            "brief": brief.model_dump(mode="json"),
        }
    )


class WechatArticleProductionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    article_id: int | None = Field(default=None, gt=0)
    current_immutable_version: int | None = Field(default=None, ge=1)
    image_slot_summary: list[dict[str, Any]] = Field(default_factory=list)
    citation_summary: dict[str, Any] = Field(default_factory=dict)
    readiness: dict[str, Any] = Field(default_factory=dict)
    explicit_user_decisions: list[dict[str, Any]] = Field(default_factory=list)


class WechatArticleImageSlotPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stable_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,127}$")
    purpose: str = Field(min_length=1, max_length=300)
    placement_after_block_id: str | None = Field(default=None, max_length=128)
    aspect_ratio: str = Field(min_length=1, max_length=32)
    visual_brief: str = Field(min_length=1, max_length=20_000)
    prompt_internal: str | None = Field(default=None, max_length=20_000)


WECHAT_ARTICLE_PRODUCTION_SKILL = SkillDefinition(
    code="wechat_article_production",
    version=1,
    name="微信公众号文章制作",
    description="由内容、编辑和美术专家制作可追溯的微信公众号文章，并在外部写入前单独确认。",
    supported_platforms=frozenset({"wechat_official_account"}),
    input_model=WechatArticleProductionInput,
    output_model=WechatArticleProductionOutput,
    expert_codes=("02-content-director", "05-editor", "03-art-director"),
    expert_stages=(
        ("02-content-director",),
        ("05-editor",),
        ("03-art-director",),
    ),
    tool_codes=(),
    critic_policy="none",
    risk_level="high",
    approval_policy="explicit_before_external_write",
    artifact_type="wechat_article",
)


__all__ = [
    "WECHAT_ARTICLE_PRODUCTION_SKILL",
    "WechatArticleProductionInput",
    "WechatArticleProductionOutput",
    "WechatArticleImageSlotPlan",
    "resolve_missing_primary_cta",
]
