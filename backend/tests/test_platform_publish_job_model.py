from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Account, PlatformPublishJob
from app.models.enums import AccountStatus, Platform
from app.models.publishing import PlatformPublishJobStatus


@pytest.mark.asyncio
async def test_publish_job_defaults_to_draft_with_optional_workspace_context(
    session,
    admin,
) -> None:
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="Official publishing test",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    await session.flush()

    job = PlatformPublishJob(
        org_id=admin.org_id,
        account_id=account.id,
        platform=Platform.DOUYIN,
        idempotency_key="publish:test:1",
        publish_package={"title": "Test title", "material_urls": ["https://cdn.test/video.mp4"]},
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    assert job.status == PlatformPublishJobStatus.DRAFT
    assert job.active_client_id is None
    assert job.active_project_id is None
    assert job.share_id is None
    assert job.posting_task_id is None
    assert job.external_video_id is None
    assert job.external_item_id is None
    assert job.platform_content_record_id is None
    assert job.retry_count == 0
    assert job.capabilities_snapshot == {}
    assert job.approval_snapshot == {}


@pytest.mark.asyncio
async def test_publish_job_persists_external_identity_and_retry_metadata(
    session,
    admin,
) -> None:
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="Callback identity test",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    await session.flush()

    expires_at = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
    next_retry_at = datetime(2026, 7, 27, 12, 5, tzinfo=UTC)
    job = PlatformPublishJob(
        org_id=admin.org_id,
        account_id=account.id,
        platform=Platform.DOUYIN,
        status=PlatformPublishJobStatus.WAITING_BIND,
        idempotency_key="publish:test:identity",
        publish_package={"title": "Identity test"},
        share_id="share-1",
        posting_task_id="task-1",
        external_video_id="video-1",
        external_item_id="item-1",
        expires_at=expires_at,
        retry_count=2,
        next_retry_at=next_retry_at,
        last_error_code="douyin_timeout",
        last_error_message="The upstream request timed out.",
        last_platform_log_id="log-1",
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    assert job.status == PlatformPublishJobStatus.WAITING_BIND
    assert job.share_id == "share-1"
    assert job.posting_task_id == "task-1"
    assert job.external_video_id == "video-1"
    assert job.external_item_id == "item-1"
    assert job.retry_count == 2
    assert job.last_error_code == "douyin_timeout"
    assert job.last_platform_log_id == "log-1"


@pytest.mark.asyncio
async def test_publish_job_idempotency_key_is_unique_inside_organization(
    session,
    admin,
) -> None:
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="Idempotency test",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    await session.flush()

    common = {
        "org_id": admin.org_id,
        "account_id": account.id,
        "platform": Platform.DOUYIN,
        "idempotency_key": "publish:test:duplicate",
        "publish_package": {},
    }
    session.add(PlatformPublishJob(**common))
    await session.commit()

    session.add(PlatformPublishJob(**common))
    with pytest.raises(IntegrityError):
        await session.commit()
