"""Account-scoped HTTP endpoints for structured WeChat article working copies."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.db import get_session
from app.models import Account, ArticleImageSlot, Deliverable, Event
from app.models.enums import DeliverableType
from app.schemas.publishing import SyncWechatDraftRequest, WechatDraftSyncOut
from app.schemas.wechat_article import (
    ArticleDraftSyncContextOut,
    ArticleDocument,
    ArticleImageGenerationRequest,
    ArticleImagePromptOut,
    ArticleImageSelectionRequest,
    ArticleImageSlotOut,
)
from app.services.image_generation import (
    MAX_IMAGE_UPLOAD_BYTES,
    ImageGenerationIdempotencyConflict,
    ImageGenerationProvider,
    ImageGenerationScopeError,
    ImageUploadError,
    WechatArticleImageService,
)
from app.services.publishing import (
    PublishingServiceError,
    execute_wechat_draft_sync_job,
    get_wechat_draft_sync_context,
    get_wechat_draft_sync_job,
    prepare_wechat_draft_sync_job,
    wechat_draft_sync_out,
)
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
sync_router = APIRouter(tags=["wechat-articles"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class WorkingCopyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_lock_version: int = Field(ge=1)
    document: ArticleDocument


class ArticleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: int = Field(gt=0)
    document: ArticleDocument


def get_wechat_capability_probe():
    return None


def get_wechat_token_provider():
    return None


def get_wechat_draft_client():
    return None


def _draft_sync_error(error: PublishingServiceError) -> JSONResponse:
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
    "/{article_id}/draft-syncs",
    response_model=WechatDraftSyncOut,
    status_code=status.HTTP_201_CREATED,
)
async def sync_wechat_article_draft(
    article_id: int,
    body: SyncWechatDraftRequest,
    user: CurrentUser,
    session: SessionDep,
):
    try:
        job = await prepare_wechat_draft_sync_job(
            session,
            user,
            article_id=article_id,
            request=body,
        )
        capability_probe = get_wechat_capability_probe()
        token_provider = get_wechat_token_provider()
        draft_client = get_wechat_draft_client()
        if capability_probe is None or token_provider is None or draft_client is None:
            return _draft_sync_error(
                PublishingServiceError(
                    "WECHAT_DRAFT_SYNC_UNAVAILABLE",
                    "微信草稿同步服务尚未配置。",
                    retryable=True,
                    status_code=503,
                )
            )
        job = await execute_wechat_draft_sync_job(
            session,
            user,
            job_id=job.id,
            capability_probe=capability_probe,
            token_provider=token_provider,
            draft_client=draft_client,
        )
        return WechatDraftSyncOut.model_validate(wechat_draft_sync_out(job))
    except PublishingServiceError as exc:
        return _draft_sync_error(exc)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_403_FORBIDDEN:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="微信公众号文章不存在",
            ) from None
        raise


@sync_router.get(
    "/wechat-draft-syncs/{sync_id}",
    response_model=WechatDraftSyncOut,
)
async def get_wechat_draft_sync(
    sync_id: int,
    user: CurrentUser,
    session: SessionDep,
):
    try:
        job = await get_wechat_draft_sync_job(session, user, sync_id)
        return WechatDraftSyncOut.model_validate(wechat_draft_sync_out(job))
    except PublishingServiceError as exc:
        return _draft_sync_error(exc)


router.routes.extend(sync_router.routes)


def get_image_generation_provider() -> ImageGenerationProvider | None:
    """Deployment extension point; the base application has no paid provider configured."""
    return None


def _image_service(
    session: AsyncSession,
    user,
    *,
    require_provider: bool = False,
) -> WechatArticleImageService:
    provider: ImageGenerationProvider = (
        get_image_generation_provider() or _ProviderRequiredForGenerationOnly()
    )
    if require_provider and isinstance(provider, _ProviderRequiredForGenerationOnly):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="image_generation_unavailable",
        )
    return WechatArticleImageService(
        session=session,
        user=user,
        provider=provider,
    )


class _ProviderRequiredForGenerationOnly:
    async def generate(self, **_kwargs):
        raise RuntimeError("image generation provider is unavailable")


def _image_batch_response(result) -> dict:
    return {
        "requestedSlotIds": result.requested_slot_ids,
        "materialIds": result.material_ids,
        "failedSlotIds": result.failed_slot_ids,
    }


def _raise_image_action_error(exc: Exception) -> None:
    if isinstance(exc, ImageGenerationScopeError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image_not_found")
    if isinstance(exc, ImageGenerationIdempotencyConflict):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="image_state_conflict")
    raise exc


def _slot_response(slot: ArticleImageSlot) -> dict:
    return ArticleImageSlotOut(
        id=slot.id,
        stableKey=slot.stable_key,
        purpose=slot.purpose,
        aspectRatio=slot.aspect_ratio,
        visualBrief=slot.visual_brief,
        status=slot.status,
        selectedMaterialId=slot.selected_material_id,
        lockVersion=slot.lock_version,
        hasPrompt=slot.prompt_internal is not None,
    ).model_dump(mode="json")


async def _working_copy_response(session: AsyncSession, working_copy, account: Account) -> dict:
    slots = list(
        await session.scalars(
            select(ArticleImageSlot)
            .where(ArticleImageSlot.content_item_id == working_copy.content_item_id)
            .order_by(ArticleImageSlot.id)
        )
    )
    return {
        "articleId": working_copy.content_item_id,
        "document": working_copy.document,
        "lockVersion": working_copy.lock_version,
        "basedOnDeliverableId": working_copy.based_on_deliverable_id,
        "accountId": account.id,
        "accountName": account.nickname,
        "imageSlots": [_slot_response(slot) for slot in slots],
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
    article = await _load_article_for_user(session, user, content_item.id)
    if article is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="article_not_found")
    _working_copy, _content_item, account = article
    return {
        **(await _working_copy_response(session, working_copy, account)),
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
    article = await _load_article_for_user(session, user, article_id)
    if article is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="article_not_found")
    _working_copy, _content_item, account = article
    return await _working_copy_response(session, working_copy, account)


@router.get("/{article_id}/working-copy")
async def get_working_copy(article_id: int, user: CurrentUser, session: SessionDep) -> dict:
    article = await _load_article_for_user(session, user, article_id)
    if article is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="article_not_found")
    return await _working_copy_response(session, article[0], article[2])


@router.get(
    "/{article_id}/draft-sync-context",
    response_model=ArticleDraftSyncContextOut,
)
async def get_article_draft_sync_context(
    article_id: int,
    article_version_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> ArticleDraftSyncContextOut | JSONResponse:
    try:
        return await get_wechat_draft_sync_context(
            session,
            user,
            article_id=article_id,
            article_version_id=article_version_id,
        )
    except PublishingServiceError as exc:
        return _draft_sync_error(exc)


@router.get(
    "/{article_id}/image-slots/{slot_id}/prompt",
    response_model=ArticleImagePromptOut,
)
async def get_article_image_prompt(
    article_id: int,
    slot_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> ArticleImagePromptOut:
    article = await _load_article_for_user(session, user, article_id)
    if article is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="article_not_found")
    _working_copy, _content_item, account = article
    slot = await session.scalar(
        select(ArticleImageSlot).where(
            ArticleImageSlot.id == slot_id,
            ArticleImageSlot.content_item_id == article_id,
            ArticleImageSlot.account_id == account.id,
        )
    )
    if slot is None or slot.prompt_internal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image_prompt_not_found")
    session.add(
        Event(
            type="wechat.article_image_prompt.accessed",
            org_id=user.org_id,
            account_id=account.id,
            content_item_id=article_id,
            payload={"action": "prompt_read", "status": "accessed"},
        )
    )
    await session.commit()
    return ArticleImagePromptOut(prompt=slot.prompt_internal)


@router.post("/{article_id}/image-generations", response_model=None)
async def generate_article_images(
    article_id: int,
    body: ArticleImageGenerationRequest,
    user: CurrentUser,
    session: SessionDep,
) -> dict:
    try:
        result = await _image_service(session, user, require_provider=True).generate_all(
            article_id,
            idempotency_key=body.idempotency_key,
            reference_material_ids=tuple(body.reference_material_ids),
        )
    except (ImageGenerationScopeError, ImageGenerationIdempotencyConflict) as exc:
        _raise_image_action_error(exc)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="article_not_found")
    return _image_batch_response(result)


@router.post("/{article_id}/image-slots/{slot_id}/generations", response_model=None)
async def generate_article_image_slot(
    article_id: int,
    slot_id: int,
    body: ArticleImageGenerationRequest,
    user: CurrentUser,
    session: SessionDep,
) -> dict:
    try:
        result = await _image_service(session, user, require_provider=True).generate_slot(
            article_id,
            slot_id,
            idempotency_key=body.idempotency_key,
            reference_material_ids=tuple(body.reference_material_ids),
        )
    except (ImageGenerationScopeError, ImageGenerationIdempotencyConflict) as exc:
        _raise_image_action_error(exc)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="article_not_found")
    return _image_batch_response(result)


@router.post("/{article_id}/image-slots/{slot_id}/uploads", status_code=status.HTTP_201_CREATED)
async def upload_article_image_slot(
    article_id: int,
    slot_id: int,
    user: CurrentUser,
    session: SessionDep,
    file: Annotated[UploadFile, File(...)],
) -> dict:
    try:
        asset = await _image_service(session, user).upload_image(
            article_id,
            slot_id,
            content=await file.read(MAX_IMAGE_UPLOAD_BYTES + 1),
            media_type=file.content_type or "application/octet-stream",
        )
    except ImageUploadError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="invalid_image_upload",
        ) from exc
    except (ImageGenerationScopeError, ImageGenerationIdempotencyConflict) as exc:
        _raise_image_action_error(exc)
    return {"materialId": asset.id, "status": asset.status.value}


@router.put("/{article_id}/image-slots/{slot_id}/selection", response_model=None)
async def select_article_image_slot(
    article_id: int,
    slot_id: int,
    body: ArticleImageSelectionRequest,
    user: CurrentUser,
    session: SessionDep,
) -> dict:
    try:
        selected = await _image_service(session, user).select_material(
            article_id,
            slot_id,
            body.material_id,
            expected_lock_version=body.expected_lock_version,
        )
    except (ImageGenerationScopeError, ImageGenerationIdempotencyConflict) as exc:
        _raise_image_action_error(exc)
    if selected is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="article_not_found")
    return _slot_response(selected)


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
