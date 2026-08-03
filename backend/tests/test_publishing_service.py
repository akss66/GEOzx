from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.models import (
    Account,
    AgentToolCall,
    BrainTask,
    ContentItem,
    Deliverable,
    MaterialAsset,
    PlatformAccountAuth,
    PlatformContentRecord,
    PlatformIntegration,
)
from app.models.enums import (
    AccountStatus,
    BrainTaskStatus,
    BrainTaskType,
    DataSourceKind,
    DeliverableStatus,
    DeliverableType,
    MaterialStatus,
    Platform,
)
from app.models.publishing import PlatformPublishJob, PlatformPublishJobStatus
from app.schemas.orchestrator import PublishPackageOut
from app.schemas.publishing import CreatePublishJobRequest, DouyinCreateVideoCallback
from app.services import publishing as publishing_service
from app.services.publishing import PublishingServiceError


async def _approved_publish_artifact(
    session,
    admin,
    *,
    account,
    package,
    tool_call,
    version: int = 1,
    status=DeliverableStatus.APPROVED,
):
    content = ContentItem(
        account_id=account.id,
        created_by_id=admin.id,
        title="approved publish package",
    )
    session.add(content)
    await session.flush()
    artifact = Deliverable(
        content_item_id=content.id,
        agent_code="06-operator",
        type=DeliverableType.PUBLISH_CALENDAR,
        version=version,
        status=status,
        payload={
            "publish_package": package.model_dump(mode="json"),
            "approval_tool_call_id": tool_call.id,
        },
    )
    session.add(artifact)
    await session.commit()
    return artifact


async def _seed_publish_context(session, admin, *, approved: bool):
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        external_account_id="douyin-account-1",
        nickname="测试抖音账号",
        status=AccountStatus.ACTIVE,
        auth={"auth_status": "authorized"},
    )
    material = MaterialAsset(
        org_id=admin.org_id,
        kind="video",
        status=MaterialStatus.READY,
        source_url="https://cdn.example.test/publish/video.mp4",
    )
    integration = PlatformIntegration(
        org_id=admin.org_id,
        platform=Platform.DOUYIN.value,
        status="configured",
        auth_status="authorized",
        client_key="client-key",
        client_secret_ref="env:TEST_DOUYIN_CLIENT_SECRET",
        scopes=["h5.share", "open.get.ticket", "aweme.share"],
    )
    auth = PlatformAccountAuth(
        org_id=admin.org_id,
        account=account,
        platform=Platform.DOUYIN.value,
        external_open_id="open-id-1",
        auth_status="authorized",
        scopes=["user_info", "h5.share"],
        access_token_encrypted="encrypted-token",
    )
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="发布测试",
        type=BrainTaskType.CONTENT_CREATION,
        status=BrainTaskStatus.PENDING_ACCEPTANCE,
    )
    session.add_all([account, material, integration, auth, task])
    await session.flush()

    package = PublishPackageOut(
        platform=Platform.DOUYIN,
        account_id=account.id,
        content_type="video",
        title="一条真实发布测试",
        body="正文",
        topics=["品牌案例"],
        scheduled_at=None,
        material_ids=[material.id],
        cover_material_id=None,
        visibility="public",
        allow_comment=True,
        execution_mode="official_api",
        manual_steps=[],
    )
    tool_call = AgentToolCall(
        org_id=admin.org_id,
        task_id=task.id,
        module="content_production",
        agent_code="06-operator",
        tool_code="publish_package_prepare",
        tool_name="发布包准备",
        idempotency_key="tool-publish-1",
        status="success" if approved else "waiting_approval",
        permission_mode="confirm",
        requires_human_confirmation=True,
        input_summary="准备发布",
        output_summary="发布包已准备",
        meta={
            "publish_package": package.model_dump(mode="json"),
            "decision": {"approved": approved, "reviewed_by": admin.id},
            "publish_decision_status": (
                "approved_for_manual_publish" if approved else "pending"
            ),
        },
    )
    session.add(tool_call)
    await session.commit()
    return account, material, package, tool_call


