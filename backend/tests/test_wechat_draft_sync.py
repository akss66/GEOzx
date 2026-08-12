"""Durable and idempotent WeChat draft synchronization contracts."""

import importlib
import inspect
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.config import settings
from app.models import (
    Account,
    ArticleImageSlot,
    ConversationThread,
    ConversationTurn,
    Event,
    MaterialAsset,
    PlatformPublishJob,
    WechatDraftMapping,
)
from app.models.enums import ArticleImageSlotStatus, MaterialStatus, Platform
from app.models.publishing import (
    PlatformPublishJobOperationType,
    PlatformPublishJobStatus,
)
from app.schemas.platform import CapabilityState, WechatCapabilitySnapshot
from app.schemas.publishing import SyncWechatDraftRequest, WechatDraftSyncOut
from app.services.publishing import (
    PublishingServiceError,
    execute_wechat_draft_sync_job,
    prepare_wechat_draft_sync_job,
)
from app.services.wechat_articles import create_article
from app.services.wechat_component import WechatIntegrationError
from app.services.wechat_drafts import WechatDraftIntegrationError


def _document(*, body_slot_keys: tuple[str, ...] = ()) -> dict:
    return {
        "title": "Safe window film guide",
        "digest": "Evidence-grounded installation guidance.",
        "author": "Editorial team",
        "blocks": [
            {
                "type": "paragraph",
                "block_id": "intro",
                "text": "Measure the glass before choosing a film.",
            },
            *[
                {
                    "type": "imageSlot",
                    "block_id": f"image-{slot_key}",
                    "slot_key": slot_key,
                }
                for slot_key in body_slot_keys
            ],
        ],
    }


async def _article_with_selected_cover(
    session,
    admin,
    tmp_path: Path,
    *,
    body_slot_keys: tuple[str, ...] = (),
):
    settings.storage_local_dir = str(tmp_path)
    account = Account(
        org_id=admin.org_id,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="WeChat account",
    )
    session.add(account)
    await session.commit()
    created = await create_article(
        session,
        admin,
        account_id=account.id,
        document=_document(body_slot_keys=body_slot_keys),
    )
    assert created is not None
    article, _working_copy, version = created
    relative_path = Path("wechat-images") / str(admin.org_id) / str(article.id) / "cover.png"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"safe-image-bytes")
    material = MaterialAsset(
        org_id=admin.org_id,
        content_item_id=article.id,
        kind="image",
        provider="user_upload",
        status=MaterialStatus.READY,
        local_path=relative_path.as_posix(),
        size_bytes=target.stat().st_size,
    )
    session.add(material)
    await session.flush()
    session.add(
        ArticleImageSlot(
            content_item_id=article.id,
            account_id=account.id,
            stable_key="cover",
            purpose="Article cover",
            aspect_ratio="2.35:1",
            visual_brief="A clean architectural window",
            status=ArticleImageSlotStatus.SELECTED,
            selected_material_id=material.id,
        )
    )
    for body_slot_key in body_slot_keys:
        body_path = (
            Path("wechat-images") / str(admin.org_id) / str(article.id) / f"{body_slot_key}.png"
        )
        body_target = tmp_path / body_path
        body_target.write_bytes(f"safe-{body_slot_key}-bytes".encode())
        body_material = MaterialAsset(
            org_id=admin.org_id,
            content_item_id=article.id,
            kind="image",
            provider="user_upload",
            status=MaterialStatus.READY,
            local_path=body_path.as_posix(),
            size_bytes=body_target.stat().st_size,
        )
        session.add(body_material)
        await session.flush()
        session.add(
            ArticleImageSlot(
                content_item_id=article.id,
                account_id=account.id,
                stable_key=body_slot_key,
                purpose=f"Body illustration {body_slot_key}",
                aspect_ratio="16:9",
                visual_brief=f"Illustration {body_slot_key}",
                status=ArticleImageSlotStatus.SELECTED,
                selected_material_id=body_material.id,
            )
        )
    await session.commit()
    return account, article, version


def _capabilities(account_id: int) -> WechatCapabilitySnapshot:
    enabled = CapabilityState(can_use=True)
    return WechatCapabilitySnapshot(
        account_id=account_id,
        upload_article_image=enabled,
        add_permanent_material=enabled,
        draft_add=enabled,
        draft_get=enabled,
        draft_update=enabled,
        analytics=CapabilityState(can_use=False, reason="not_required"),
        freepublish=CapabilityState(can_use=False, reason="disabled_by_product_policy"),
        checked_at=datetime.now(UTC),
    )


class _TokenProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def get_authorizer_access_token(self, _session, _account_id: int) -> str:
        self.calls += 1
        return "test-authorizer-token"


class _DraftClient:
    def __init__(self) -> None:
        self.cover_calls = 0
        self.add_calls = 0
        self.body_calls = 0

    async def upload_article_image(self, **_kwargs) -> str:
        self.body_calls += 1
        return f"https://mmbiz.qpic.cn/body-{self.body_calls}.png"

    async def add_permanent_cover(self, **_kwargs) -> str:
        self.cover_calls += 1
        return "cover-media-1"

    async def add_draft(self, **_kwargs) -> str:
        self.add_calls += 1
        return "draft-media-1"


