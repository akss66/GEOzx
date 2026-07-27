"""Durable official publishing APIs and public Douyin webhook boundary."""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.db import get_session
from app.schemas.publishing import (
    CreatePublishJobRequest,
    DouyinCreateVideoCallback,
    PublishHandoffOut,
    PublishJobOut,
)
from app.services.publishing import (
    PublishingServiceError,
    cancel_publish_job,
    create_publish_job,
    get_publish_job,
    ingest_douyin_create_video_callback,
    list_publish_jobs,
    mark_publish_job_launched,
    prepare_douyin_handoff,
    retry_publish_job,
)

router = APIRouter(tags=["publishing"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _service_error(error: PublishingServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={
            "error": {
                "code": error.code,
                "message": error.message,
                "retryable": error.retryable,
                "details": error.details,
            }
        },
    )


@router.post(
    "/publishing/jobs",
    response_model=PublishJobOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_job(
    body: CreatePublishJobRequest,
    user: CurrentUser,
    session: SessionDep,
):
    try:
        return await create_publish_job(session, user, body)
    except PublishingServiceError as exc:
        return _service_error(exc)


@router.get("/publishing/jobs", response_model=list[PublishJobOut])
async def list_jobs(
    user: CurrentUser,
    session: SessionDep,
    account_id: Annotated[int | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    try:
        return await list_publish_jobs(
            session,
            user,
            account_id=account_id,
            limit=limit,
        )
    except PublishingServiceError as exc:
        return _service_error(exc)


@router.get("/publishing/jobs/{job_id}", response_model=PublishJobOut)
async def get_job(job_id: int, user: CurrentUser, session: SessionDep):
    try:
        return await get_publish_job(session, user, job_id)
    except PublishingServiceError as exc:
        return _service_error(exc)


@router.post(
    "/publishing/jobs/{job_id}/handoff",
    response_model=PublishHandoffOut,
)
async def create_handoff(job_id: int, user: CurrentUser, session: SessionDep):
    try:
        return await prepare_douyin_handoff(session, user, job_id)
    except PublishingServiceError as exc:
        return _service_error(exc)


@router.post(
    "/publishing/jobs/{job_id}/launched",
    response_model=PublishJobOut,
)
async def mark_handoff_launched(
    job_id: int,
    user: CurrentUser,
    session: SessionDep,
):
    try:
        return await mark_publish_job_launched(session, user, job_id)
    except PublishingServiceError as exc:
        return _service_error(exc)


@router.post(
    "/publishing/jobs/{job_id}/retry",
    response_model=PublishJobOut,
)
async def retry_job(job_id: int, user: CurrentUser, session: SessionDep):
    try:
        return await retry_publish_job(session, user, job_id)
    except PublishingServiceError as exc:
        return _service_error(exc)


@router.post(
    "/publishing/jobs/{job_id}/cancel",
    response_model=PublishJobOut,
)
async def cancel_job(job_id: int, user: CurrentUser, session: SessionDep):
    try:
        return await cancel_publish_job(session, user, job_id)
    except PublishingServiceError as exc:
        return _service_error(exc)


@router.post("/platform-integrations/douyin/webhooks")
async def receive_douyin_webhook(
    session: SessionDep,
    body: Annotated[dict[str, Any], Body()],
):
    challenge = body.get("challenge")
    if isinstance(challenge, str) and challenge:
        return {"challenge": challenge}
    try:
        callback = DouyinCreateVideoCallback.model_validate(body)
    except ValidationError as exc:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": {
                    "code": "DOUYIN_WEBHOOK_INVALID",
                    "message": "抖音回调格式无效。",
                    "retryable": False,
                    "details": {"fields": [list(error["loc"]) for error in exc.errors()]},
                }
            },
        )
    try:
        job = await ingest_douyin_create_video_callback(session, callback)
        return PublishJobOut.model_validate(job).model_dump(mode="json")
    except PublishingServiceError as exc:
        return _service_error(exc)
