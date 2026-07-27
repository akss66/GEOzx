from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.models import (
    Account,
    AgentToolCall,
    BrainTask,
    MaterialAsset,
    PlatformAccountAuth,
    PlatformIntegration,
)
from app.models.enums import (
    AccountStatus,
    BrainTaskStatus,
    BrainTaskType,
    MaterialStatus,
    Platform,
)
from app.models.publishing import PlatformPublishJobStatus
from app.schemas.orchestrator import PublishPackageOut
from app.schemas.publishing import CreatePublishJobRequest
from app.services import publishing as publishing_service


def _headers(user) -> dict[str, str]:
    token = create_access_token(str(user.id), user.role.value)
    return {"Authorization": f"Bearer {token}"}


async def _seed_publish_context(session, admin, *, approved: bool):
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        external_account_id="douyin-account-api",
        nickname="API 测试抖音账号",
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
        title="发布 API 测试",
        type=BrainTaskType.CONTENT_CREATION,
        status=BrainTaskStatus.PENDING_ACCEPTANCE,
    )
    session.add_all([account, material, integration, auth, task])
    await session.flush()
    package = PublishPackageOut(
        platform=Platform.DOUYIN,
        account_id=account.id,
        content_type="video",
        title="一条真实发布 API 测试",
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
        idempotency_key="tool-publish-api-1",
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
async def test_publish_job_api_requires_login(client) -> None:
    response = await client.get("/publishing/jobs/1")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_publish_job_api_reads_owned_job(client, session, admin) -> None:
    account, _material, package, tool_call = await _seed_publish_context(
        session, admin, approved=True
    )
    job = await publishing_service.create_publish_job(
        session,
        admin,
        CreatePublishJobRequest(
            account_id=account.id,
            tool_call_id=tool_call.id,
            idempotency_key="publish-api-read-1",
            publish_package=package,
        ),
    )

    response = await client.get(
        f"/publishing/jobs/{job.id}",
        headers=_headers(admin),
    )

    assert response.status_code == 200
    assert response.json()["id"] == job.id
    assert response.json()["status"] == "task_created"


@pytest.mark.asyncio
async def test_publish_job_follows_publish_package_approval(
    client, session, admin
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
            idempotency_key="publish-api-approval-sync-1",
            publish_package=package,
        ),
    )
    assert job.status == PlatformPublishJobStatus.PENDING_APPROVAL

    response = await client.post(
        f"/brain/tool-calls/{tool_call.id}/approve",
        headers=_headers(admin),
        json={"approved": True, "comment": "批准进入抖音官方投稿流程"},
    )

    assert response.status_code == 200
    await session.refresh(job)
    assert job.status == PlatformPublishJobStatus.TASK_CREATED
    assert job.approval_snapshot["approved"] is True


@pytest.mark.asyncio
async def test_publish_job_action_api_updates_durable_state(
    client, session, admin
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
            idempotency_key="publish-api-actions-1",
            publish_package=package,
        ),
    )
    job.status = PlatformPublishJobStatus.HANDOFF_READY
    job.share_id = "share-api-launched"
    await session.commit()

    launched = await client.post(
        f"/publishing/jobs/{job.id}/launched",
        headers=_headers(admin),
    )
    assert launched.status_code == 200
    assert launched.json()["status"] == "waiting_bind"

    cancelled = await client.post(
        f"/publishing/jobs/{job.id}/cancel",
        headers=_headers(admin),
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_publish_job_retry_api_resets_failed_job(client, session, admin) -> None:
    account, _material, package, tool_call = await _seed_publish_context(
        session, admin, approved=True
    )
    job = await publishing_service.create_publish_job(
        session,
        admin,
        CreatePublishJobRequest(
            account_id=account.id,
            tool_call_id=tool_call.id,
            idempotency_key="publish-api-retry-1",
            publish_package=package,
        ),
    )
    job.status = PlatformPublishJobStatus.FAILED
    job.share_id = "stale-share"
    await session.commit()

    response = await client.post(
        f"/publishing/jobs/{job.id}/retry",
        headers=_headers(admin),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "task_created"
    assert response.json()["share_id"] is None


@pytest.mark.asyncio
async def test_douyin_webhook_challenge_is_echoed_without_auth(client) -> None:
    response = await client.post(
        "/platform-integrations/douyin/webhooks",
        json={"challenge": "douyin-webhook-challenge"},
    )

    assert response.status_code == 200
    assert response.json() == {"challenge": "douyin-webhook-challenge"}


@pytest.mark.asyncio
async def test_douyin_create_video_webhook_binds_publish_job(
    client, session, admin
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
            idempotency_key="publish-api-callback-1",
            publish_package=package,
        ),
    )
    job.share_id = "share-api-callback"
    job.status = PlatformPublishJobStatus.WAITING_BIND
    await session.commit()

    response = await client.post(
        "/platform-integrations/douyin/webhooks",
        json={
            "event": "create_video",
            "from_user_id": "open-id-1",
            "client_key": "client-key",
            "log_id": "callback-log-api-1",
            "content": {
                "share_id": "share-api-callback",
                "item_id": "item-api-1",
                "video_id": "video-api-1",
                "has_default_hashtag": True,
            },
            "event_time": datetime.now(UTC).isoformat(),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "bound"
    assert payload["external_item_id"] == "item-api-1"


@pytest.mark.asyncio
async def test_douyin_webhook_returns_stable_error_payload(client) -> None:
    response = await client.post(
        "/platform-integrations/douyin/webhooks",
        json={
            "event": "create_video",
            "from_user_id": "open-id-1",
            "client_key": "client-key",
            "content": {
                "share_id": "unknown-share-id",
                "item_id": "item-missing",
            },
        },
    )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "DOUYIN_CALLBACK_JOB_NOT_FOUND",
            "message": "找不到对应的投稿任务。",
            "retryable": False,
            "details": {},
        }
    }
