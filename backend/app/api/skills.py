"""User-facing Skill catalog with a strict business-only projection."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.auth import CurrentUser
from app.models.enums import Platform, UserRole
from app.orchestrator.skills.registry import skill_registry
from app.schemas.skills import (
    PublicSkillCatalogItem,
    PublicSkillCatalogOut,
    PublicSkillCategory,
    SkillCatalogSurface,
)

router = APIRouter(tags=["skills"])


@dataclass(frozen=True)
class _PublicSkillPolicy:
    code: str
    category: PublicSkillCategory
    icon: str
    requires_account: bool
    surfaces: frozenset[SkillCatalogSurface]
    enabled: bool = True
    allowed_roles: frozenset[UserRole] = frozenset({UserRole.ADMIN, UserRole.USER})
    internal_disabled_reason: str | None = None


_PUBLIC_SKILL_POLICIES: dict[str, _PublicSkillPolicy] = {
    "account_inspection": _PublicSkillPolicy(
        code="account_inspection",
        category="quick_operations",
        icon="activity",
        requires_account=True,
        surfaces=frozenset({"composer"}),
    ),
}


@router.get("/skills", response_model=PublicSkillCatalogOut)
async def list_public_skills(
    user: CurrentUser,
    platform: Platform,
    surface: Annotated[SkillCatalogSurface, Query()],
) -> PublicSkillCatalogOut:
    """Return only explicitly published, compatible business capabilities."""
    data: list[PublicSkillCatalogItem] = []
    for policy in sorted(_PUBLIC_SKILL_POLICIES.values(), key=lambda item: item.code):
        if surface not in policy.surfaces:
            continue
        try:
            definition = skill_registry.get(policy.code)
        except KeyError:
            continue
        if platform.value not in definition.supported_platforms:
            continue
        is_available = policy.enabled and user.role in policy.allowed_roles
        data.append(
            PublicSkillCatalogItem(
                code=definition.code,
                version=definition.version,
                name=definition.name,
                description=definition.description,
                category=policy.category,
                icon=policy.icon,
                requires_account=policy.requires_account,
                is_available=is_available,
                unavailable_reason=None if is_available else "暂不可用",
            )
        )
    return PublicSkillCatalogOut(data=data)
