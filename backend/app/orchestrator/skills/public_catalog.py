"""Publication and availability policy for user-selectable business Skills."""

from dataclasses import dataclass

from app.models.enums import UserRole
from app.schemas.skills import PublicSkillCategory, SkillCatalogSurface


@dataclass(frozen=True)
class PublicSkillPolicy:
    code: str
    category: PublicSkillCategory
    icon: str
    requires_account: bool
    surfaces: frozenset[SkillCatalogSurface]
    enabled: bool = True
    allowed_roles: frozenset[UserRole] = frozenset({UserRole.ADMIN, UserRole.USER})
    internal_disabled_reason: str | None = None


PUBLIC_SKILL_POLICIES: dict[str, PublicSkillPolicy] = {
    "account_inspection": PublicSkillPolicy(
        code="account_inspection",
        category="quick_operations",
        icon="activity",
        requires_account=True,
        surfaces=frozenset({"composer"}),
    ),
}


__all__ = ["PUBLIC_SKILL_POLICIES", "PublicSkillPolicy"]
