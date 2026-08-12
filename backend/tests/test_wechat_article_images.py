"""Image generation, disclosure, upload, and selection contracts for WeChat articles."""

from __future__ import annotations

import base64
from collections.abc import Callable
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import (
    Account,
    ArticleImageSlot,
    ArticleWorkingCopy,
    ContentItem,
    Event,
    MaterialAsset,
)
from app.models.enums import ArticleImageSlotStatus, MaterialStatus, Platform
from app.services.image_generation import (
    MAX_IMAGE_UPLOAD_BYTES,
    ImageGenerationIdempotencyConflict,
    ImageGenerationResult,
    ImageGenerationScopeError,
    ImageUploadError,
    WechatArticleImageService,
)

_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture(autouse=True)
def _isolated_image_storage(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))


class _FakeProvider:
    def __init__(
        self,
        *,
        before_generate: Callable[[str], object] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.before_generate = before_generate
        self.error = error

    async def generate(
        self,
        *,
        prompt: str,
        aspect_ratio: str,
        reference_material_ids: tuple[int, ...],
        idempotency_key: str,
    ) -> ImageGenerationResult:
        self.calls.append(
            {
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "reference_material_ids": reference_material_ids,
                "idempotency_key": idempotency_key,
            }
        )
        if self.before_generate is not None:
            observed = self.before_generate(idempotency_key)
            if hasattr(observed, "__await__"):
                await observed
        if self.error is not None:
            raise self.error
        return ImageGenerationResult(
            provider="fake-provider",
            content=_ONE_PIXEL_PNG,
            media_type="image/png",
        )


def _document() -> dict:
    return {
        "title": "Image planning article",
        "digest": "Image generation state tests.",
        "blocks": [
            {"type": "paragraph", "block_id": "intro", "text": "Introduction."},
            {"type": "imageSlot", "block_id": "planned-image", "slot_key": "planned"},
            {"type": "imageSlot", "block_id": "ready-image", "slot_key": "ready"},
            {"type": "imageSlot", "block_id": "upload-image", "slot_key": "upload"},
        ],
    }


async def _article_with_slot(session, admin, *, status=ArticleImageSlotStatus.PLANNED):
    account = Account(
        org_id=admin.org_id,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="Image account",
    )
    session.add(account)
    await session.flush()
    article = ContentItem(account_id=account.id, created_by_id=admin.id, title="Image article")
    session.add(article)
    await session.flush()
    session.add(
        ArticleWorkingCopy(
            content_item_id=article.id,
            account_id=account.id,
            document=_document(),
            updated_by_id=admin.id,
        )
    )
    slot = ArticleImageSlot(
        content_item_id=article.id,
        account_id=account.id,
        stable_key="planned",
        purpose="Explain the planned image.",
        aspect_ratio="3:2",
        visual_brief="A practical product detail.",
        prompt_internal="Create a practical product detail.",
        status=status,
    )
    session.add(slot)
    await session.commit()
    return article, slot


async def _headers(client, *, email="admin@test.com", password="admin-pw-123"):
    login = await client.post("/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.mark.asyncio
async def test_generate_all_skips_ready_and_uploaded_slots(session, admin):
    """Batch generation must spend quota only for slots that still need an image."""
    account = Account(
        org_id=admin.org_id,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="Image account",
    )
    session.add(account)
    await session.flush()
    article = ContentItem(account_id=account.id, created_by_id=admin.id, title="Image article")
    session.add(article)
    await session.flush()
    session.add(
        ArticleWorkingCopy(
            content_item_id=article.id,
            account_id=account.id,
            document=_document(),
            updated_by_id=admin.id,
        )
    )
    planned_slot = ArticleImageSlot(
        content_item_id=article.id,
        account_id=account.id,
        stable_key="planned",
        purpose="Explain the planned image.",
        aspect_ratio="3:2",
        visual_brief="A practical product detail.",
        prompt_internal="Create a practical product detail.",
    )
    ready_slot = ArticleImageSlot(
        content_item_id=article.id,
        account_id=account.id,
        stable_key="ready",
        purpose="Already generated.",
        aspect_ratio="3:2",
        visual_brief="Already available.",
        prompt_internal="Do not regenerate this image.",
        status=ArticleImageSlotStatus.READY,
    )
    uploaded_asset = MaterialAsset(
        org_id=admin.org_id,
        content_item_id=article.id,
        kind="image",
        provider="user_upload",
        status=MaterialStatus.READY,
        local_path="wechat-images/existing.png",
        size_bytes=len(_ONE_PIXEL_PNG),
    )
    uploaded_slot = ArticleImageSlot(
        content_item_id=article.id,
        account_id=account.id,
        stable_key="upload",
        purpose="User selected upload.",
        aspect_ratio="1:1",
        visual_brief="User supplied.",
        prompt_internal="Do not regenerate this upload.",
        status=ArticleImageSlotStatus.SELECTED,
        selected_material=uploaded_asset,
    )
    session.add_all([planned_slot, ready_slot, uploaded_slot])
    await session.commit()

    service = WechatArticleImageService(session=session, user=admin, provider=_FakeProvider())
    result = await service.generate_all(article.id, idempotency_key="article-7-images-v1")

    assert result.requested_slot_ids == [planned_slot.id]
    assert ready_slot.id not in result.requested_slot_ids
    assert uploaded_slot.id not in result.requested_slot_ids


@pytest.mark.asyncio
async def test_batch_request_is_committed_before_provider_and_replay_returns_persisted_result(
    session, admin
):
    """A crash-safe request record must precede provider spend and replay must not spend twice."""
    article, slot = await _article_with_slot(session, admin)
    independent_sessions = async_sessionmaker(session.bind, expire_on_commit=False)
    observed_request: dict | None = None

    async def observe_committed_request(_provider_key: str) -> None:
        nonlocal observed_request
        async with independent_sessions() as verifier:
            request = await verifier.scalar(
                select(Event).where(Event.type == "wechat.article_image_generation.requested")
            )
            observed_request = request.payload if request is not None else None

    provider = _FakeProvider(before_generate=observe_committed_request)
    service = WechatArticleImageService(session=session, user=admin, provider=provider)

    first = await service.generate_all(article.id, idempotency_key="durable-batch-key")
    replay = await service.generate_all(article.id, idempotency_key="durable-batch-key")

    assert observed_request is not None
    assert observed_request["operation"] == "batch"
    assert "prompt" not in observed_request
    assert "secret" not in str(observed_request).lower()
    assert len(provider.calls) == 1
    assert replay == first
    assert replay.requested_slot_ids == [slot.id]
    events = list(
        await session.scalars(
            select(Event)
            .where(Event.content_item_id == article.id)
            .order_by(Event.created_at, Event.id)
        )
    )
    generation_events = [
        event for event in events if event.type.startswith("wechat.article_image_generation.")
    ]
    assert [event.type for event in generation_events] == [
        "wechat.article_image_generation.requested",
        "wechat.article_image_generation.completed",
    ]
    assert generation_events[1].payload == {
        "status": "completed",
        "material_id": first.material_ids[0],
        "cost": None,
    }
    generate_all_requested = await session.scalar(
        select(Event)
        .where(Event.type == "wechat.images.generate_all_requested")
        .order_by(Event.id.desc())
    )
    assert generate_all_requested is not None
    assert generate_all_requested.payload == {
        "account_id": slot.account_id,
        "article_id": article.id,
        "requested_slot_count": 1,
        "reference_material_count": 0,
    }
    interaction = await session.scalar(
        select(Event)
        .where(Event.type == "wechat.article.key_interaction_recorded")
        .order_by(Event.id.desc())
    )
    assert interaction is not None
    assert interaction.payload == {
        "account_id": slot.account_id,
        "article_id": article.id,
        "interaction_type": "images_generate_all_requested",
        "count": 1,
    }


@pytest.mark.asyncio
async def test_batch_idempotency_rejects_changed_payload_and_scope(session, admin):
    """A caller key cannot be rebound to another request payload or article scope."""
    first_article, _slot = await _article_with_slot(session, admin)
    second_article, _second_slot = await _article_with_slot(session, admin)
    service = WechatArticleImageService(session=session, user=admin, provider=_FakeProvider())
    await service.generate_all(first_article.id, idempotency_key="one-logical-request")

    with pytest.raises(ImageGenerationIdempotencyConflict):
        await service.generate_all(
            first_article.id,
            idempotency_key="one-logical-request",
            reference_material_ids=(123,),
        )
    with pytest.raises(ImageGenerationIdempotencyConflict):
        await service.generate_all(second_article.id, idempotency_key="one-logical-request")


@pytest.mark.asyncio
async def test_failed_provider_marks_slot_failed_and_records_only_sanitized_status(session, admin):
    """Provider exceptions must not leak messages or leave a slot stuck generating."""
    article, slot = await _article_with_slot(session, admin)
    provider = _FakeProvider(error=RuntimeError("api_key=super-secret provider payload"))
    service = WechatArticleImageService(session=session, user=admin, provider=provider)

    result = await service.generate_all(article.id, idempotency_key="failed-batch")

    await session.refresh(slot)
    assert slot.status == ArticleImageSlotStatus.FAILED
    assert result.failed_slot_ids == [slot.id]
    failed = await session.scalar(
        select(Event).where(Event.type == "wechat.article_image_generation.failed")
    )
    assert failed is not None
    assert failed.payload == {"status": "failed"}


@pytest.mark.asyncio
async def test_generation_rejects_reference_material_outside_article_scope(session, admin):
    """Reference IDs must resolve to READY images owned by this exact article."""
    article, _slot = await _article_with_slot(session, admin)
    other_article, _other_slot = await _article_with_slot(session, admin)
    foreign_reference = MaterialAsset(
        org_id=admin.org_id,
        content_item_id=other_article.id,
        kind="image",
        provider="user_upload",
        status=MaterialStatus.READY,
        local_path="wechat-images/foreign.png",
        size_bytes=len(_ONE_PIXEL_PNG),
    )
    session.add(foreign_reference)
    await session.commit()
    provider = _FakeProvider()
    service = WechatArticleImageService(session=session, user=admin, provider=provider)

    with pytest.raises(ImageGenerationScopeError):
        await service.generate_all(
            article.id,
            idempotency_key="foreign-reference",
            reference_material_ids=(foreign_reference.id,),
        )

    assert provider.calls == []


@pytest.mark.asyncio
async def test_generated_image_bytes_are_validated_and_stored_as_ready_asset(
    session, admin, tmp_path, monkeypatch
):
    """A completed generation must persist usable bytes, not only a database placeholder."""
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    article, _slot = await _article_with_slot(session, admin)
    service = WechatArticleImageService(session=session, user=admin, provider=_FakeProvider())

    result = await service.generate_all(article.id, idempotency_key="stored-generation")

    asset = await session.get(MaterialAsset, result.material_ids[0])
    assert asset is not None
    assert asset.status == MaterialStatus.READY
    assert asset.local_path is not None
    stored_path = tmp_path / Path(asset.local_path)
    assert stored_path.is_file()
    with Image.open(stored_path) as decoded:
        decoded.verify()


@pytest.mark.asyncio
async def test_generated_image_file_is_removed_when_asset_flush_fails(
    session, admin, tmp_path, monkeypatch
):
    """A generated file must not survive when its MaterialAsset cannot be persisted."""
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    article, _slot = await _article_with_slot(session, admin)
    service = WechatArticleImageService(session=session, user=admin, provider=_FakeProvider())

    async def fail_flush() -> None:
        raise RuntimeError("simulated material flush failure")

    monkeypatch.setattr(session, "flush", fail_flush)
    with pytest.raises(RuntimeError, match="material flush failure"):
        await service.generate_all(article.id, idempotency_key="failed-generated-file")

    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []


@pytest.mark.asyncio
async def test_generate_single_slot_claims_only_the_explicit_planned_slot(session, admin):
    """A per-slot action must spend only for the explicitly requested slot."""
    article, target_slot = await _article_with_slot(session, admin)
    other_slot = ArticleImageSlot(
        content_item_id=article.id,
        account_id=target_slot.account_id,
        stable_key="other",
        purpose="Keep this slot untouched.",
        aspect_ratio="1:1",
        visual_brief="Another image.",
        prompt_internal="Do not generate this image.",
    )
    session.add(other_slot)
    await session.commit()
    provider = _FakeProvider()
    service = WechatArticleImageService(session=session, user=admin, provider=provider)

    result = await service.generate_slot(
        article.id,
        target_slot.id,
        idempotency_key="explicit-single-slot",
    )

    assert result.requested_slot_ids == [target_slot.id]
    assert len(provider.calls) == 1
    await session.refresh(other_slot)
    assert other_slot.status == ArticleImageSlotStatus.PLANNED


@pytest.mark.asyncio
async def test_selecting_replacement_keeps_old_asset_and_rejects_cross_article_asset(
    session, admin
):
    """Replacing selection must retain history and accept only an exact-scope READY image."""
    article, slot = await _article_with_slot(session, admin, status=ArticleImageSlotStatus.SELECTED)
    old_asset = MaterialAsset(
        org_id=admin.org_id,
        content_item_id=article.id,
        kind="image",
        provider="fake-provider",
        status=MaterialStatus.READY,
        size_bytes=len(_ONE_PIXEL_PNG),
    )
    replacement = MaterialAsset(
        org_id=admin.org_id,
        content_item_id=article.id,
        kind="image",
        provider="user_upload",
        status=MaterialStatus.READY,
        size_bytes=len(_ONE_PIXEL_PNG),
    )
    other_article, _other_slot = await _article_with_slot(session, admin)
    foreign_asset = MaterialAsset(
        org_id=admin.org_id,
        content_item_id=other_article.id,
        kind="image",
        provider="user_upload",
        status=MaterialStatus.READY,
        size_bytes=len(_ONE_PIXEL_PNG),
    )
    session.add_all([old_asset, replacement, foreign_asset])
    await session.flush()
    slot.selected_material_id = old_asset.id
    await session.commit()
    original_lock_version = slot.lock_version
    service = WechatArticleImageService(session=session, user=admin, provider=_FakeProvider())

    selected = await service.select_material(
        article.id,
        slot.id,
        replacement.id,
        expected_lock_version=original_lock_version,
    )

    assert selected is not None
    assert selected.status == ArticleImageSlotStatus.SELECTED
    assert selected.selected_material_id == replacement.id
    assert selected.lock_version == original_lock_version + 2
    assert await session.get(MaterialAsset, old_asset.id) is old_asset
    image_selected = await session.scalar(
        select(Event).where(Event.type == "wechat.images.image_selected").order_by(Event.id.desc())
    )
    assert image_selected is not None
    assert image_selected.payload == {
        "account_id": slot.account_id,
        "article_id": article.id,
        "slot_id": slot.id,
        "material_id": replacement.id,
        "selection_source": "existing_material",
    }
    interaction = await session.scalar(
        select(Event)
        .where(Event.type == "wechat.article.key_interaction_recorded")
        .order_by(Event.id.desc())
    )
    assert interaction is not None
    assert interaction.payload == {
        "account_id": slot.account_id,
        "article_id": article.id,
        "interaction_type": "image_selected",
        "count": 1,
    }

    with pytest.raises(ImageGenerationScopeError):
        await service.select_material(
            article.id,
            slot.id,
            foreign_asset.id,
            expected_lock_version=selected.lock_version,
        )


@pytest.mark.asyncio
async def test_prompt_is_hidden_by_default_and_authorized_read_appends_audit_event(
    client, session, admin, member
):
    """Prompt text is opt-in, scoped to the article, and every successful read is audited."""
    article, slot = await _article_with_slot(session, admin)
    admin_headers = await _headers(client)
    member_headers = await _headers(client, email="user@test.com", password="user-pw-123")

    working_copy = await client.get(
        f"/wechat-articles/{article.id}/working-copy", headers=admin_headers
    )
    hidden = await client.get(
        f"/wechat-articles/{article.id}/image-slots/{slot.id}/prompt",
        headers=member_headers,
    )
    revealed = await client.get(
        f"/wechat-articles/{article.id}/image-slots/{slot.id}/prompt",
        headers=admin_headers,
    )

    assert working_copy.status_code == 200
    assert working_copy.json()["imageSlots"] == [
        {
            "id": slot.id,
            "stableKey": "planned",
            "purpose": "Explain the planned image.",
            "aspectRatio": "3:2",
            "visualBrief": "A practical product detail.",
            "status": "planned",
            "selectedMaterialId": None,
            "lockVersion": 1,
            "hasPrompt": True,
        }
    ]
    assert all("prompt" not in slot_data for slot_data in working_copy.json()["imageSlots"])
    assert hidden.status_code == 404
    assert revealed.status_code == 200
    assert revealed.json() == {"prompt": "Create a practical product detail."}
    audit = await session.scalar(
        select(Event).where(Event.type == "wechat.article_image_prompt.accessed")
    )
    assert audit is not None
    assert audit.org_id == admin.org_id
    assert audit.account_id == slot.account_id
    assert audit.content_item_id == article.id
    assert audit.payload == {"action": "prompt_read", "status": "accessed"}


def _encoded_image(format_name: str, size: tuple[int, int] = (8, 8)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color=(20, 40, 60)).save(output, format=format_name)
    return output.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "media_type"),
    [
        (b"not-an-image", "image/png"),
        (_encoded_image("PNG")[:-8], "image/png"),
        (_encoded_image("JPEG"), "image/png"),
        (b"x" * (MAX_IMAGE_UPLOAD_BYTES + 1), "image/png"),
    ],
    ids=["spoofed", "truncated", "mime-format-mismatch", "byte-limit"],
)
async def test_upload_rejects_invalid_encoded_images_without_writing_file(
    session, admin, tmp_path, monkeypatch, content, media_type
):
    """Untrusted bytes must pass strict Pillow decode and declared MIME validation."""
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    article, slot = await _article_with_slot(session, admin, status=ArticleImageSlotStatus.READY)
    service = WechatArticleImageService(session=session, user=admin, provider=_FakeProvider())

    with pytest.raises(ImageUploadError):
        await service.upload_image(article.id, slot.id, content=content, media_type=media_type)

    assert list(tmp_path.rglob("*")) == []


