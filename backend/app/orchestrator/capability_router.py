"""Deterministic routing for an explicitly selected business Skill."""

from __future__ import annotations

from app.orchestrator.skills.registry import SkillRegistry
from app.schemas.conversation import TurnExecutionMode, TurnRouteDecision


class SkillUnavailable(ValueError):
    """A requested Skill cannot be executed in the current route context."""

    def __init__(self, *, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(reason)


def route_explicit_request(
    requested_skill_code: str | None,
    *,
    platform: str,
    registry: SkillRegistry,
    has_account: bool,
) -> TurnRouteDecision | None:
    """Route an explicit Skill selection or defer classification when absent."""

    if requested_skill_code is None:
        return None

    try:
        skill = registry.get(requested_skill_code)
    except KeyError as error:
        raise SkillUnavailable(
            code="unknown_skill",
            reason="requested_skill_not_registered",
        ) from error

    if platform not in skill.supported_platforms:
        raise SkillUnavailable(
            code="unsupported_platform",
            reason="requested_skill_platform_incompatible",
        )

    if not has_account:
        return TurnRouteDecision(
            mode=TurnExecutionMode.CLARIFY,
            intent="explicit_skill",
            confidence=1,
            reason="explicit_skill_requires_account_context",
            skill_code=requested_skill_code,
            requires_account_context=True,
            requires_operation_task=False,
            missing_field="account_id",
            clarifying_question="请先选择需要操作的账号。",
        )

    return TurnRouteDecision(
        mode=TurnExecutionMode.SKILL,
        intent="explicit_skill",
        confidence=1,
        reason="explicit_skill_request",
        skill_code=requested_skill_code,
        requires_account_context=True,
        requires_operation_task=True,
    )
