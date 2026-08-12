"""Provider-neutral image generation for account-scoped WeChat article slots."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import uuid
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Protocol, cast

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ArticleImageSlot, Event, MaterialAsset, User
from app.models.enums import ArticleImageSlotStatus, MaterialStatus
from app.services.wechat_articles import _load_article_for_user, _record_article_key_interaction

_REQUEST_EVENT = "wechat.article_image_generation.requested"
_COMPLETED_EVENT = "wechat.article_image_generation.completed"
_FAILED_EVENT = "wechat.article_image_generation.failed"
_PRODUCT_GENERATE_ALL_REQUESTED_EVENT = "wechat.images.generate_all_requested"
_PRODUCT_IMAGE_SELECTED_EVENT = "wechat.images.image_selected"
MAX_IMAGE_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_IMAGE_DIMENSION = 8_192
MAX_IMAGE_PIXELS = 40_000_000
_IMAGE_FORMATS = {
    "image/png": ("PNG", "png"),
    "image/jpeg": ("JPEG", "jpg"),
    "image/webp": ("WEBP", "webp"),
}


class ImageGenerationIdempotencyConflict(ValueError):
    """A caller-owned key was rebound to a different request."""


class ImageGenerationScopeError(ValueError):
    """A referenced slot or material is outside the accessible article scope."""


class ImageUploadError(ValueError):
    """Untrusted uploaded bytes are not an accepted decoded image."""


@dataclass(frozen=True)
class ImageGenerationResult:
    """Provider-neutral generated image bytes and non-sensitive cost metadata."""

    provider: str
    content: bytes
    media_type: str
    cost: float | None = None


class ImageGenerationProvider(Protocol):
    async def generate(
        self,
        *,
        prompt: str,
        aspect_ratio: str,
        reference_material_ids: tuple[int, ...],
        idempotency_key: str,
    ) -> ImageGenerationResult: ...


@dataclass(frozen=True)
class ImageGenerationBatchResult:
    requested_slot_ids: list[int]
    material_ids: list[int]
    failed_slot_ids: list[int]


class WechatArticleImageService:
    """Coordinate durable image requests without holding a transaction over provider I/O."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        user: User,
        provider: ImageGenerationProvider,
    ) -> None:
        self._session = session
        self._user = user
        self._provider = provider

    async def generate_all(
        self,
        article_id: int,
        *,
        idempotency_key: str,
        reference_material_ids: tuple[int, ...] = (),
    ) -> ImageGenerationBatchResult | None:
        """Generate only planned/failed slots and replay the durable request on retries."""
        article = await _load_article_for_user(self._session, self._user, article_id)
        if article is None:
            return None
        _working_copy, _content_item, account = article
        reference_ids = tuple(sorted(set(reference_material_ids)))
        request_key = _request_key(self._user.org_id, self._user.id, idempotency_key)
        request_payload_hash = _canonical_hash(
            {"operation": "batch", "reference_material_ids": reference_ids}
        )

        existing = await self._session.scalar(
            select(Event).where(Event.idempotency_key == request_key)
        )
        if existing is not None:
            self._assert_replay_matches(
                existing,
                article_id=article_id,
                account_id=account.id,
                operation="batch",
                request_payload_hash=request_payload_hash,
            )
            requested_slot_ids = [int(value) for value in (existing.payload or {})["slot_ids"]]
            await self._session.commit()
            return await self._resume_request(
                article_id=article_id,
                account_id=account.id,
                request_key=request_key,
                requested_slot_ids=requested_slot_ids,
                reference_material_ids=reference_ids,
            )

        await self._require_scoped_reference_materials(article_id, reference_ids)
        slots = list(
            await self._session.scalars(
                select(ArticleImageSlot)
                .where(
                    ArticleImageSlot.content_item_id == article_id,
                    ArticleImageSlot.account_id == account.id,
                    ArticleImageSlot.status.in_(
                        (ArticleImageSlotStatus.PLANNED, ArticleImageSlotStatus.FAILED)
                    ),
                )
                .order_by(ArticleImageSlot.id)
            )
        )
        claimed_slot_ids: list[int] = []
        for slot in slots:
            claimed = await self._session.execute(
                update(ArticleImageSlot)
                .where(
                    ArticleImageSlot.id == slot.id,
                    ArticleImageSlot.content_item_id == article_id,
                    ArticleImageSlot.account_id == account.id,
                    ArticleImageSlot.status == slot.status,
                    ArticleImageSlot.lock_version == slot.lock_version,
                )
                .values(
                    status=ArticleImageSlotStatus.GENERATING,
                    lock_version=ArticleImageSlot.lock_version + 1,
                )
            )
            if getattr(claimed, "rowcount", 0) == 1:
                claimed_slot_ids.append(slot.id)

        self._session.add(
            Event(
                type=_REQUEST_EVENT,
                org_id=self._user.org_id,
                account_id=account.id,
                content_item_id=article_id,
                payload={
                    "status": "requested",
                    "operation": "batch",
                    "request_hash": request_payload_hash,
                    "slot_ids": claimed_slot_ids,
                    "reference_material_ids": list(reference_ids),
                },
                idempotency_key=request_key,
            )
        )
        self._session.add(
            Event(
                type=_PRODUCT_GENERATE_ALL_REQUESTED_EVENT,
                org_id=self._user.org_id,
                account_id=account.id,
                content_item_id=article_id,
                payload={
                    "account_id": account.id,
                    "article_id": article_id,
                    "requested_slot_count": len(claimed_slot_ids),
                    "reference_material_count": len(reference_ids),
                },
            )
        )
        _record_article_key_interaction(
            self._session,
            org_id=self._user.org_id,
            account_id=account.id,
            article_id=article_id,
            interaction_type="images_generate_all_requested",
        )
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            winner = await self._session.scalar(
                select(Event).where(Event.idempotency_key == request_key)
            )
            if winner is None:
                raise
            self._assert_replay_matches(
                winner,
                article_id=article_id,
                account_id=account.id,
                operation="batch",
                request_payload_hash=request_payload_hash,
            )
            claimed_slot_ids = [int(value) for value in (winner.payload or {})["slot_ids"]]
            await self._session.commit()

        return await self._resume_request(
            article_id=article_id,
            account_id=account.id,
            request_key=request_key,
            requested_slot_ids=claimed_slot_ids,
            reference_material_ids=reference_ids,
        )

    async def generate_slot(
        self,
        article_id: int,
        slot_id: int,
        *,
        idempotency_key: str,
        reference_material_ids: tuple[int, ...] = (),
    ) -> ImageGenerationBatchResult | None:
        """Generate one explicitly requested planned/failed slot."""
        article = await _load_article_for_user(self._session, self._user, article_id)
        if article is None:
            return None
        _working_copy, _content_item, account = article
        slot = await self._session.scalar(
            select(ArticleImageSlot).where(
                ArticleImageSlot.id == slot_id,
                ArticleImageSlot.content_item_id == article_id,
                ArticleImageSlot.account_id == account.id,
            )
        )
        if slot is None:
            raise ImageGenerationScopeError("image slot is outside the article scope")
        reference_ids = tuple(sorted(set(reference_material_ids)))
        request_key = _request_key(self._user.org_id, self._user.id, idempotency_key)
        request_payload_hash = _canonical_hash(
            {
                "operation": "single",
                "slot_id": slot_id,
                "reference_material_ids": reference_ids,
            }
        )
        existing = await self._session.scalar(
            select(Event).where(Event.idempotency_key == request_key)
        )
        if existing is not None:
            self._assert_replay_matches(
                existing,
                article_id=article_id,
                account_id=account.id,
                operation="single",
                request_payload_hash=request_payload_hash,
            )
            requested_slot_ids = [int(value) for value in (existing.payload or {})["slot_ids"]]
            await self._session.commit()
            return await self._resume_request(
                article_id=article_id,
                account_id=account.id,
                request_key=request_key,
                requested_slot_ids=requested_slot_ids,
                reference_material_ids=reference_ids,
            )

        await self._require_scoped_reference_materials(article_id, reference_ids)
        claimed_slot_ids: list[int] = []
        if slot.status in (ArticleImageSlotStatus.PLANNED, ArticleImageSlotStatus.FAILED):
            claimed = await self._session.execute(
                update(ArticleImageSlot)
                .where(
                    ArticleImageSlot.id == slot_id,
                    ArticleImageSlot.content_item_id == article_id,
                    ArticleImageSlot.account_id == account.id,
                    ArticleImageSlot.status == slot.status,
                    ArticleImageSlot.lock_version == slot.lock_version,
                )
                .values(
                    status=ArticleImageSlotStatus.GENERATING,
                    lock_version=slot.lock_version + 1,
                )
            )
            if getattr(claimed, "rowcount", 0) == 1:
                claimed_slot_ids.append(slot_id)
        self._session.add(
            Event(
                type=_REQUEST_EVENT,
                org_id=self._user.org_id,
                account_id=account.id,
                content_item_id=article_id,
                payload={
                    "status": "requested",
                    "operation": "single",
                    "request_hash": request_payload_hash,
                    "slot_ids": claimed_slot_ids,
                    "reference_material_ids": list(reference_ids),
                },
                idempotency_key=request_key,
            )
        )
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            winner = await self._session.scalar(
                select(Event).where(Event.idempotency_key == request_key)
            )
            if winner is None:
                raise
            self._assert_replay_matches(
                winner,
                article_id=article_id,
                account_id=account.id,
                operation="single",
                request_payload_hash=request_payload_hash,
            )
            claimed_slot_ids = [int(value) for value in (winner.payload or {})["slot_ids"]]
            await self._session.commit()
        return await self._resume_request(
            article_id=article_id,
            account_id=account.id,
            request_key=request_key,
            requested_slot_ids=claimed_slot_ids,
            reference_material_ids=reference_ids,
        )

    async def select_material(
        self,
        article_id: int,
        slot_id: int,
        material_id: int,
        *,
        expected_lock_version: int,
    ) -> ArticleImageSlot | None:
        """Select one exact-scope READY image, retaining any old candidate asset."""
        article = await _load_article_for_user(self._session, self._user, article_id)
        if article is None:
            return None
        _working_copy, _content_item, account = article
        material = await self._session.scalar(
            select(MaterialAsset).where(
                MaterialAsset.id == material_id,
                MaterialAsset.org_id == self._user.org_id,
                MaterialAsset.content_item_id == article_id,
                MaterialAsset.kind == "image",
                MaterialAsset.status == MaterialStatus.READY,
            )
        )
        if material is None:
            raise ImageGenerationScopeError("material is outside the article scope")
        slot = await self._session.scalar(
            select(ArticleImageSlot).where(
                ArticleImageSlot.id == slot_id,
                ArticleImageSlot.content_item_id == article_id,
                ArticleImageSlot.account_id == account.id,
            )
        )
        if slot is None:
            raise ImageGenerationScopeError("image slot is outside the article scope")
        if slot.status == ArticleImageSlotStatus.SELECTED:
            released = await self._session.execute(
                update(ArticleImageSlot)
                .where(
                    ArticleImageSlot.id == slot_id,
                    ArticleImageSlot.content_item_id == article_id,
                    ArticleImageSlot.account_id == account.id,
                    ArticleImageSlot.status == ArticleImageSlotStatus.SELECTED,
                    ArticleImageSlot.lock_version == expected_lock_version,
                )
                .values(
                    status=ArticleImageSlotStatus.READY,
                    selected_material_id=None,
                    lock_version=expected_lock_version + 1,
                )
            )
            if getattr(released, "rowcount", 0) != 1:
                await self._session.rollback()
                raise ImageGenerationIdempotencyConflict("image slot version conflict")
            expected_lock_version += 1
        elif slot.status != ArticleImageSlotStatus.READY:
            raise ImageGenerationIdempotencyConflict("image slot is not ready for selection")

        selected = await self._session.execute(
            update(ArticleImageSlot)
            .where(
                ArticleImageSlot.id == slot_id,
                ArticleImageSlot.content_item_id == article_id,
                ArticleImageSlot.account_id == account.id,
                ArticleImageSlot.status == ArticleImageSlotStatus.READY,
                ArticleImageSlot.lock_version == expected_lock_version,
            )
            .values(
                status=ArticleImageSlotStatus.SELECTED,
                selected_material_id=material.id,
                lock_version=expected_lock_version + 1,
            )
        )
        if getattr(selected, "rowcount", 0) != 1:
            await self._session.rollback()
            raise ImageGenerationIdempotencyConflict("image slot version conflict")
        self._session.add(
            Event(
                type=_PRODUCT_IMAGE_SELECTED_EVENT,
                org_id=self._user.org_id,
                account_id=account.id,
                content_item_id=article_id,
                payload={
                    "account_id": account.id,
                    "article_id": article_id,
                    "slot_id": slot_id,
                    "material_id": material.id,
                    "selection_source": "existing_material",
                },
            )
        )
        _record_article_key_interaction(
            self._session,
            org_id=self._user.org_id,
            account_id=account.id,
            article_id=article_id,
            interaction_type="image_selected",
        )
        await self._session.commit()
        return await self._session.scalar(
            select(ArticleImageSlot).where(ArticleImageSlot.id == slot_id)
        )

    async def upload_image(
        self,
        article_id: int,
        slot_id: int,
        *,
        content: bytes,
        media_type: str,
    ) -> MaterialAsset:
        """Strictly decode, re-encode, atomically store, and select a user image."""
        article = await _load_article_for_user(self._session, self._user, article_id)
        if article is None:
            raise ImageGenerationScopeError("article is outside the accessible scope")
        _working_copy, _content_item, account = article
        slot = await self._session.scalar(
            select(ArticleImageSlot).where(
                ArticleImageSlot.id == slot_id,
                ArticleImageSlot.content_item_id == article_id,
                ArticleImageSlot.account_id == account.id,
            )
        )
        if slot is None:
            raise ImageGenerationScopeError("image slot is outside the article scope")
        if slot.status == ArticleImageSlotStatus.GENERATING:
            raise ImageGenerationIdempotencyConflict("image slot is not ready for selection")

        target: Path | None = None
        try:
            relative_path, size_bytes, target = _store_validated_image(
                content,
                media_type,
                org_id=self._user.org_id,
                article_id=article_id,
            )

            asset = MaterialAsset(
                org_id=self._user.org_id,
                content_item_id=article_id,
                kind="image",
                provider="user_upload",
                status=MaterialStatus.READY,
                local_path=relative_path,
                size_bytes=size_bytes,
            )
            self._session.add(asset)
            await self._session.flush()
            expected_lock_version = slot.lock_version
            if slot.status == ArticleImageSlotStatus.SELECTED:
                released = await self._session.execute(
                    update(ArticleImageSlot)
                    .where(
                        ArticleImageSlot.id == slot_id,
                        ArticleImageSlot.content_item_id == article_id,
                        ArticleImageSlot.account_id == account.id,
                        ArticleImageSlot.status == ArticleImageSlotStatus.SELECTED,
                        ArticleImageSlot.lock_version == expected_lock_version,
                    )
                    .values(
                        status=ArticleImageSlotStatus.READY,
                        selected_material_id=None,
                        lock_version=expected_lock_version + 1,
                    )
                )
                if getattr(released, "rowcount", 0) != 1:
                    raise ImageGenerationIdempotencyConflict("image slot version conflict")
                expected_lock_version += 1
            elif slot.status in (ArticleImageSlotStatus.PLANNED, ArticleImageSlotStatus.FAILED):
                made_ready = await self._session.execute(
                    update(ArticleImageSlot)
                    .where(
                        ArticleImageSlot.id == slot_id,
                        ArticleImageSlot.content_item_id == article_id,
                        ArticleImageSlot.account_id == account.id,
                        ArticleImageSlot.status == slot.status,
                        ArticleImageSlot.lock_version == expected_lock_version,
                    )
                    .values(
                        status=ArticleImageSlotStatus.READY,
                        selected_material_id=None,
                        lock_version=expected_lock_version + 1,
                    )
                )
                if getattr(made_ready, "rowcount", 0) != 1:
                    raise ImageGenerationIdempotencyConflict("image slot version conflict")
                expected_lock_version += 1
            selected = await self._session.execute(
                update(ArticleImageSlot)
                .where(
                    ArticleImageSlot.id == slot_id,
                    ArticleImageSlot.content_item_id == article_id,
                    ArticleImageSlot.account_id == account.id,
                    ArticleImageSlot.status == ArticleImageSlotStatus.READY,
                    ArticleImageSlot.lock_version == expected_lock_version,
                )
                .values(
                    status=ArticleImageSlotStatus.SELECTED,
                    selected_material_id=asset.id,
                    lock_version=expected_lock_version + 1,
                )
            )
            if getattr(selected, "rowcount", 0) != 1:
                raise ImageGenerationIdempotencyConflict("image slot version conflict")
            self._session.add(
                Event(
                    type=_PRODUCT_IMAGE_SELECTED_EVENT,
                    org_id=self._user.org_id,
                    account_id=account.id,
                    content_item_id=article_id,
                    payload={
                        "account_id": account.id,
                        "article_id": article_id,
                        "slot_id": slot_id,
                        "material_id": asset.id,
                        "selection_source": "upload",
                    },
                )
            )
            _record_article_key_interaction(
                self._session,
                org_id=self._user.org_id,
                account_id=account.id,
                article_id=article_id,
                interaction_type="image_selected",
            )
            await self._session.commit()
            return asset
        except Exception:
            await self._session.rollback()
            if target is not None:
                target.unlink(missing_ok=True)
            raise

    def _assert_replay_matches(
        self,
        event: Event,
        *,
        article_id: int,
        account_id: int,
        operation: str,
        request_payload_hash: str,
    ) -> None:
        payload = event.payload or {}
        if (
            event.type != _REQUEST_EVENT
            or event.org_id != self._user.org_id
            or event.account_id != account_id
            or event.content_item_id != article_id
            or payload.get("operation") != operation
            or payload.get("request_hash") != request_payload_hash
        ):
            raise ImageGenerationIdempotencyConflict("idempotency key request conflict")

    async def _require_scoped_reference_materials(
        self, article_id: int, reference_material_ids: tuple[int, ...]
    ) -> None:
        if not reference_material_ids:
            return
        references = list(
            await self._session.scalars(
                select(MaterialAsset).where(MaterialAsset.id.in_(reference_material_ids))
            )
        )
        if {material.id for material in references} != set(reference_material_ids) or any(
            material.org_id != self._user.org_id
            or material.content_item_id != article_id
            or material.kind != "image"
            or material.status != MaterialStatus.READY
            for material in references
        ):
            raise ImageGenerationScopeError("reference material is outside the article scope")

    async def _resume_request(
        self,
        *,
        article_id: int,
        account_id: int,
        request_key: str,
        requested_slot_ids: list[int],
        reference_material_ids: tuple[int, ...],
    ) -> ImageGenerationBatchResult:
        material_ids: list[int] = []
        failed_slot_ids: list[int] = []
        for slot_id in requested_slot_ids:
            result_key = _result_key(request_key, slot_id)
            persisted = await self._session.scalar(
                select(Event).where(Event.idempotency_key == result_key)
            )
            if persisted is not None:
                self._append_persisted_result(
                    persisted,
                    article_id=article_id,
                    account_id=account_id,
                    slot_id=slot_id,
                    material_ids=material_ids,
                    failed_slot_ids=failed_slot_ids,
                )
                continue

            slot = await self._session.scalar(
                select(ArticleImageSlot).where(
                    ArticleImageSlot.id == slot_id,
                    ArticleImageSlot.content_item_id == article_id,
                    ArticleImageSlot.account_id == account_id,
                    ArticleImageSlot.status == ArticleImageSlotStatus.GENERATING,
                )
            )
            if slot is None:
                failed_slot_ids.append(slot_id)
                await self._session.commit()
                continue
            prompt = slot.prompt_internal or slot.visual_brief
            aspect_ratio = slot.aspect_ratio
            expected_lock_version = slot.lock_version

            # End the read transaction before any paid/slow provider operation.
            await self._session.commit()
            stored_path: Path | None = None
            try:
                generated = await self._provider.generate(
                    prompt=prompt,
                    aspect_ratio=aspect_ratio,
                    reference_material_ids=reference_material_ids,
                    idempotency_key=_provider_key(request_key, slot_id),
                )
                relative_path, size_bytes, stored_path = _store_validated_image(
                    generated.content,
                    generated.media_type,
                    org_id=self._user.org_id,
                    article_id=article_id,
                )
            except Exception:
                finalized = await self._finalize_failure(
                    article_id=article_id,
                    account_id=account_id,
                    slot_id=slot_id,
                    expected_lock_version=expected_lock_version,
                    result_key=result_key,
                )
                if not finalized:
                    replay = await self._session.scalar(
                        select(Event).where(Event.idempotency_key == result_key)
                    )
                    if replay is not None and replay.type == _COMPLETED_EVENT:
                        material_ids.append(int(cast(dict, replay.payload)["material_id"]))
                        continue
                failed_slot_ids.append(slot_id)
                continue

            try:
                asset = MaterialAsset(
                    org_id=self._user.org_id,
                    content_item_id=article_id,
                    kind="image",
                    provider=generated.provider[:32],
                    status=MaterialStatus.READY,
                    local_path=relative_path,
                    size_bytes=size_bytes,
                )
                self._session.add(asset)
                await self._session.flush()
                transition = await self._session.execute(
                    update(ArticleImageSlot)
                    .where(
                        ArticleImageSlot.id == slot_id,
                        ArticleImageSlot.content_item_id == article_id,
                        ArticleImageSlot.account_id == account_id,
                        ArticleImageSlot.status == ArticleImageSlotStatus.GENERATING,
                        ArticleImageSlot.lock_version == expected_lock_version,
                    )
                    .values(
                        status=ArticleImageSlotStatus.READY,
                        lock_version=expected_lock_version + 1,
                    )
                )
            except Exception:
                await self._session.rollback()
                if stored_path is not None:
                    stored_path.unlink(missing_ok=True)
                raise
            if getattr(transition, "rowcount", 0) != 1:
                await self._session.rollback()
                if stored_path is not None:
                    stored_path.unlink(missing_ok=True)
                replay = await self._session.scalar(
                    select(Event).where(Event.idempotency_key == result_key)
                )
                if replay is not None and replay.type == _COMPLETED_EVENT:
                    material_ids.append(int(cast(dict, replay.payload)["material_id"]))
                else:
                    failed_slot_ids.append(slot_id)
                continue
            self._session.add(
                Event(
                    type=_COMPLETED_EVENT,
                    org_id=self._user.org_id,
                    account_id=account_id,
                    content_item_id=article_id,
                    payload={
                        "status": "completed",
                        "material_id": asset.id,
                        "cost": _safe_cost(generated.cost),
                    },
                    idempotency_key=result_key,
                )
            )
            try:
                await self._session.commit()
            except IntegrityError:
                await self._session.rollback()
                if stored_path is not None:
                    stored_path.unlink(missing_ok=True)
                replay = await self._session.scalar(
                    select(Event).where(Event.idempotency_key == result_key)
                )
                if replay is None or replay.type != _COMPLETED_EVENT:
                    failed_slot_ids.append(slot_id)
                    continue
                asset_id = int(cast(dict, replay.payload)["material_id"])
                material_ids.append(asset_id)
                continue
            except Exception:
                await self._session.rollback()
                if stored_path is not None:
                    stored_path.unlink(missing_ok=True)
                raise
            material_ids.append(asset.id)

        return ImageGenerationBatchResult(
            requested_slot_ids=requested_slot_ids,
            material_ids=material_ids,
            failed_slot_ids=failed_slot_ids,
        )

    def _append_persisted_result(
        self,
        event: Event,
        *,
        article_id: int,
        account_id: int,
        slot_id: int,
        material_ids: list[int],
        failed_slot_ids: list[int],
    ) -> None:
        if (
            event.org_id != self._user.org_id
            or event.account_id != account_id
            or event.content_item_id != article_id
        ):
            raise ImageGenerationIdempotencyConflict("result event scope conflict")
        if event.type == _COMPLETED_EVENT:
            material_ids.append(int(cast(dict, event.payload)["material_id"]))
        elif event.type == _FAILED_EVENT:
            failed_slot_ids.append(slot_id)
        else:
            raise ImageGenerationIdempotencyConflict("result event type conflict")

    async def _finalize_failure(
        self,
        *,
        article_id: int,
        account_id: int,
        slot_id: int,
        expected_lock_version: int,
        result_key: str,
    ) -> bool:
        finalized = await self._session.execute(
            update(ArticleImageSlot)
            .where(
                ArticleImageSlot.id == slot_id,
                ArticleImageSlot.content_item_id == article_id,
                ArticleImageSlot.account_id == account_id,
                ArticleImageSlot.status == ArticleImageSlotStatus.GENERATING,
                ArticleImageSlot.lock_version == expected_lock_version,
            )
            .values(
                status=ArticleImageSlotStatus.FAILED,
                lock_version=expected_lock_version + 1,
            )
        )
        if getattr(finalized, "rowcount", 0) != 1:
            await self._session.rollback()
            return False
        self._session.add(
            Event(
                type=_FAILED_EVENT,
                org_id=self._user.org_id,
                account_id=account_id,
                content_item_id=article_id,
                payload={"status": "failed"},
                idempotency_key=result_key,
            )
        )
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
        return True


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _request_key(org_id: int, user_id: int, user_key: str) -> str:
    return _canonical_hash(["wechat-article-image-request-v1", org_id, user_id, user_key])