@pytest.mark.asyncio
async def test_upload_rejects_decompression_bomb_and_excessive_dimensions(
    session, admin, tmp_path, monkeypatch
):
    """Pillow bomb warnings and decoded dimension caps are hard validation failures."""
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    article, slot = await _article_with_slot(session, admin, status=ArticleImageSlotStatus.READY)
    service = WechatArticleImageService(session=session, user=admin, provider=_FakeProvider())
    content = _encoded_image("PNG", size=(32, 32))
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)

    with pytest.raises(ImageUploadError):
        await service.upload_image(article.id, slot.id, content=content, media_type="image/png")

    assert list(tmp_path.rglob("*")) == []


@pytest.mark.asyncio
async def test_upload_rejects_decoded_dimensions_beyond_service_cap(
    session, admin, tmp_path, monkeypatch
):
    """Decoded width/height and total pixels are enforced independently of Pillow defaults."""
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    monkeypatch.setattr("app.services.image_generation.MAX_IMAGE_DIMENSION", 16)
    article, slot = await _article_with_slot(session, admin, status=ArticleImageSlotStatus.READY)
    service = WechatArticleImageService(session=session, user=admin, provider=_FakeProvider())

    with pytest.raises(ImageUploadError):
        await service.upload_image(
            article.id,
            slot.id,
            content=_encoded_image("PNG", size=(17, 2)),
            media_type="image/png",
        )

    assert list(tmp_path.rglob("*")) == []