class _FailSecondBodyOnceClient(_DraftClient):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    async def upload_article_image(self, **_kwargs) -> str:
        self.body_calls += 1
        if self.body_calls == 2 and not self.failed:
            self.failed = True
            error = WechatDraftIntegrationError(
                "WeChat service unavailable",
                code="http_503",
                retryable=True,
                rid="safe-rid",
                endpoint="/cgi-bin/media/uploadimg",
            )
            error.errmsg = "service unavailable"
            raise error
        return f"https://mmbiz.qpic.cn/body-{self.body_calls}.png"


class _RemoteDraftClient(_DraftClient):
    def __init__(self, remote_article) -> None:
        super().__init__()
        self.remote_article = remote_article
        self.get_calls = 0
        self.update_calls = 0
        self.updated_media_ids: list[str] = []

    async def get_draft(self, **_kwargs):
        from app.schemas.wechat_article import WechatRemoteDraft

        self.get_calls += 1
        return WechatRemoteDraft(news_item=[self.remote_article.model_dump(mode="json")])

    async def update_draft(self, **kwargs) -> None:
        self.update_calls += 1
        self.updated_media_ids.append(kwargs["media_id"])


def test_wechat_draft_sync_model_contract_is_explicit() -> None:
    """The shared ledger must distinguish legacy publishing from WeChat draft sync."""
    assert PlatformPublishJobOperationType.LEGACY_DOUYIN_PUBLISH.value == "legacy_douyin_publish"
    assert PlatformPublishJobOperationType.WECHAT_DRAFT_SYNC.value == "draft_sync"
    assert PlatformPublishJobStatus.WECHAT_QUEUED.value == "wechat_queued"
    assert PlatformPublishJobStatus.WECHAT_RUNNING.value == "wechat_running"
    assert PlatformPublishJobStatus.WECHAT_SYNCED.value == "wechat_synced"
    assert PlatformPublishJobStatus.WECHAT_CONFLICT.value == "wechat_conflict"
    assert PlatformPublishJobStatus.WECHAT_BLOCKED.value == "wechat_blocked"
    assert (
        PlatformPublishJobStatus.WECHAT_RECONCILIATION_REQUIRED.value
        == "wechat_reconciliation_required"
    )
    assert "operation_type" in PlatformPublishJob.__table__.columns
    assert "article_version_id" in PlatformPublishJob.__table__.columns
    assert "external_media_id" in PlatformPublishJob.__table__.columns
    assert "request_digest" in PlatformPublishJob.__table__.columns


def test_wechat_draft_sync_request_is_strict_and_safe_output_is_allowlisted() -> None:
    request = SyncWechatDraftRequest(
        article_version_id=7,
        idempotency_key="sync-request-7",
        expected_remote_hash=None,
        conflict_strategy="fail",
    )

    assert request.conflict_strategy == "fail"
    with pytest.raises(ValidationError):
        SyncWechatDraftRequest.model_validate(
            {
                **request.model_dump(),
                "access_token": "must-never-be-accepted",
            }
        )
    assert set(WechatDraftSyncOut.model_fields) == {
        "id",
        "account_id",
        "article_id",
        "article_version_id",
        "status",
        "conflict_strategy",
        "external_media_id",
        "expected_remote_hash",
        "observed_remote_hash",
        "retryable",
        "error_code",
        "created_at",
        "updated_at",
    }


@pytest.mark.asyncio
async def test_prepare_sync_freezes_exact_version_and_replays_same_digest(
    session, admin, tmp_path
) -> None:
    account, article, version = await _article_with_selected_cover(session, admin, tmp_path)
    request = SyncWechatDraftRequest(
        article_version_id=version.id,
        idempotency_key="wechat-sync-one",
        conflict_strategy="fail",
    )

    first = await prepare_wechat_draft_sync_job(
        session,
        admin,
        article_id=article.id,
        request=request,
    )
    replay = await prepare_wechat_draft_sync_job(
        session,
        admin,
        article_id=article.id,
        request=request,
    )

    assert replay.id == first.id
    assert first.account_id == account.id
    assert first.article_version_id == version.id
    assert first.operation_type is PlatformPublishJobOperationType.WECHAT_DRAFT_SYNC
    assert first.status is PlatformPublishJobStatus.WECHAT_QUEUED
    assert len(first.request_digest or "") == 64
    assert first.approval_snapshot == {
        "actor_id": admin.id,
        "account_id": account.id,
        "article_id": article.id,
        "article_version_id": version.id,
        "conflict_strategy": "fail",
        "expected_remote_hash": None,
        "request_digest": first.request_digest,
        "approved_at": first.approval_snapshot["approved_at"],
    }
    serialized_job = str(first.publish_package)
    assert "safe-image-bytes" not in serialized_job
    assert "storage_local_dir" not in serialized_job
    assert "<p>" not in serialized_job
    assert len(list(await session.scalars(select(PlatformPublishJob)))) == 1


