"""Account-scoped HTTP endpoints for structured WeChat article working copies."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.db import get_session
from app.models import Deliverable
from app.models.enums import DeliverableType
from app.schemas.wechat_article import ArticleDocument
from app.services.wechat_articles import (
    ARTICLE_VERSION_AGENT_CODE,
    ArticleFreezeConflict,
    ArticleVersionConflict,
    _load_article_for_user,
    create_article,
    diff_versions,
    freeze_article_version,
    update_working_copy,
)

router = APIRouter(prefix="/wechat-articles", tags=["wechat-articles"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class WorkingCopyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_lock_version: int = Field(ge=1)
    document: ArticleDocument


class ArticleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: int = Field(gt=0)
    document: ArticleDocument


def _working_copy_response(working_copy) -> dict:
    return {
        "articleId": working_copy.content_item_id,
        "document": working_copy.document,
        "lockVersion": working_copy.lock_version,
        "basedOnDeliverableId": working_copy.based_on_deliverable_id,
    }


def _deliverable_response(deliverable) -> dict:
    return {
        "id": deliverable.id,
        "articleId": deliverable.content_item_id,
        "version": deliverable.version,
        "document": deliverable.payload["document"],
        "trigger": (deliverable.note or "").removeprefix("article_version:"),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_wechat_article(
    body: ArticleCreateRequest, user: CurrentUser, session: SessionDep
) -> dict:
    result = await create_article(session, user, account_id=body.account_id, document=body.document)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="article_not_found")
    content_item, working_copy, first_version = result
    return {
        **_working_copy_response(working_copy),
        "articleId": content_item.id,
        "firstVersion": _deliverable_response(first_version),
    }


@router.patch("/{article_id}/working-copy", response_model=None)
async def autosave_working_copy(
    article_id: int,
    body: WorkingCopyUpdateRequest,
    user: CurrentUser,
    session: SessionDep,
) -> dict | JSONResponse:
    try:
        working_copy = await update_working_copy(
            session,
            user,
            content_item_id=article_id,
            expected_lock_version=body.expected_lock_version,
            document=body.document,
        )
    except ArticleVersionConflict as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "error": {
                    "code": "ARTICLE_VERSION_CONFLICT",
                    "details": {"currentLockVersion": exc.current_lock_version},
                }
            },
        )
    if working_copy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="article_not_found")
    return _working_copy_response(working_copy)


@router.get("/{article_id}/working-copy")
async def get_working_copy(article_id: int, user: CurrentUser, session: SessionDep) -> dict:
    article = await _load_article_for_user(session, user, article_id)
    if article is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="article_not_found")
    return _working_copy_response(article[0])


@router.post("/{article_id}/versions", status_code=status.HTTP_201_CREATED, response_model=None)
async def save_article_version(
    article_id: int, user: CurrentUser, session: SessionDep
) -> dict | JSONResponse:
    try:
        deliverable = await freeze_article_version(
            session, user, content_item_id=article_id, trigger="explicit_save_version"
        )
    except ArticleFreezeConflict as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "error": {
                    "code": "ARTICLE_VERSION_CONFLICT",
                    "details": {"currentVersion": exc.current_version},
                }
            },
        )
    if deliverable is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="article_not_found")
    return _deliverable_response(deliverable)


@router.get("/{article_id}/versions")
async def list_article_versions(
    article_id: int, user: CurrentUser, session: SessionDep
) -> list[dict]:
    article = await _load_article_for_user(session, user, article_id)
    if article is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="article_not_found")
    versions = await session.scalars(
        select(Deliverable)
        .where(
            Deliverable.content_item_id == article_id,
            Deliverable.agent_code == ARTICLE_VERSION_AGENT_CODE,
            Deliverable.type == DeliverableType.WECHAT_ARTICLE,
        )
        .order_by(Deliverable.version)
    )
    return [_deliverable_response(version) for version in versions]


@router.get("/{article_id}/versions/{target_version}/diff")
async def get_article_version_diff(
    article_id: int,
    target_version: int,
    base_version: int,
    user: CurrentUser,
    session: SessionDep,
) -> dict:
    versions = await list_article_versions(article_id, user, session)
    by_version = {version["version"]: version for version in versions}
    if target_version not in by_version or base_version not in by_version:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="article_version_not_found"
        )
    return {
        "baseVersion": base_version,
        "targetVersion": target_version,
        **diff_versions(
            ArticleDocument.model_validate(by_version[base_version]["document"]),
            ArticleDocument.model_validate(by_version[target_version]["document"]),
        ),
    }


@router.get("/{article_id}/preview")
async def article_preview_contract(article_id: int, user: CurrentUser, session: SessionDep) -> dict:
    """Task 10 owns rendering; this only proves the scoped preview contract."""
    article = await _load_article_for_user(session, user, article_id)
    if article is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="article_not_found")
    return {"articleId": article_id, "document": article[0].document, "renderedHtml": None}