@pytest.mark.asyncio
async def test_upload_rejects_cross_scope_slot_and_cleans_file_on_database_failure(
    session, admin, tmp_path, monkeypatch
):
    """Scope checks precede writes and a failed DB commit removes the atomically moved file."""
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    article, slot = await _article_with_slot(session, admin, status=ArticleImageSlotStatus.READY)
    other_article, other_slot = await _article_with_slot(session, admin)
    service = WechatArticleImageService(session=session, user=admin, provider=_FakeProvider())
    content = _encoded_image("PNG")

    with pytest.raises(ImageGenerationScopeError):
        await service.upload_image(
            article.id, other_slot.id, content=content, media_type="image/png"
        )
    assert list(tmp_path.rglob("*")) == []

    async def fail_commit() -> None:
        raise IntegrityError("COMMIT", {}, Exception("simulated database failure"))

    monkeypatch.setattr(session, "commit", fail_commit)
    with pytest.raises(IntegrityError):
        await service.upload_image(article.id, slot.id, content=content, media_type="image/png")

    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []


@pytest.mark.asyncio
async def test_upload_reencodes_without_exif_and_selects_ready_user_asset(
    session, admin, tmp_path, monkeypatch
):
    """Accepted uploads become READY selected assets under UUID paths without metadata."""
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    article, slot = await _article_with_slot(session, admin)
    original_lock_version = slot.lock_version
    source = BytesIO()
    Image.new("RGB", (8, 8), color=(10, 20, 30)).save(
        source, format="JPEG", exif=b"Exif\x00\x00test-metadata"
    )
    service = WechatArticleImageService(session=session, user=admin, provider=_FakeProvider())

    asset = await service.upload_image(
        article.id,
        slot.id,
        content=source.getvalue(),
        media_type="image/jpeg",
    )

    assert asset.org_id == admin.org_id
    assert asset.content_item_id == article.id
    assert asset.kind == "image"
    assert asset.provider == "user_upload"
    assert asset.status == MaterialStatus.READY
    assert asset.local_path is not None
    stored_path = tmp_path / Path(asset.local_path)
    assert stored_path.is_file()
    assert "test-metadata" not in stored_path.read_bytes().decode("latin1")
    await session.refresh(slot)
    assert slot.status == ArticleImageSlotStatus.SELECTED
    assert slot.selected_material_id == asset.id
    assert slot.lock_version == original_lock_version + 2
    image_selected = await session.scalar(
        select(Event).where(Event.type == "wechat.images.image_selected").order_by(Event.id.desc())
    )
    assert image_selected is not None
    assert image_selected.payload == {
        "account_id": slot.account_id,
        "article_id": article.id,
        "slot_id": slot.id,
        "material_id": asset.id,
        "selection_source": "upload",
    }


