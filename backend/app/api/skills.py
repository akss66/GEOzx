"""User-facing Skill catalog with a strict business-only projection."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.core.auth import CurrentUser
from app.models.enums import Platform
from app.orchestrator.skills.public_catalog import (
    PUBLIC_SKILL_POLICIES as _PUBLIC_SKILL_POLICIES,
)
from app.orchestrator.skills.registry import skill_registry
from app.schemas.skills import (
    PublicSkillCatalogItem,
    PublicSkillCatalogOut,
    SkillCatalogSurface,
)

router = APIRouter(tags=["skills"])


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