@pytest.mark.asyncio
async def test_same_idempotency_key_with_changed_request_conflicts_before_external_work(
    session, admin, tmp_path
) -> None:
    _account, article, version = await _article_with_selected_cover(session, admin, tmp_path)
    await prepare_wechat_draft_sync_job(
        session,
        admin,
        article_id=article.id,
        request=SyncWechatDraftRequest(
            article_version_id=version.id,
            idempotency_key="wechat-sync-conflict",
            conflict_strategy="fail",
        ),
    )

    with pytest.raises(PublishingServiceError) as caught:
        await prepare_wechat_draft_sync_job(
            session,
            admin,
            article_id=article.id,
            request=SyncWechatDraftRequest(
                article_version_id=version.id,
                idempotency_key="wechat-sync-conflict",
                conflict_strategy="create_new",
            ),
        )

    assert caught.value.code == "WECHAT_DRAFT_IDEMPOTENCY_CONFLICT"
    assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_execute_new_draft_records_intent_and_result_and_replay_writes_once(
    session, admin, tmp_path
) -> None:
    account, article, version = await _article_with_selected_cover(session, admin, tmp_path)
    job = await prepare_wechat_draft_sync_job(
        session,
        admin,
        article_id=article.id,
        request=SyncWechatDraftRequest(
            article_version_id=version.id,
            idempotency_key="wechat-sync-execute",
            conflict_strategy="fail",
        ),
    )
    draft_client = _DraftClient()
    token_provider = _TokenProvider()

    async def capability_probe(_session, account_id: int):
        return _capabilities(account_id)

    completed = await execute_wechat_draft_sync_job(
        session,
        admin,
        job_id=job.id,
        capability_probe=capability_probe,
        token_provider=token_provider,
        draft_client=draft_client,
    )
    replay = await execute_wechat_draft_sync_job(
        session,
        admin,
        job_id=job.id,
        capability_probe=capability_probe,
        token_provider=token_provider,
        draft_client=draft_client,
    )

    assert completed.status is PlatformPublishJobStatus.WECHAT_SYNCED
    assert replay.status is PlatformPublishJobStatus.WECHAT_SYNCED
    assert completed.external_media_id == "draft-media-1"
    assert draft_client.cover_calls == 1
    assert draft_client.add_calls == 1
    assert token_provider.calls == 1
    mapping = await session.scalar(
        select(WechatDraftMapping).where(WechatDraftMapping.content_item_id == article.id)
    )
    assert mapping is not None
    assert mapping.media_id == "draft-media-1"
    assert mapping.last_synced_deliverable_id == version.id
    events = list(
        await session.scalars(
            select(Event)
            .where(Event.org_id == admin.org_id, Event.content_item_id == article.id)
            .order_by(Event.id)
        )
    )
    sync_events = [event for event in events if event.type.startswith("wechat.draft_sync.")]
    assert [event.type for event in sync_events] == [
        "wechat.draft_sync.intent",
        "wechat.draft_sync.result",
        "wechat.draft_sync.intent",
        "wechat.draft_sync.result",
    ]
    assert all(len(event.idempotency_key or "") == 64 for event in sync_events)
    assert all("test-authorizer-token" not in str(event.payload) for event in sync_events)
    assert all("<p>" not in str(event.payload) for event in sync_events)


@pytest.mark.asyncio
async def test_intent_without_result_requires_reconciliation_and_never_repeats_write(
    session, admin, tmp_path
) -> None:
    account, article, version = await _article_with_selected_cover(session, admin, tmp_path)
    job = await prepare_wechat_draft_sync_job(
        session,
        admin,
        article_id=article.id,
        request=SyncWechatDraftRequest(
            article_version_id=version.id,
            idempotency_key="wechat-sync-ambiguous",
            conflict_strategy="fail",
        ),
    )
    job.status = PlatformPublishJobStatus.WECHAT_RUNNING
    session.add(
        Event(
            type="wechat.draft_sync.intent",
            org_id=admin.org_id,
            account_id=account.id,
            content_item_id=article.id,
            idempotency_key=__import__("hashlib")
            .sha256(
                (
                    f"wechat-draft-sync:{job.id}:{job.request_digest}:attempt:0:cover_upload:intent"
                ).encode()
            )
            .hexdigest(),
            payload={
                "operation": "cover_upload",
                "status": "committed",
                "job_id": job.id,
                "attempt": 0,
            },
        )
    )
    await session.commit()
    draft_client = _DraftClient()
    token_provider = _TokenProvider()

    async def capability_probe(_session, account_id: int):
        return _capabilities(account_id)

    with pytest.raises(PublishingServiceError) as caught:
        await execute_wechat_draft_sync_job(
            session,
            admin,
            job_id=job.id,
            capability_probe=capability_probe,
            token_provider=token_provider,
            draft_client=draft_client,
        )

    assert caught.value.code == "WECHAT_DRAFT_RECONCILIATION_REQUIRED"
    await session.refresh(job)
    assert job.status is PlatformPublishJobStatus.WECHAT_RECONCILIATION_REQUIRED
    assert draft_client.cover_calls == 0
    assert draft_client.add_calls == 0


@pytest.mark.asyncio
async def test_body_images_upload_before_render_cover_and_draft_and_replay_skips_them(
    session, admin, tmp_path
) -> None:
    account, article, version = await _article_with_selected_cover(
        session,
        admin,
        tmp_path,
        body_slot_keys=("body-a", "body-b"),
    )
    job = await prepare_wechat_draft_sync_job(
        session,
        admin,
        article_id=article.id,
        request=SyncWechatDraftRequest(
            article_version_id=version.id,
            idempotency_key="wechat-sync-body-images",
            conflict_strategy="fail",
        ),
    )
    client = _DraftClient()
    token_provider = _TokenProvider()

    async def capability_probe(_session, account_id: int):
        return _capabilities(account_id)

    first = await execute_wechat_draft_sync_job(
        session,
        admin,
        job_id=job.id,
        capability_probe=capability_probe,
        token_provider=token_provider,
        draft_client=client,
    )
    replay = await execute_wechat_draft_sync_job(
        session,
        admin,
        job_id=job.id,
        capability_probe=capability_probe,
        token_provider=token_provider,
        draft_client=client,
    )

    assert first.status is PlatformPublishJobStatus.WECHAT_SYNCED
    assert replay.id == first.id
    assert client.body_calls == 2
    assert client.cover_calls == 1
    assert client.add_calls == 1
    progress = first.publish_package["progress"]
    assert set(progress["body_urls"]) == {"body-a", "body-b"}
    assert all(url.startswith("https://mmbiz.qpic.cn/") for url in progress["body_urls"].values())
    assert "<img" not in str(first.publish_package)