def _provider_key(request_key: str, slot_id: int) -> str:
    return _canonical_hash(["wechat-article-image-provider-v1", request_key, slot_id])


def _result_key(request_key: str, slot_id: int) -> str:
    return _canonical_hash(["wechat-article-image-result-v1", request_key, slot_id])


def _safe_cost(cost: float | None) -> float | None:
    if cost is None:
        return None
    normalized = float(cost)
    return normalized if math.isfinite(normalized) and normalized >= 0 else None


def _validated_reencoded_image(content: bytes, media_type: str) -> tuple[bytes, str]:
    if len(content) > MAX_IMAGE_UPLOAD_BYTES:
        raise ImageUploadError("image exceeds the encoded byte limit")
    expected = _IMAGE_FORMATS.get(media_type.lower())
    if expected is None:
        raise ImageUploadError("unsupported image media type")
    expected_format, suffix = expected
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as probe:
                if probe.format != expected_format:
                    raise ImageUploadError("declared media type does not match image format")
                width, height = probe.size
                if (
                    width <= 0
                    or height <= 0
                    or width > MAX_IMAGE_DIMENSION
                    or height > MAX_IMAGE_DIMENSION
                    or width * height > MAX_IMAGE_PIXELS
                ):
                    raise ImageUploadError("decoded image dimensions exceed the limit")
                probe.verify()
            with Image.open(BytesIO(content)) as decoded:
                if decoded.format != expected_format:
                    raise ImageUploadError("decoded image format changed during verification")
                decoded.load()
                if expected_format == "JPEG":
                    clean = decoded.convert("RGB")
                elif decoded.mode not in ("RGB", "RGBA"):
                    clean = decoded.convert("RGBA")
                else:
                    clean = decoded.copy()
                output = BytesIO()
                save_options = {"quality": 95} if expected_format == "JPEG" else {}
                clean.save(output, format=expected_format, **save_options)
                return output.getvalue(), suffix
    except ImageUploadError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageUploadError("image exceeds safe decoded pixel limits") from exc
    except (OSError, SyntaxError, UnidentifiedImageError, ValueError) as exc:
        raise ImageUploadError("image data is invalid or truncated") from exc


def _store_validated_image(
    content: bytes,
    media_type: str,
    *,
    org_id: int,
    article_id: int,
) -> tuple[str, int, Path]:
    """Validate once, then atomically move a metadata-free image onto local storage."""
    normalized, suffix = _validated_reencoded_image(content, media_type)
    relative_path = (
        Path("wechat-images") / str(org_id) / str(article_id) / f"{uuid.uuid4().hex}.{suffix}"
    )
    target = Path(settings.storage_local_dir) / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=target.parent, prefix=".upload-", delete=False
        ) as temporary:
            temporary.write(normalized)
            temporary.flush()
            os.fsync(temporary.fileno())
            temp_path = Path(temporary.name)
        os.replace(temp_path, target)
        temp_path = None
        return relative_path.as_posix(), len(normalized), target
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
