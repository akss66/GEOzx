"""Administrator-only model provider governance APIs."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser
from app.db import get_session
from app.schemas.configuration import (
    CreateModelProviderRequest,
    ModelProviderDetailOut,
    ModelProviderDiscoveryOut,
    ModelProviderTemplateOut,
    ModelProviderVerifyOut,
    PatchModelProviderRequest,
    PutModelProviderCredentialRequest,
    PutModelProviderModelsRequest,
)
from app.services.model_provider_registry import (
    ProviderDeleteConflictError,
    ProviderModelCatalogConflictError,
    create_model_provider,
    delete_model_provider,
    delete_model_provider_credential,
    discover_model_provider_models,
    get_model_provider_detail,
    list_model_providers,
    list_provider_templates,
    patch_model_provider,
    put_model_provider_credential,
    put_model_provider_models,
    verify_model_provider,
)

router = APIRouter(prefix="/model-providers", tags=["model-providers"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/templates", response_model=list[ModelProviderTemplateOut])
async def get_provider_templates(admin: AdminUser) -> list[ModelProviderTemplateOut]:
    return [ModelProviderTemplateOut.model_validate(row) for row in list_provider_templates()]


@router.get("", response_model=list[ModelProviderDetailOut])
async def get_model_providers(
    admin: AdminUser,
    session: SessionDep,
) -> list[ModelProviderDetailOut]:
    return [
        ModelProviderDetailOut.model_validate(row)
        for row in await list_model_providers(session, org_id=admin.org_id)
    ]


@router.post(
    "",
    response_model=ModelProviderDetailOut,
    status_code=status.HTTP_201_CREATED,
)
async def post_model_provider(
    body: CreateModelProviderRequest,
    admin: AdminUser,
    session: SessionDep,
) -> ModelProviderDetailOut:
    return ModelProviderDetailOut.model_validate(
        await create_model_provider(
            session,
            org_id=admin.org_id,
            user_id=admin.id,
            template_code=body.template_code,
            provider_type=body.provider_type,
            code=body.code,
            display_name=body.display_name,
            base_url=body.base_url,
            enabled=body.enabled,
        )
    )


@router.get("/{provider_id}", response_model=ModelProviderDetailOut)
async def get_model_provider(
    provider_id: int,
    admin: AdminUser,
    session: SessionDep,
) -> ModelProviderDetailOut:
    return ModelProviderDetailOut.model_validate(
        await get_model_provider_detail(session, org_id=admin.org_id, provider_id=provider_id)
    )


@router.patch("/{provider_id}", response_model=ModelProviderDetailOut)
async def patch_provider(
    provider_id: int,
    body: PatchModelProviderRequest,
    admin: AdminUser,
    session: SessionDep,
) -> ModelProviderDetailOut:
    return ModelProviderDetailOut.model_validate(
        await patch_model_provider(
            session,
            org_id=admin.org_id,
            user_id=admin.id,
            provider_id=provider_id,
            display_name=body.display_name,
            base_url=body.base_url,
            enabled=body.enabled,
        )
    )


@router.put("/{provider_id}/credential", response_model=ModelProviderDetailOut)
async def put_provider_credential(
    provider_id: int,
    body: PutModelProviderCredentialRequest,
    admin: AdminUser,
    session: SessionDep,
) -> ModelProviderDetailOut:
    return ModelProviderDetailOut.model_validate(
        await put_model_provider_credential(
            session,
            org_id=admin.org_id,
            user_id=admin.id,
            provider_id=provider_id,
            api_key=body.api_key,
        )
    )


@router.delete("/{provider_id}/credential", response_model=ModelProviderDetailOut)
async def delete_provider_credential(
    provider_id: int,
    admin: AdminUser,
    session: SessionDep,
) -> ModelProviderDetailOut:
    return ModelProviderDetailOut.model_validate(
        await delete_model_provider_credential(
            session,
            org_id=admin.org_id,
            user_id=admin.id,
            provider_id=provider_id,
        )
    )


@router.post("/{provider_id}/verify", response_model=ModelProviderVerifyOut)
async def post_provider_verify(
    provider_id: int,
    admin: AdminUser,
    session: SessionDep,
) -> ModelProviderVerifyOut:
    return ModelProviderVerifyOut.model_validate(
        await verify_model_provider(
            session,
            org_id=admin.org_id,
            user_id=admin.id,
            provider_id=provider_id,
        )
    )


@router.post("/{provider_id}/discover-models", response_model=ModelProviderDiscoveryOut)
async def post_provider_discover_models(
    provider_id: int,
    admin: AdminUser,
    session: SessionDep,
) -> ModelProviderDiscoveryOut | JSONResponse:
    try:
        return ModelProviderDiscoveryOut.model_validate(
            await discover_model_provider_models(
                session,
                org_id=admin.org_id,
                user_id=admin.id,
                provider_id=provider_id,
            )
        )
    except ProviderModelCatalogConflictError as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "affected_agents": exc.affected_agents,
                "missing_models": exc.missing_models,
            },
        )


@router.put("/{provider_id}/models", response_model=ModelProviderDetailOut)
async def put_provider_models(
    provider_id: int,
    body: PutModelProviderModelsRequest,
    admin: AdminUser,
    session: SessionDep,
) -> ModelProviderDetailOut | JSONResponse:
    try:
        return ModelProviderDetailOut.model_validate(
            await put_model_provider_models(
                session,
                org_id=admin.org_id,
                user_id=admin.id,
                provider_id=provider_id,
                models=body.models,
            )
        )
    except ProviderModelCatalogConflictError as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "affected_agents": exc.affected_agents,
                "missing_models": exc.missing_models,
            },
        )


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: int,
    admin: AdminUser,
    session: SessionDep,
) -> Response:
    try:
        await delete_model_provider(
            session,
            org_id=admin.org_id,
            user_id=admin.id,
            provider_id=provider_id,
        )
    except ProviderDeleteConflictError as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"affected_agents": exc.affected_agents},
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