@pytest.mark.asyncio
async def test_retryable_body_failure_persists_completed_progress_and_does_not_repeat_it(
    session, admin, tmp_path
) -> None:
    _account, article, version = await _article_with_selected_cover(
        session,
        admin,
        tmp_path,
        body_slot_keys=("body-a", "body-b"),
    )
    job = await prepare_wechat_draft_sync_job(
        session,
        admin,
        article_id=article.id,
        request=SyncWechatDraftRequest(
            article_version_id=version.id,
            idempotency_key="wechat-sync-body-resume",
            conflict_strategy="fail",
        ),
    )
    client = _FailSecondBodyOnceClient()
    token_provider = _TokenProvider()

    async def capability_probe(_session, account_id: int):
        return _capabilities(account_id)

    with pytest.raises(PublishingServiceError) as caught:
        await execute_wechat_draft_sync_job(
            session,
            admin,
            job_id=job.id,
            capability_probe=capability_probe,
            token_provider=token_provider,
            draft_client=client,
        )

    assert caught.value.retryable is True
    await session.refresh(job)
    assert job.status is PlatformPublishJobStatus.WECHAT_QUEUED
    assert set(job.publish_package["progress"]["body_urls"]) == {"body-a"}
    completed = await execute_wechat_draft_sync_job(
        session,
        admin,
        job_id=job.id,
        capability_probe=capability_probe,
        token_provider=token_provider,
        draft_client=client,
    )
    assert completed.status is PlatformPublishJobStatus.WECHAT_SYNCED
    assert client.body_calls == 3
    assert client.cover_calls == 1
    assert client.add_calls == 1


@pytest.mark.asyncio
async def test_second_worker_cannot_join_same_running_attempt(session, admin, tmp_path) -> None:
    _account, article, version = await _article_with_selected_cover(session, admin, tmp_path)
    job = await prepare_wechat_draft_sync_job(
        session,
        admin,
        article_id=article.id,
        request=SyncWechatDraftRequest(
            article_version_id=version.id,
            idempotency_key="wechat-sync-concurrent-worker",
            conflict_strategy="fail",
        ),
    )
    job.status = PlatformPublishJobStatus.WECHAT_RUNNING
    job.retry_count = 1
    await session.commit()
    client = _DraftClient()

    async def capability_probe(_session, account_id: int):
        return _capabilities(account_id)

    with pytest.raises(PublishingServiceError) as caught:
        await execute_wechat_draft_sync_job(
            session,
            admin,
            job_id=job.id,
            capability_probe=capability_probe,
            token_provider=_TokenProvider(),
            draft_client=client,
        )

    assert caught.value.code == "WECHAT_DRAFT_SYNC_ALREADY_RUNNING"
    assert client.body_calls == 0
    assert client.cover_calls == 0
    assert client.add_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["fail", "overwrite_confirmed"])
async def test_existing_mapping_updates_only_when_fresh_remote_hash_matches_confirmation(
    session, admin, tmp_path, strategy
) -> None:
    from app.schemas.wechat_article import WechatDraftArticle
    from app.services.wechat_drafts import compute_remote_hash

    account, article, version = await _article_with_selected_cover(session, admin, tmp_path)
    remote_article = WechatDraftArticle(
        title="Remote title",
        author="Remote author",
        digest="Remote digest",
        content="<p>Remote body</p>",
        thumb_media_id="remote-cover",
        need_open_comment=1,
        only_fans_can_comment=0,
        content_source_url=None,
    )
    remote_hash = compute_remote_hash(remote_article)
    session.add(
        WechatDraftMapping(
            org_id=admin.org_id,
            account_id=account.id,
            content_item_id=article.id,
            media_id="mapped-media",
            remote_hash=remote_hash,
            last_synced_deliverable_id=version.id,
        )
    )
    await session.commit()
    job = await prepare_wechat_draft_sync_job(
        session,
        admin,
        article_id=article.id,
        request=SyncWechatDraftRequest(
            article_version_id=version.id,
            idempotency_key=f"wechat-sync-update-{strategy}",
            expected_remote_hash=remote_hash,
            conflict_strategy=strategy,
        ),
    )
    client = _RemoteDraftClient(remote_article)

    async def capability_probe(_session, account_id: int):
        return _capabilities(account_id)

    completed = await execute_wechat_draft_sync_job(
        session,
        admin,
        job_id=job.id,
        capability_probe=capability_probe,
        token_provider=_TokenProvider(),
        draft_client=client,
    )

    assert completed.status is PlatformPublishJobStatus.WECHAT_SYNCED
    assert completed.external_media_id == "mapped-media"
    assert client.get_calls == 1
    assert client.update_calls == 1
    assert client.add_calls == 0
    assert client.updated_media_ids == ["mapped-media"]


