"""Account Artifact Center and artifact action endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.db import get_session
from app.schemas.artifacts import (
    ArtifactAcceptanceRequest,
    ArtifactOut,
    ArtifactPageOut,
    ArtifactRevisionRequest,
    ArtifactStatus,
)
from app.services.artifacts import (
    accept_artifact,
    create_artifact_revision,
    get_artifact_out,
    list_artifacts,
)

router = APIRouter(tags=["artifacts"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/artifacts", response_model=ArtifactPageOut)
async def artifact_center(
    user: CurrentUser,
    session: SessionDep,
    account_id: Annotated[int, Query(gt=0)],
    artifact_type: Annotated[str | None, Query(max_length=120)] = None,
    artifact_status: Annotated[ArtifactStatus | None, Query(alias="status")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ArtifactPageOut:
    return await list_artifacts(
        session,
        user,
        account_id=account_id,
        artifact_type=artifact_type,
        artifact_status=artifact_status,
        page=page,
        page_size=page_size,
    )


@router.get("/artifacts/{artifact_id}", response_model=ArtifactOut)
async def artifact_detail(
    artifact_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> ArtifactOut:
    return await get_artifact_out(session, user, artifact_id)


@router.post(
    "/artifact-revisions",
    response_model=ArtifactOut,
    status_code=status.HTTP_201_CREATED,
)
async def revise_artifact(
    body: ArtifactRevisionRequest,
    user: CurrentUser,
    session: SessionDep,
) -> ArtifactOut:
    return await create_artifact_revision(
        session,
        user,
        artifact_id=body.artifact_id,
        payload=body.payload,
        note=body.note,
    )


@router.post("/artifact-acceptances", response_model=ArtifactOut)
async def accept_artifact_version(
    body: ArtifactAcceptanceRequest,
    user: CurrentUser,
    session: SessionDep,
) -> ArtifactOut:
    return await accept_artifact(session, user, artifact_id=body.artifact_id)
