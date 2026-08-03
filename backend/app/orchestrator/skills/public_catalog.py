"""Publication and availability policy for user-selectable business Skills."""

from dataclasses import dataclass

from app.models.enums import UserRole
from app.schemas.skills import (
    CapabilityRequiredContext,
    PublicSkillCategory,
    SkillCatalogSurface,
)


@dataclass(frozen=True)
class PublicSkillPolicy:
    code: str
    category: PublicSkillCategory
    icon: str
    requires_account: bool
    surfaces: frozenset[SkillCatalogSurface]
    required_context: tuple[CapabilityRequiredContext, ...] = ("account",)
    enabled: bool = True
    allowed_roles: frozenset[UserRole] = frozenset({UserRole.ADMIN, UserRole.USER})
    internal_disabled_reason: str | None = None


PUBLIC_SKILL_POLICIES: dict[str, PublicSkillPolicy] = {
    "account_positioning": PublicSkillPolicy(
        code="account_positioning",
        category="quick_operations",
        icon="compass",
        requires_account=True,
        surfaces=frozenset({"composer"}),
    ),
    "visual_brief_generation": PublicSkillPolicy(
        code="visual_brief_generation",
        category="quick_operations",
        icon="picture",
        requires_account=True,
        surfaces=frozenset({"composer"}),
        required_context=("account", "confirmed_artifact"),
    ),
    "content_calendar_planning": PublicSkillPolicy(
        code="content_calendar_planning",
        category="quick_operations",
        icon="calendar",
        requires_account=True,
        surfaces=frozenset({"composer"}),
        required_context=("account", "confirmed_artifact"),
    ),
    "content_publishing": PublicSkillPolicy(
        code="content_publishing",
        category="quick_operations",
        icon="send",
        requires_account=True,
        surfaces=frozenset({"composer"}),
        required_context=("account", "confirmed_artifact", "platform_connection"),
    ),
    "engagement_review": PublicSkillPolicy(
        code="engagement_review",
        category="quick_operations",
        icon="message-circle",
        requires_account=True,
        surfaces=frozenset({"composer"}),
        required_context=("account", "account_data"),
    ),
    "operation_iteration": PublicSkillPolicy(
        code="operation_iteration",
        category="quick_operations",
        icon="repeat",
        requires_account=True,
        surfaces=frozenset({"composer", "artifact_center"}),
        required_context=("account", "confirmed_artifact"),
    ),
    "account_inspection": PublicSkillPolicy(
        code="account_inspection",
        category="quick_operations",
        icon="activity",
        requires_account=True,
        surfaces=frozenset({"composer"}),
    ),
    "topic_planning": PublicSkillPolicy(
        code="topic_planning",
        category="quick_operations",
        icon="calendar",
        requires_account=True,
        surfaces=frozenset({"composer"}),
    ),
    "script_generation": PublicSkillPolicy(
        code="script_generation",
        category="quick_operations",
        icon="file-text",
        requires_account=True,
        surfaces=frozenset({"composer"}),
    ),
    "publishing_preparation": PublicSkillPolicy(
        code="publishing_preparation",
        category="quick_operations",
        icon="send",
        requires_account=True,
        surfaces=frozenset({"composer"}),
    ),
    "performance_review": PublicSkillPolicy(
        code="performance_review",
        category="quick_operations",
        icon="line-chart",
        requires_account=True,
        surfaces=frozenset({"composer"}),
    ),
}


__all__ = ["PUBLIC_SKILL_POLICIES", "PublicSkillPolicy"]