@pytest.mark.asyncio
async def test_stale_overwrite_confirmation_persists_conflict_without_update(
    session, admin, tmp_path
) -> None:
    from app.schemas.wechat_article import WechatDraftArticle

    account, article, version = await _article_with_selected_cover(session, admin, tmp_path)
    session.add(
        WechatDraftMapping(
            org_id=admin.org_id,
            account_id=account.id,
            content_item_id=article.id,
            media_id="mapped-media",
            remote_hash="stored-hash",
            last_synced_deliverable_id=version.id,
        )
    )
    await session.commit()
    job = await prepare_wechat_draft_sync_job(
        session,
        admin,
        article_id=article.id,
        request=SyncWechatDraftRequest(
            article_version_id=version.id,
            idempotency_key="wechat-sync-stale-confirmation",
            expected_remote_hash="stale-confirmation",
            conflict_strategy="overwrite_confirmed",
        ),
    )
    remote_article = WechatDraftArticle(
        title="Changed remote title",
        author="Remote author",
        digest="Remote digest",
        content="<p>Changed remote body</p>",
        thumb_media_id="remote-cover",
        need_open_comment=1,
        only_fans_can_comment=0,
        content_source_url=None,
    )
    client = _RemoteDraftClient(remote_article)

    async def capability_probe(_session, account_id: int):
        return _capabilities(account_id)

    with pytest.raises(PublishingServiceError) as caught:
        await execute_wechat_draft_sync_job(
            session,
            admin,
            job_id=job.id,
            capability_probe=capability_probe,
            token_provider=_TokenProvider(),
            draft_client=client,
        )

    assert caught.value.code == "WECHAT_DRAFT_CONFLICT"
    assert caught.value.details == {
        "syncId": job.id,
        "observedRemoteHash": job.observed_remote_hash,
    }
    await session.refresh(job)
    assert job.status is PlatformPublishJobStatus.WECHAT_CONFLICT
    assert client.get_calls == 1
    assert client.update_calls == 0
    assert client.add_calls == 0


@pytest.mark.asyncio
async def test_create_new_never_updates_old_media_and_replaces_mapping_after_add(
    session, admin, tmp_path
) -> None:
    account, article, version = await _article_with_selected_cover(session, admin, tmp_path)
    mapping = WechatDraftMapping(
        org_id=admin.org_id,
        account_id=account.id,
        content_item_id=article.id,
        media_id="old-media",
        remote_hash="old-hash",
        last_synced_deliverable_id=version.id,
    )
    session.add(mapping)
    await session.commit()
    job = await prepare_wechat_draft_sync_job(
        session,
        admin,
        article_id=article.id,
        request=SyncWechatDraftRequest(
            article_version_id=version.id,
            idempotency_key="wechat-sync-create-new",
            conflict_strategy="create_new",
        ),
    )
    client = _DraftClient()

    async def capability_probe(_session, account_id: int):
        return _capabilities(account_id)

    completed = await execute_wechat_draft_sync_job(
        session,
        admin,
        job_id=job.id,
        capability_probe=capability_probe,
        token_provider=_TokenProvider(),
        draft_client=client,
    )

    await session.refresh(mapping)
    assert completed.status is PlatformPublishJobStatus.WECHAT_SYNCED
    assert mapping.media_id == "draft-media-1"
    assert client.add_calls == 1


