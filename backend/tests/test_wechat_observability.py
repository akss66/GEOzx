"""Observability contracts for WeChat article production workflows."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

import app.api.platform_integrations as platform_api
from app.models import Account, Event, PlatformAccountAuth
from app.models.enums import Platform
from app.schemas.platform import CapabilityState, WechatCapabilitySnapshot
from app.schemas.publishing import SyncWechatDraftRequest
from app.schemas.wechat_article import ArticleDocument, WechatDraftArticle
from app.services.publishing import (
    PublishingServiceError,
    execute_wechat_draft_sync_job,
    prepare_wechat_draft_sync_job,
)
from app.services.wechat_articles import create_article, freeze_article_version, update_working_copy
from app.services.wechat_drafts import WechatDraftClient
from app.services.wechat_rollout_alerts import evaluate_wechat_rollout_alerts
from tests.test_wechat_draft_sync import (
    _article_with_selected_cover,
    _capabilities,
    _DraftClient,
    _FailSecondBodyOnceClient,
    _RemoteDraftClient,
    _TokenProvider,
)


def _article() -> WechatDraftArticle:
    return WechatDraftArticle.model_validate(
        {
            "title": "夏季隔热指南",
            "author": "悠护",
            "digest": "一篇有事实依据的隔热说明。",
            "content": "<p>正文</p>",
            "thumb_media_id": "cover-media-id",
            "need_open_comment": 1,
            "only_fans_can_comment": 0,
            "content_source_url": "https://example.com/source",
        }
    )


@pytest.mark.asyncio
async def test_draft_boundary_logs_redacted_failure_metadata(caplog) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "errcode": -1,
                "errmsg": "system busy access_token=leaked-token",
                "rid": "rid access_token=rid-token",
            },
        )

    caplog.set_level(logging.INFO, logger="app.services.wechat_drafts")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(RuntimeError):
            await WechatDraftClient(client=http_client).add_draft(
                access_token="authorizer-secret",
                article=_article(),
            )

    records = [
        record
        for record in caplog.records
        if record.name == "app.services.wechat_drafts"
        and getattr(record, "event_name", None) == "wechat_api_request"
    ]
    assert len(records) == 1
    record = records[0]
    assert record.endpoint == "/cgi-bin/draft/add"
    assert record.outcome == "error"
    assert record.error_code == -1
    assert record.retryable is True
    assert record.rid == "rid access_token=[redacted]"
    assert isinstance(record.duration_ms, int)
    assert record.duration_ms >= 0
    serialized = caplog.text + str(record.__dict__)
    assert "authorizer-secret" not in serialized
    assert "leaked-token" not in serialized
    assert "rid-token" not in serialized
    assert "正文" not in serialized


@pytest.mark.asyncio
async def test_create_article_records_safe_product_events(session, admin) -> None:
    account = Account(
        org_id=admin.org_id,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="Official account",
    )
    session.add(account)
    await session.commit()

    created = await create_article(
        session, admin, account_id=account.id, document=_article_document()
    )

    assert created is not None
    article, _working_copy, version = created
    events = list(
        await session.scalars(
            select(Event)
            .where(Event.org_id == admin.org_id, Event.content_item_id == article.id)
            .order_by(Event.id)
        )
    )
    assert [event.type for event in events] == [
        "wechat.article.created",
        "wechat.article.initial_draft_ready",
    ]
    assert events[0].payload == {
        "account_id": account.id,
        "article_id": article.id,
    }
    assert events[1].payload == {
        "account_id": account.id,
        "article_id": article.id,
        "article_version_id": version.id,
        "version": 1,
        "trigger": "first_ai_draft",
    }
    serialized = "".join(str(event.payload) for event in events)
    assert "正文" not in serialized
    assert "cover-media-id" not in serialized


@pytest.mark.asyncio
async def test_freeze_article_version_records_semantic_change_ratio(session, admin) -> None:
    account = Account(
        org_id=admin.org_id,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="Official account",
    )
    session.add(account)
    await session.commit()
    created = await create_article(
        session, admin, account_id=account.id, document=_article_document()
    )
    assert created is not None
    article, working_copy, _version = created

    await update_working_copy(
        session,
        admin,
        content_item_id=article.id,
        expected_lock_version=working_copy.lock_version,
        document=ArticleDocument.model_validate(
            {
                **_article_document(),
                "blocks": [
                    {
                        "type": "paragraph",
                        "block_id": "intro",
                        "text": "先完成测量，再安装隔热膜。",
                    }
                ],
            }
        ),
    )
    frozen = await freeze_article_version(
        session,
        admin,
        content_item_id=article.id,
        trigger="explicit_save_version",
    )

    assert frozen is not None
    events = list(
        await session.scalars(
            select(Event)
            .where(Event.org_id == admin.org_id, Event.content_item_id == article.id)
            .order_by(Event.id)
        )
    )
    version_saved = events[-1]
    assert version_saved.type == "wechat.article.version_saved"
    assert version_saved.payload == {
        "account_id": account.id,
        "article_id": article.id,
        "article_version_id": frozen.id,
        "version": 2,
        "trigger": "explicit_save_version",
        "text_semantic_change_ratio": 1.0,
    }


def _article_document() -> dict[str, object]:
    return {
        "title": "夏季隔热指南",
        "author": "悠护",
        "digest": "一篇有事实依据的隔热说明。",
        "blocks": [
            {
                "type": "paragraph",
                "block_id": "intro",
                "text": "先测量玻璃，再选择隔热膜。",
            }
        ],
    }


async def _headers(client) -> dict[str, str]:
    response = await client.post(
        "/auth/login", json={"email": "admin@test.com", "password": "admin-pw-123"}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.asyncio
async def test_capability_probe_records_safe_checked_event(
    client, session, admin, monkeypatch
) -> None:
    account = Account(
        org_id=admin.org_id,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="Official account",
    )
    session.add(account)
    await session.flush()
    session.add(
        PlatformAccountAuth(
            org_id=admin.org_id,
            account_id=account.id,
            platform=Platform.WECHAT_OFFICIAL_ACCOUNT.value,
            external_open_id="authorizer-appid",
            auth_status="authorized",
        )
    )
    await session.commit()

    unavailable = CapabilityState(can_use=False, reason="component_permission_missing")

    async def fake_probe(*_args, **_kwargs) -> WechatCapabilitySnapshot:
        return WechatCapabilitySnapshot(
            account_id=account.id,
            upload_article_image=unavailable,
            add_permanent_material=unavailable,
            draft_add=unavailable,
            draft_get=unavailable,
            draft_update=unavailable,
            analytics=CapabilityState(can_use=True),
            freepublish=CapabilityState(
                can_use=False,
                reason="disabled_by_product_policy",
            ),
            checked_at=datetime.now(UTC),
        )

    monkeypatch.setattr(platform_api, "probe_wechat_capabilities", fake_probe)

    response = await client.get(
        f"/accounts/{account.id}/platform-capabilities",
        headers=await _headers(client),
    )

    assert response.status_code == 200
    event = await session.scalar(
        select(Event)
        .where(
            Event.org_id == admin.org_id,
            Event.account_id == account.id,
            Event.type == "wechat.capabilities.checked",
        )
        .order_by(Event.id.desc())
    )
    assert event is not None
    assert event.payload == {
        "account_id": account.id,
        "draft_add": "component_permission_missing",
        "draft_get": "component_permission_missing",
        "draft_update": "component_permission_missing",
        "upload_article_image": "component_permission_missing",
        "add_permanent_material": "component_permission_missing",
        "analytics": "ready",
        "freepublish": "disabled_by_product_policy",
    }


@pytest.mark.asyncio
async def test_draft_sync_records_requested_and_completed_events(session, admin, tmp_path) -> None:
    _account, article, version = await _article_with_selected_cover(session, admin, tmp_path)
    job = await prepare_wechat_draft_sync_job(
        session,
        admin,
        article_id=article.id,
        request=SyncWechatDraftRequest(
            article_version_id=version.id,
            idempotency_key="wechat-sync-observability-success",
            conflict_strategy="fail",
        ),
    )

    requested = await session.scalar(
        select(Event)
        .where(
            Event.org_id == admin.org_id,
            Event.content_item_id == article.id,
            Event.type == "wechat.draft.sync_requested",
        )
        .order_by(Event.id.desc())
    )
    assert requested is not None
    assert requested.payload == {
        "account_id": job.account_id,
        "article_id": article.id,
        "article_version_id": version.id,
        "sync_id": job.id,
        "conflict_strategy": "fail",
    }

    async def capability_probe(_session, account_id: int):
        return _capabilities(account_id)

    completed = await execute_wechat_draft_sync_job(
        session,
        admin,
        job_id=job.id,
        capability_probe=capability_probe,
        token_provider=_TokenProvider(),
        draft_client=_DraftClient(),
    )

    assert completed.id == job.id
    succeeded = await session.scalar(
        select(Event)
        .where(
            Event.org_id == admin.org_id,
            Event.content_item_id == article.id,
            Event.type == "wechat.draft.sync_completed",
        )
        .order_by(Event.id.desc())
    )
    assert succeeded is not None
    assert succeeded.payload == {
        "account_id": job.account_id,
        "article_id": article.id,
        "article_version_id": version.id,
        "sync_id": job.id,
        "external_media_id": "draft-media-1",
        "retry_count": 1,
    }
    serialized = "".join(str(event.payload) for event in [requested, succeeded])
    assert "test-authorizer-token" not in serialized
    assert "<p>" not in serialized


@pytest.mark.asyncio
async def test_draft_sync_records_conflicted_event(session, admin, tmp_path) -> None:
    from app.models import WechatDraftMapping
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
            idempotency_key="wechat-sync-observability-conflict",
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

    async def capability_probe(_session, account_id: int):
        return _capabilities(account_id)

    with pytest.raises(PublishingServiceError) as caught:
        await execute_wechat_draft_sync_job(
            session,
            admin,
            job_id=job.id,
            capability_probe=capability_probe,
            token_provider=_TokenProvider(),
            draft_client=_RemoteDraftClient(remote_article),
        )
    assert caught.value.code == "WECHAT_DRAFT_CONFLICT"

    conflicted = await session.scalar(
        select(Event)
        .where(
            Event.org_id == admin.org_id,
            Event.content_item_id == article.id,
            Event.type == "wechat.draft.sync_conflicted",
        )
        .order_by(Event.id.desc())
    )
    assert conflicted is not None
    assert conflicted.payload == {
        "account_id": account.id,
        "article_id": article.id,
        "article_version_id": version.id,
        "sync_id": job.id,
        "error_code": "WECHAT_DRAFT_CONFLICT",
    }


@pytest.mark.asyncio
async def test_draft_sync_records_failed_event(session, admin, tmp_path) -> None:
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
            idempotency_key="wechat-sync-observability-failed",
            conflict_strategy="fail",
        ),
    )

    async def capability_probe(_session, account_id: int):
        return _capabilities(account_id)

    with pytest.raises(PublishingServiceError) as caught:
        await execute_wechat_draft_sync_job(
            session,
            admin,
            job_id=job.id,
            capability_probe=capability_probe,
            token_provider=_TokenProvider(),
            draft_client=_FailSecondBodyOnceClient(),
        )
    assert caught.value.code == "WECHAT_DRAFT_EXTERNAL_RETRYABLE"

    failed = await session.scalar(
        select(Event)
        .where(
            Event.org_id == admin.org_id,
            Event.content_item_id == article.id,
            Event.type == "wechat.draft.sync_failed",
        )
        .order_by(Event.id.desc())
    )
    assert failed is not None
    assert failed.payload == {
        "account_id": job.account_id,
        "article_id": article.id,
        "article_version_id": version.id,
        "sync_id": job.id,
        "error_code": "http_503",
        "retryable": "true",
    }


def test_rollout_alerts_fire_only_on_bounded_thresholds() -> None:
    alerts = evaluate_wechat_rollout_alerts(
        now=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
        component_ticket_created_at=datetime(2026, 8, 12, 11, 39, tzinfo=UTC),
        consecutive_component_refresh_failures=3,
        consecutive_authorizer_refresh_failures=0,
        draft_sync_failures_last_5m=6,
        draft_sync_attempts_last_5m=100,
        conflicting_idempotency_reuses=1,
        scope_denial_anomalies=2,
    )

    assert [alert.code for alert in alerts] == [
        "WECHAT_COMPONENT_TICKET_STALE",
        "WECHAT_COMPONENT_REFRESH_FAILURES_REPEATED",
        "WECHAT_DRAFT_SYNC_FAILURE_RATE_HIGH",
        "WECHAT_DRAFT_SYNC_IDEMPOTENCY_CONFLICT",
        "WECHAT_SCOPE_DENIAL_ANOMALY",
    ]
    assert alerts[0].context == {"ticket_age_minutes": 21}
    assert alerts[1].context == {"failure_count": 3, "token_kind": "component"}
    assert alerts[2].context == {
        "failure_count": 6,
        "attempt_count": 100,
        "failure_rate": 0.06,
        "window_minutes": 5,
    }


def test_rollout_alerts_respect_strict_non_trigger_boundaries() -> None:
    alerts = evaluate_wechat_rollout_alerts(
        now=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
        component_ticket_created_at=datetime(2026, 8, 12, 11, 40, tzinfo=UTC),
        consecutive_component_refresh_failures=2,
        consecutive_authorizer_refresh_failures=2,
        draft_sync_failures_last_5m=5,
        draft_sync_attempts_last_5m=100,
        conflicting_idempotency_reuses=0,
        scope_denial_anomalies=0,
    )

    assert alerts == []
