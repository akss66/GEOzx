"""User-facing Skill catalog with a strict business-only projection."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.core.workspace_access import require_account_access
from app.db import get_session
from app.models.enums import Platform
from app.orchestrator.capability_registry import resolve_capability_availability
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
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/skills", response_model=PublicSkillCatalogOut)
async def list_public_skills(
    user: CurrentUser,
    session: SessionDep,
    platform: Platform,
    surface: Annotated[SkillCatalogSurface, Query()],
    account_id: Annotated[int | None, Query(gt=0)] = None,
) -> PublicSkillCatalogOut:
    """Return only explicitly published, compatible business capabilities."""
    account = (
        await require_account_access(session, user, account_id)
        if account_id is not None
        else None
    )
    if account is not None and account.platform != platform:
        account = None
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
        availability, reason = resolve_capability_availability(
            enabled=policy.enabled,
            role_allowed=user.role in policy.allowed_roles,
            required_context=policy.required_context,
            account=account,
        )
        is_available = availability == "available"
        data.append(
            PublicSkillCatalogItem(
                code=definition.code,
                version=definition.version,
                name=definition.name,
                description=definition.description,
                category=policy.category,
                icon=policy.icon,
                requires_account=policy.requires_account,
                availability=availability,
                reason=reason,
                required_context=list(policy.required_context),
                is_available=is_available,
                unavailable_reason=reason,
            )
        )
    return PublicSkillCatalogOut(data=data)