async def _headers(client) -> dict[str, str]:
    login = await client.post(
        "/auth/login",
        json={"email": "admin@test.com", "password": "admin-pw-123"},
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.mark.asyncio
async def test_draft_sync_http_routes_return_only_safe_fields(
    client, session, admin, tmp_path, monkeypatch
) -> None:
    from app.api import wechat_articles as api

    account, article, version = await _article_with_selected_cover(session, admin, tmp_path)
    draft_client = _DraftClient()
    token_provider = _TokenProvider()

    async def capability_probe(_session, account_id: int):
        return _capabilities(account_id)

    monkeypatch.setattr(api, "get_wechat_capability_probe", lambda: capability_probe)
    monkeypatch.setattr(api, "get_wechat_token_provider", lambda: token_provider)
    monkeypatch.setattr(api, "get_wechat_draft_client", lambda: draft_client)
    response = await client.post(
        f"/wechat-articles/{article.id}/draft-syncs",
        headers=await _headers(client),
        json={
            "article_version_id": version.id,
            "idempotency_key": "wechat-sync-http-safe",
            "expected_remote_hash": None,
            "conflict_strategy": "fail",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body) == set(WechatDraftSyncOut.model_fields)
    assert body["status"] == "wechat_synced"
    assert "approval_snapshot" not in body
    assert "publish_package" not in body
    assert "capabilities_snapshot" not in body
    assert "test-authorizer-token" not in str(body)
    fetched = await client.get(
        f"/wechat-draft-syncs/{body['id']}",
        headers=await _headers(client),
    )
    assert fetched.status_code == 200
    assert fetched.json() == body


@pytest.mark.asyncio
async def test_unconfigured_draft_sync_returns_503_before_external_intent(
    client, session, admin, tmp_path
) -> None:
    _account, article, version = await _article_with_selected_cover(session, admin, tmp_path)
    response = await client.post(
        f"/wechat-articles/{article.id}/draft-syncs",
        headers=await _headers(client),
        json={
            "article_version_id": version.id,
            "idempotency_key": "wechat-sync-no-provider",
            "expected_remote_hash": None,
            "conflict_strategy": "fail",
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "WECHAT_DRAFT_SYNC_UNAVAILABLE"
    assert not list(
        await session.scalars(select(Event).where(Event.type == "wechat.draft_sync.intent"))
    )


def test_draft_sync_migration_is_additive_and_fail_closed_on_downgrade() -> None:
    migration = importlib.import_module("migrations.versions.20260811_0400_wechat_draft_sync_jobs")

    assert migration.revision == "20260811_0400"
    assert migration.down_revision == "20260811_0330"
    upgrade_source = inspect.getsource(migration.upgrade)
    downgrade_source = inspect.getsource(migration.downgrade)
    assert "legacy_douyin_publish" in upgrade_source
    assert "draft_sync" in upgrade_source
    assert "article_version_id" in upgrade_source
    assert "platform_publish_job_status" in upgrade_source
    assert "wechat_reconciliation_required" in inspect.getsource(migration)
    assert "platform_publish_job_operation_type" in downgrade_source
    assert "raise RuntimeError" in downgrade_source


@pytest.mark.asyncio
async def test_missing_approval_and_capability_block_before_every_external_write(
    session, admin, tmp_path
) -> None:
    account, article, version = await _article_with_selected_cover(session, admin, tmp_path)
    job = await prepare_wechat_draft_sync_job(
        session,
        admin,
        article_id=article.id,
        request=SyncWechatDraftRequest(
            article_version_id=version.id,
            idempotency_key="wechat-sync-approval-required",
            conflict_strategy="fail",
        ),
    )
    client = _DraftClient()
    probe_calls = 0

    async def capability_probe(_session, account_id: int):
        nonlocal probe_calls
        probe_calls += 1
        return _capabilities(account_id)

    job.approval_snapshot = {}
    await session.commit()
    with pytest.raises(PublishingServiceError) as missing_approval:
        await execute_wechat_draft_sync_job(
            session,
            admin,
            job_id=job.id,
            capability_probe=capability_probe,
            token_provider=_TokenProvider(),
            draft_client=client,
        )
    assert missing_approval.value.code == "WECHAT_DRAFT_APPROVAL_INVALID"
    assert probe_calls == client.cover_calls == client.add_calls == 0

    job.approval_snapshot = {
        "actor_id": admin.id,
        "account_id": account.id,
        "article_id": article.id,
        "article_version_id": version.id,
        "conflict_strategy": "fail",
        "expected_remote_hash": None,
        "request_digest": job.request_digest,
        "approved_at": datetime.now(UTC).isoformat(),
    }
    await session.commit()

    async def missing_capability(_session, account_id: int):
        snapshot = _capabilities(account_id)
        snapshot.draft_add = CapabilityState(can_use=False, reason="not_authorized")
        return snapshot

    blocked = await execute_wechat_draft_sync_job(
        session,
        admin,
        job_id=job.id,
        capability_probe=missing_capability,
        token_provider=_TokenProvider(),
        draft_client=client,
    )
    assert blocked.status is PlatformPublishJobStatus.WECHAT_BLOCKED
    assert blocked.last_error_code == "WECHAT_DRAFT_CAPABILITY_MISSING"
    assert client.cover_calls == client.add_calls == 0


@pytest.mark.asyncio
async def test_revoked_authorization_is_terminal_before_external_intent(
    session, admin, tmp_path
) -> None:
    _account, article, version = await _article_with_selected_cover(session, admin, tmp_path)
    job = await prepare_wechat_draft_sync_job(
        session,
        admin,
        article_id=article.id,
        request=SyncWechatDraftRequest(
            article_version_id=version.id,
            idempotency_key="wechat-sync-revoked-auth",
            conflict_strategy="fail",
        ),
    )

    class RevokedTokenProvider:
        async def get_authorizer_access_token(self, _session, _account_id: int) -> str:
            raise WechatIntegrationError(
                "authorization revoked",
                code="WECHAT_AUTHORIZATION_REVOKED",
                retryable=False,
                rid=None,
                endpoint="authorizer_token",
            )

    async def capability_probe(_session, account_id: int):
        return _capabilities(account_id)

    client = _DraftClient()
    with pytest.raises(PublishingServiceError) as revoked:
        await execute_wechat_draft_sync_job(
            session,
            admin,
            job_id=job.id,
            capability_probe=capability_probe,
            token_provider=RevokedTokenProvider(),
            draft_client=client,
        )
    assert revoked.value.code == "WECHAT_DRAFT_AUTHORIZATION_REVOKED"
    await session.refresh(job)
    assert job.status is PlatformPublishJobStatus.WECHAT_BLOCKED
    assert client.cover_calls == client.add_calls == 0
    assert not list(
        await session.scalars(select(Event).where(Event.type == "wechat.draft_sync.intent"))
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "retryable", "retry_after", "expected_status"),
    [
        ("http_503", True, None, PlatformPublishJobStatus.WECHAT_QUEUED),
        ("http_429", True, None, PlatformPublishJobStatus.WECHAT_BLOCKED),
        ("http_429", True, 3, PlatformPublishJobStatus.WECHAT_QUEUED),
        ("permission_denied", False, None, PlatformPublishJobStatus.WECHAT_BLOCKED),
    ],
)
async def test_external_retry_classification_is_fail_closed(
    session, admin, tmp_path, code, retryable, retry_after, expected_status
) -> None:
    _account, article, version = await _article_with_selected_cover(session, admin, tmp_path)
    job = await prepare_wechat_draft_sync_job(
        session,
        admin,
        article_id=article.id,
        request=SyncWechatDraftRequest(
            article_version_id=version.id,
            idempotency_key=f"wechat-sync-retry-{code}-{retry_after}",
            conflict_strategy="fail",
        ),
    )

    class FailingCoverClient(_DraftClient):
        async def add_permanent_cover(self, **_kwargs) -> str:
            self.cover_calls += 1
            error = WechatDraftIntegrationError(
                "safe provider failure",
                code=code,
                retryable=retryable,
                rid="safe-rid",
                endpoint="/cgi-bin/material/add_material",
            )
            error.retry_after_seconds = retry_after
            raise error

    async def capability_probe(_session, account_id: int):
        return _capabilities(account_id)

    client = FailingCoverClient()
    with pytest.raises(PublishingServiceError):
        await execute_wechat_draft_sync_job(
            session,
            admin,
            job_id=job.id,
            capability_probe=capability_probe,
            token_provider=_TokenProvider(),
            draft_client=client,
        )
    await session.refresh(job)
    assert job.status is expected_status
    assert client.cover_calls == 1
    assert client.add_calls == 0


@pytest.mark.asyncio
async def test_provider_success_then_result_commit_failure_requires_reconciliation(
    session, admin, tmp_path, monkeypatch
) -> None:
    from app.services import publishing

    _account, article, version = await _article_with_selected_cover(session, admin, tmp_path)
    job = await prepare_wechat_draft_sync_job(
        session,
        admin,
        article_id=article.id,
        request=SyncWechatDraftRequest(
            article_version_id=version.id,
            idempotency_key="wechat-sync-ambiguous-cover",
            conflict_strategy="fail",
        ),
    )
    job_id = job.id
    admin_id = admin.id
    client = _DraftClient()

    async def capability_probe(_session, account_id: int):
        return _capabilities(account_id)

    original_result = publishing._commit_external_result

    async def fail_cover_result(_session, _job, *, operation: str):
        if operation == "cover_upload":
            raise RuntimeError("simulated database disconnect")
        return await original_result(_session, _job, operation=operation)

    monkeypatch.setattr(publishing, "_commit_external_result", fail_cover_result)
    with pytest.raises(RuntimeError, match="database disconnect"):
        await execute_wechat_draft_sync_job(
            session,
            admin,
            job_id=job_id,
            capability_probe=capability_probe,
            token_provider=_TokenProvider(),
            draft_client=client,
        )
    assert client.cover_calls == 1
    await session.rollback()
    admin = await session.get(type(admin), admin_id)
    monkeypatch.setattr(publishing, "_commit_external_result", original_result)
    with pytest.raises(PublishingServiceError) as ambiguous:
        await execute_wechat_draft_sync_job(
            session,
            admin,
            job_id=job_id,
            capability_probe=capability_probe,
            token_provider=_TokenProvider(),
            draft_client=client,
        )
    assert ambiguous.value.code == "WECHAT_DRAFT_RECONCILIATION_REQUIRED"
    assert client.cover_calls == 1
    reconciled = await session.get(PlatformPublishJob, job_id)
    assert reconciled.status is PlatformPublishJobStatus.WECHAT_RECONCILIATION_REQUIRED


@pytest.mark.asyncio
async def test_get_job_and_version_scope_fail_closed(session, admin, member, tmp_path) -> None:
    from app.services.publishing import get_wechat_draft_sync_job

    _account, article, version = await _article_with_selected_cover(session, admin, tmp_path)
    job = await prepare_wechat_draft_sync_job(
        session,
        admin,
        article_id=article.id,
        request=SyncWechatDraftRequest(
            article_version_id=version.id,
            idempotency_key="wechat-sync-scope-job",
            conflict_strategy="fail",
        ),
    )
    with pytest.raises(Exception) as inaccessible_job:
        await get_wechat_draft_sync_job(session, member, job.id)
    assert getattr(inaccessible_job.value, "status_code", None) == 404
    with pytest.raises(Exception) as inaccessible_version:
        await prepare_wechat_draft_sync_job(
            session,
            member,
            article_id=article.id,
            request=SyncWechatDraftRequest(
                article_version_id=version.id,
                idempotency_key="wechat-sync-scope-version",
                conflict_strategy="fail",
            ),
        )
    assert getattr(inaccessible_version.value, "status_code", None) == 404


@pytest.mark.asyncio
async def test_manual_article_does_not_fabricate_turn_events(session, admin, tmp_path) -> None:
    _account, article, version = await _article_with_selected_cover(session, admin, tmp_path)
    assert version.thread_id is None and version.turn_id is None
    job = await prepare_wechat_draft_sync_job(
        session,
        admin,
        article_id=article.id,
        request=SyncWechatDraftRequest(
            article_version_id=version.id,
            idempotency_key="wechat-sync-no-fake-turn",
            conflict_strategy="fail",
        ),
    )

    async def capability_probe(_session, account_id: int):
        return _capabilities(account_id)

    await execute_wechat_draft_sync_job(
        session,
        admin,
        job_id=job.id,
        capability_probe=capability_probe,
        token_provider=_TokenProvider(),
        draft_client=_DraftClient(),
    )
    turn_events = list(await session.scalars(select(Event).where(Event.turn_id.is_not(None))))
    assert turn_events == []


@pytest.mark.asyncio
async def test_lineaged_article_emits_idempotent_public_step_events(
    session, admin, tmp_path
) -> None:
    account, article, version = await _article_with_selected_cover(session, admin, tmp_path)
    thread = ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title="WeChat sync",
    )
    turn = ConversationTurn(
        thread=thread,
        org_id=admin.org_id,
        created_by_id=admin.id,
        user_input="同步文章到草稿箱",
    )
    session.add_all([thread, turn])
    await session.flush()
    version.thread_id = thread.id
    version.turn_id = turn.id
    await session.commit()
    job = await prepare_wechat_draft_sync_job(
        session,
        admin,
        article_id=article.id,
        request=SyncWechatDraftRequest(
            article_version_id=version.id,
            idempotency_key="wechat-sync-turn-progress",
            conflict_strategy="fail",
        ),
    )

    async def capability_probe(_session, account_id: int):
        return _capabilities(account_id)

    client = _DraftClient()
    await execute_wechat_draft_sync_job(
        session,
        admin,
        job_id=job.id,
        capability_probe=capability_probe,
        token_provider=_TokenProvider(),
        draft_client=client,
    )
    await execute_wechat_draft_sync_job(
        session,
        admin,
        job_id=job.id,
        capability_probe=capability_probe,
        token_provider=_TokenProvider(),
        draft_client=client,
    )
    events = list(
        await session.scalars(
            select(Event)
            .where(
                Event.turn_id == turn.id,
                Event.type.in_({"step.started", "step.completed"}),
            )
            .order_by(Event.sequence)
        )
    )
    assert [(event.type, event.payload["title"]) for event in events] == [
        ("step.started", "检查文章版本"),
        ("step.completed", "检查文章版本"),
        ("step.started", "检查公众号能力"),
        ("step.completed", "检查公众号能力"),
        ("step.started", "检查文章素材"),
        ("step.completed", "检查文章素材"),
        ("step.started", "检查远端草稿冲突"),
        ("step.completed", "检查远端草稿冲突"),
        ("step.started", "同步微信公众号草稿"),
        ("step.completed", "同步微信公众号草稿"),
    ]
    assert all("access_token" not in str(event.payload) for event in events)


@pytest.mark.asyncio
async def test_lineaged_capability_block_emits_idempotent_safe_step_failed(
    session, admin, tmp_path
) -> None:
    account, article, version = await _article_with_selected_cover(session, admin, tmp_path)
    thread = ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title="Blocked WeChat sync",
    )
    turn = ConversationTurn(
        thread=thread,
        org_id=admin.org_id,
        created_by_id=admin.id,
        user_input="同步文章",
    )
    session.add_all([thread, turn])
    await session.flush()
    version.thread_id = thread.id
    version.turn_id = turn.id
    await session.commit()
    job = await prepare_wechat_draft_sync_job(
        session,
        admin,
        article_id=article.id,
        request=SyncWechatDraftRequest(
            article_version_id=version.id,
            idempotency_key="wechat-sync-turn-failed",
            conflict_strategy="fail",
        ),
    )

    async def missing_capability(_session, account_id: int):
        snapshot = _capabilities(account_id)
        snapshot.draft_add = CapabilityState(can_use=False, reason="not_authorized")
        return snapshot

    client = _DraftClient()
    blocked = await execute_wechat_draft_sync_job(
        session,
        admin,
        job_id=job.id,
        capability_probe=missing_capability,
        token_provider=_TokenProvider(),
        draft_client=client,
    )
    assert blocked.status is PlatformPublishJobStatus.WECHAT_BLOCKED
    failed = list(
        await session.scalars(
            select(Event).where(
                Event.turn_id == turn.id,
                Event.type == "step.failed",
            )
        )
    )
    assert len(failed) == 1
    assert failed[0].payload == {
        "step": "capabilities",
        "title": "检查公众号能力",
        "status": "failed",
        "metadata": {"category": "wechat_draft_sync"},
        "error_code": "WECHAT_DRAFT_CAPABILITY_MISSING",
    }
    assert client.cover_calls == client.add_calls == 0


@pytest.mark.asyncio
async def test_lineaged_readiness_block_emits_safe_step_failed_before_job_or_write(
    session, admin, tmp_path
) -> None:
    account, article, version = await _article_with_selected_cover(session, admin, tmp_path)
    thread = ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title="Unready article",
    )
    turn = ConversationTurn(
        thread=thread,
        org_id=admin.org_id,
        created_by_id=admin.id,
        user_input="同步未就绪文章",
    )
    session.add_all([thread, turn])
    await session.flush()
    version.thread_id = thread.id
    version.turn_id = turn.id
    version.payload["document"]["claims"] = [
        {
            "claim_id": "unsafe-claim",
            "block_id": "intro",
            "kind": "product_fact",
            "text": "Unverified product claim",
            "citation_ids": [],
        }
    ]
    await session.commit()

    with pytest.raises(PublishingServiceError) as blocked:
        await prepare_wechat_draft_sync_job(
            session,
            admin,
            article_id=article.id,
            request=SyncWechatDraftRequest(
                article_version_id=version.id,
                idempotency_key="wechat-sync-readiness-failed",
                conflict_strategy="fail",
            ),
        )
    assert blocked.value.code == "WECHAT_ARTICLE_NOT_READY"
    failed = list(
        await session.scalars(
            select(Event).where(
                Event.turn_id == turn.id,
                Event.type == "step.failed",
            )
        )
    )
    assert len(failed) == 1
    assert failed[0].payload["step"] == "readiness"
    assert failed[0].payload["error_code"] == "WECHAT_ARTICLE_NOT_READY"
    assert await session.scalar(select(PlatformPublishJob.id)) is None