@pytest.mark.asyncio
async def test_image_action_endpoints_map_validation_scope_and_conflicts(
    client, session, admin, tmp_path, monkeypatch
):
    """HTTP actions expose stable 404/409/422 errors without raw provider/Pillow details."""
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    article, slot = await _article_with_slot(session, admin, status=ArticleImageSlotStatus.READY)
    article_id = article.id
    slot_id = slot.id
    slot_lock_version = slot.lock_version
    headers = await _headers(client)

    invalid_upload = await client.post(
        f"/wechat-articles/{article_id}/image-slots/{slot_id}/uploads",
        headers=headers,
        files={"file": ("../../attacker-name.png", b"not-an-image", "image/png")},
    )
    hidden_upload = await client.post(
        f"/wechat-articles/{article_id}/image-slots/999999/uploads",
        headers=headers,
        files={"file": ("ignored.png", _encoded_image("PNG"), "image/png")},
    )
    conflict_selection = await client.put(
        f"/wechat-articles/{article_id}/image-slots/{slot_id}/selection",
        headers=headers,
        json={"material_id": 999999, "expected_lock_version": slot_lock_version},
    )
    malformed_generation = await client.post(
        f"/wechat-articles/{article_id}/image-slots/{slot_id}/generations",
        headers=headers,
        json={"idempotency_key": "short"},
    )

    assert invalid_upload.status_code == 422
    assert invalid_upload.json()["detail"] == "invalid_image_upload"
    assert "pillow" not in str(invalid_upload.json()).lower()
    assert "attacker-name" not in str(invalid_upload.json()).lower()
    assert hidden_upload.status_code == 404
    assert conflict_selection.status_code == 404
    assert malformed_generation.status_code == 422
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []


@pytest.mark.asyncio
async def test_generation_endpoint_uses_injected_provider_and_unconfigured_is_503(
    client, session, admin, monkeypatch
):
    """The HTTP boundary supports a real provider factory and fails closed when absent."""
    article, slot = await _article_with_slot(session, admin)
    article_id = article.id
    slot_id = slot.id
    headers = await _headers(client)
    provider = _FakeProvider()
    monkeypatch.setattr(
        "app.api.wechat_articles.get_image_generation_provider",
        lambda: provider,
    )

    generated = await client.post(
        f"/wechat-articles/{article_id}/image-slots/{slot_id}/generations",
        headers=headers,
        json={"idempotency_key": "api-single-image"},
    )

    assert generated.status_code == 200
    assert generated.json()["requestedSlotIds"] == [slot_id]
    assert len(generated.json()["materialIds"]) == 1
    assert len(provider.calls) == 1

    monkeypatch.setattr(
        "app.api.wechat_articles.get_image_generation_provider",
        lambda: None,
    )
    unavailable = await client.post(
        f"/wechat-articles/{article_id}/image-generations",
        headers=headers,
        json={"idempotency_key": "unconfigured-provider"},
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"] == "image_generation_unavailable"


@pytest.mark.asyncio
async def test_generation_endpoint_sanitizes_provider_failure(client, session, admin, monkeypatch):
    """A provider exception becomes a failed operation without exposing its original message."""
    article, slot = await _article_with_slot(session, admin)
    headers = await _headers(client)
    monkeypatch.setattr(
        "app.api.wechat_articles.get_image_generation_provider",
        lambda: _FakeProvider(error=RuntimeError("api_key=provider-secret raw response")),
    )

    response = await client.post(
        f"/wechat-articles/{article.id}/image-slots/{slot.id}/generations",
        headers=headers,
        json={"idempotency_key": "provider-api-failure"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "requestedSlotIds": [slot.id],
        "materialIds": [],
        "failedSlotIds": [slot.id],
    }
    assert "provider-secret" not in str(response.json())