@pytest.mark.asyncio
async def test_create_publish_job_is_idempotent_and_allows_unbound_account(
    session, admin
) -> None:
    account, _material, package, tool_call = await _seed_publish_context(
        session, admin, approved=False
    )
    request = CreatePublishJobRequest(
        account_id=account.id,
        tool_call_id=tool_call.id,
        idempotency_key="publish-request-1",
        publish_package=package,
    )

    first = await publishing_service.create_publish_job(session, admin, request)
    second = await publishing_service.create_publish_job(session, admin, request)

    assert first.id == second.id
    assert first.active_client_id is None
    assert first.active_project_id is None
    assert first.status == PlatformPublishJobStatus.PENDING_APPROVAL
    assert await session.scalar(select(func.count(PlatformPublishJob.id))) == 1


@pytest.mark.asyncio
async def test_unapproved_publish_job_cannot_start_external_handoff(
    session, admin, monkeypatch
) -> None:
    account, _material, package, tool_call = await _seed_publish_context(
        session, admin, approved=False
    )
    job = await publishing_service.create_publish_job(
        session,
        admin,
        CreatePublishJobRequest(
            account_id=account.id,
            tool_call_id=tool_call.id,
            idempotency_key="publish-request-unapproved",
            publish_package=package,
        ),
    )
    called = False

    async def forbidden_external_call(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("external API must not be called before approval")

    monkeypatch.setattr(
        publishing_service, "get_douyin_client_token", forbidden_external_call
    )

    with pytest.raises(PublishingServiceError) as captured:
        await publishing_service.prepare_douyin_handoff(session, admin, job.id)

    assert captured.value.code == "PUBLISH_APPROVAL_REQUIRED"
    assert called is False


@pytest.mark.asyncio
async def test_publish_approved_artifact_rejects_unapproved_and_stale_versions(
    session, admin
) -> None:
    account, _material, package, tool_call = await _seed_publish_context(
        session, admin, approved=True
    )
    unapproved = await _approved_publish_artifact(
        session,
        admin,
        account=account,
        package=package,
        tool_call=tool_call,
        status=DeliverableStatus.PENDING_REVIEW,
    )
    with pytest.raises(PublishingServiceError) as captured:
        await publishing_service.publish_approved_artifact(
            session,
            admin,
            account_id=account.id,
            artifact_id=unapproved.id,
            artifact_version=unapproved.version,
            scheduled_at=None,
            visibility="public",
            allow_comment=True,
        )
    assert captured.value.code == "PUBLISH_ARTIFACT_NOT_APPROVED"

    unapproved.status = DeliverableStatus.APPROVED
    newer = Deliverable(
        content_item_id=unapproved.content_item_id,
        agent_code="06-operator",
        type=DeliverableType.PUBLISH_CALENDAR,
        version=2,
        status=DeliverableStatus.PENDING_REVIEW,
        payload=dict(unapproved.payload),
    )
    session.add(newer)
    await session.commit()
    with pytest.raises(PublishingServiceError) as captured:
        await publishing_service.publish_approved_artifact(
            session,
            admin,
            account_id=account.id,
            artifact_id=unapproved.id,
            artifact_version=unapproved.version,
            scheduled_at=None,
            visibility="public",
            allow_comment=True,
        )
    assert captured.value.code == "PUBLISH_APPROVAL_VERSION_STALE"


@pytest.mark.asyncio
async def test_publish_approved_artifact_returns_no_fake_receipt_without_connection(
    session, admin, monkeypatch
) -> None:
    account, _material, package, tool_call = await _seed_publish_context(
        session, admin, approved=True
    )
    artifact = await _approved_publish_artifact(
        session,
        admin,
        account=account,
        package=package,
        tool_call=tool_call,
    )
    monkeypatch.setattr(publishing_service.settings, "douyin_h5_publish_enabled", False)

    receipt = await publishing_service.publish_approved_artifact(
        session,
        admin,
        account_id=account.id,
        artifact_id=artifact.id,
        artifact_version=artifact.version,
        scheduled_at=None,
        visibility="public",
        allow_comment=True,
    )

    assert receipt["status"] == "blocked"
    assert receipt["connection_state"] == "needs_connection"
    assert receipt["platform_receipt_id"] is None


@pytest.mark.asyncio
async def test_publish_approved_artifact_replays_one_platform_receipt(
    session, admin, monkeypatch
) -> None:
    account, _material, package, tool_call = await _seed_publish_context(
        session, admin, approved=True
    )
    artifact = await _approved_publish_artifact(
        session,
        admin,
        account=account,
        package=package,
        tool_call=tool_call,
    )
    monkeypatch.setattr(publishing_service.settings, "douyin_h5_publish_enabled", True)
    monkeypatch.setenv("TEST_DOUYIN_CLIENT_SECRET", "client-secret")
    share_calls = 0

    async def fake_client_token(**_kwargs):
        return "client-token"

    async def fake_open_ticket(**_kwargs):
        return "open-ticket"

    async def fake_share_id(**_kwargs):
        nonlocal share_calls
        share_calls += 1
        return {"share_id": "artifact-share-1", "log_id": "artifact-log-1"}

    monkeypatch.setattr(publishing_service, "get_douyin_client_token", fake_client_token)
    monkeypatch.setattr(publishing_service, "get_douyin_open_ticket", fake_open_ticket)
    monkeypatch.setattr(publishing_service, "create_douyin_share_id", fake_share_id)
    kwargs = {
        "account_id": account.id,
        "artifact_id": artifact.id,
        "artifact_version": artifact.version,
        "scheduled_at": None,
        "visibility": "public",
        "allow_comment": True,
    }

    first = await publishing_service.publish_approved_artifact(session, admin, **kwargs)
    second = await publishing_service.publish_approved_artifact(session, admin, **kwargs)

    assert first == second
    assert first["status"] == "handoff_ready"
    assert first["platform_receipt_id"] is not None
    assert share_calls == 1


@pytest.mark.asyncio
async def test_disabled_h5_publish_flag_blocks_external_calls(
    session, admin, monkeypatch
) -> None:
    account, _material, package, tool_call = await _seed_publish_context(
        session, admin, approved=True
    )
    job = await publishing_service.create_publish_job(
        session,
        admin,
        CreatePublishJobRequest(
            account_id=account.id,
            tool_call_id=tool_call.id,
            idempotency_key="publish-request-disabled",
            publish_package=package,
        ),
    )
    monkeypatch.setattr(publishing_service.settings, "douyin_h5_publish_enabled", False)
    called = False

    async def forbidden_external_call(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("disabled H5 publishing must not call Douyin")

    monkeypatch.setattr(
        publishing_service, "get_douyin_client_token", forbidden_external_call
    )

    with pytest.raises(PublishingServiceError) as captured:
        await publishing_service.prepare_douyin_handoff(session, admin, job.id)

    assert captured.value.code == "DOUYIN_H5_PUBLISH_DISABLED"
    assert called is False


@pytest.mark.asyncio
async def test_h5_publish_requires_all_current_douyin_app_scopes(
    session, admin, monkeypatch
) -> None:
    account, _material, package, tool_call = await _seed_publish_context(
        session, admin, approved=True
    )
    integration = await session.scalar(
        select(PlatformIntegration).where(
            PlatformIntegration.org_id == admin.org_id,
            PlatformIntegration.platform == Platform.DOUYIN.value,
        )
    )
    assert integration is not None
    integration.scopes = ["h5.share", "open.get.ticket"]
    await session.commit()

    job = await publishing_service.create_publish_job(
        session,
        admin,
        CreatePublishJobRequest(
            account_id=account.id,
            tool_call_id=tool_call.id,
            idempotency_key="publish-request-missing-aweme-share",
            publish_package=package,
        ),
    )
    monkeypatch.setattr(publishing_service.settings, "douyin_h5_publish_enabled", True)
    called = False

    async def forbidden_external_call(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("incomplete permissions must not call Douyin")

    monkeypatch.setattr(
        publishing_service, "get_douyin_client_token", forbidden_external_call
    )

    with pytest.raises(PublishingServiceError) as captured:
        await publishing_service.prepare_douyin_handoff(session, admin, job.id)

    assert captured.value.code == "DOUYIN_PUBLISH_SCOPE_MISSING"
    assert captured.value.details["missing"] == ["aweme.share"]
    assert called is False


@pytest.mark.asyncio
async def test_approved_job_builds_ephemeral_handoff_without_storing_signature(
    session, admin, monkeypatch
) -> None:
    account, _material, package, tool_call = await _seed_publish_context(
        session, admin, approved=True
    )
    job = await publishing_service.create_publish_job(
        session,
        admin,
        CreatePublishJobRequest(
            account_id=account.id,
            tool_call_id=tool_call.id,
            idempotency_key="publish-request-approved",
            publish_package=package,
        ),
    )
    monkeypatch.setattr(publishing_service.settings, "douyin_h5_publish_enabled", True)
    monkeypatch.setenv("TEST_DOUYIN_CLIENT_SECRET", "client-secret")

    async def fake_client_token(**_kwargs):
        return "client-token"

    async def fake_open_ticket(**_kwargs):
        return "open-ticket"

    async def fake_share_id(**_kwargs):
        return {"share_id": "share-1", "log_id": "log-1"}

    monkeypatch.setattr(
        publishing_service, "get_douyin_client_token", fake_client_token
    )
    monkeypatch.setattr(
        publishing_service, "get_douyin_open_ticket", fake_open_ticket
    )
    monkeypatch.setattr(
        publishing_service, "create_douyin_share_id", fake_share_id
    )

    handoff = await publishing_service.prepare_douyin_handoff(
        session, admin, job.id
    )
    await session.refresh(job)

    assert handoff.schema_url.startswith("snssdk1128://openplatform/share?")
    assert "signature=" in handoff.schema_url
    assert job.share_id == "share-1"
    assert job.last_platform_log_id == "log-1"
    assert job.status == PlatformPublishJobStatus.HANDOFF_READY
    assert "schema_url" not in job.publish_package
    assert "signature" not in str(job.publish_package)


@pytest.mark.asyncio
async def test_create_video_callback_binds_from_qr_handoff_without_direct_launch(
    session, admin
) -> None:
    account, _material, package, tool_call = await _seed_publish_context(
        session, admin, approved=True
    )
    job = await publishing_service.create_publish_job(
        session,
        admin,
        CreatePublishJobRequest(
            account_id=account.id,
            tool_call_id=tool_call.id,
            idempotency_key="publish-request-callback",
            publish_package=package,
        ),
    )
    job.share_id = "share-callback"
    job.status = PlatformPublishJobStatus.HANDOFF_READY
    await session.commit()

    callback = DouyinCreateVideoCallback(
        event="create_video",
        from_user_id="open-id-1",
        client_key="client-key",
        log_id="douyin-callback-log-1",
        content={
            "share_id": "share-callback",
            "item_id": "item-1",
            "video_id": "video-1",
        },
        event_time=datetime.now(UTC),
    )
    first = await publishing_service.ingest_douyin_create_video_callback(
        session, callback
    )
    second = await publishing_service.ingest_douyin_create_video_callback(
        session, callback
    )
    await session.refresh(job)

    assert first.id == second.id == job.id
    assert job.status == PlatformPublishJobStatus.BOUND
    assert job.external_item_id == "item-1"
    assert job.external_video_id == "video-1"
    assert job.platform_content_record_id is not None
    record = await session.get(
        PlatformContentRecord, job.platform_content_record_id
    )
    assert record is not None
    assert record.account_id == account.id
    assert record.external_content_id == "item-1"
    assert record.title == package.title
    assert record.published_at is not None
    assert record.source_kind == DataSourceKind.OFFICIAL_API
    assert record.source_metadata == {
        "share_id": "share-callback",
        "item_id": "item-1",
        "video_id": "video-1",
        "callback_log_id": "douyin-callback-log-1",
        "has_default_hashtag": None,
    }
    assert await session.scalar(select(func.count(PlatformContentRecord.id))) == 1


@pytest.mark.asyncio
async def test_mark_handoff_launched_moves_job_to_waiting_bind(
    session, admin
) -> None:
    account, _material, package, tool_call = await _seed_publish_context(
        session, admin, approved=True
    )
    job = await publishing_service.create_publish_job(
        session,
        admin,
        CreatePublishJobRequest(
            account_id=account.id,
            tool_call_id=tool_call.id,
            idempotency_key="publish-launch-state-1",
            publish_package=package,
        ),
    )
    job.status = PlatformPublishJobStatus.HANDOFF_READY
    job.share_id = "share-launched"
    await session.commit()

    launched = await publishing_service.mark_publish_job_launched(
        session, admin, job.id
    )

    assert launched.status == PlatformPublishJobStatus.WAITING_BIND
    assert launched.share_id == "share-launched"


@pytest.mark.asyncio
async def test_retry_failed_publish_job_clears_ephemeral_handoff_identity(
    session, admin
) -> None:
    account, _material, package, tool_call = await _seed_publish_context(
        session, admin, approved=True
    )
    job = await publishing_service.create_publish_job(
        session,
        admin,
        CreatePublishJobRequest(
            account_id=account.id,
            tool_call_id=tool_call.id,
            idempotency_key="publish-retry-state-1",
            publish_package=package,
        ),
    )
    job.status = PlatformPublishJobStatus.FAILED
    job.share_id = "expired-share"
    job.expires_at = datetime.now(UTC)
    job.last_error_code = "NETWORK_ERROR"
    job.last_error_message = "failed"
    await session.commit()

    retried = await publishing_service.retry_publish_job(session, admin, job.id)

    assert retried.status == PlatformPublishJobStatus.TASK_CREATED
    assert retried.share_id is None
    assert retried.expires_at is None
    assert retried.last_error_code is None
    assert retried.last_error_message is None


@pytest.mark.asyncio
async def test_bound_publish_job_cannot_be_cancelled(session, admin) -> None:
    account, _material, package, tool_call = await _seed_publish_context(
        session, admin, approved=True
    )
    job = await publishing_service.create_publish_job(
        session,
        admin,
        CreatePublishJobRequest(
            account_id=account.id,
            tool_call_id=tool_call.id,
            idempotency_key="publish-cancel-bound-1",
            publish_package=package,
        ),
    )
    job.status = PlatformPublishJobStatus.BOUND
    await session.commit()

    with pytest.raises(publishing_service.PublishingServiceError) as exc_info:
        await publishing_service.cancel_publish_job(session, admin, job.id)
    assert exc_info.value.code == "PUBLISH_JOB_CANNOT_CANCEL"


@pytest.mark.asyncio
async def test_callback_rejects_open_id_mismatch(session, admin) -> None:
    account, _material, package, tool_call = await _seed_publish_context(
        session, admin, approved=True
    )
    job = await publishing_service.create_publish_job(
        session,
        admin,
        CreatePublishJobRequest(
            account_id=account.id,
            tool_call_id=tool_call.id,
            idempotency_key="publish-request-open-id-mismatch",
            publish_package=package,
        ),
    )
    job.share_id = "share-mismatch"
    job.status = PlatformPublishJobStatus.WAITING_BIND
    await session.commit()

    with pytest.raises(PublishingServiceError) as captured:
        await publishing_service.ingest_douyin_create_video_callback(
            session,
            DouyinCreateVideoCallback(
                event="create_video",
                from_user_id="different-open-id",
                client_key="client-key",
                content={
                    "share_id": "share-mismatch",
                    "item_id": "item-mismatch",
                    "video_id": "video-mismatch",
                },
            ),
        )

    assert captured.value.code == "DOUYIN_CALLBACK_ACCOUNT_MISMATCH"
