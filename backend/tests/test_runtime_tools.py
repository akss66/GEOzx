from datetime import date, timedelta

import pytest

from app.models import Account, MetricSnapshot, PlatformContentRecord
from app.models.enums import (
    AccountStatus,
    ContentIdentityConfidence,
    MetricSource,
    Platform,
)
from app.orchestrator.runtime_tools import (
    build_runtime_tool_adapter,
    runtime_tool_capabilities,
)
from app.tools import ToolExecutionContext, ToolValidationError


async def _account(session, admin, *, nickname: str = "测试账号") -> Account:
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname=nickname,
        status=AccountStatus.ACTIVE,
        auth={
            "auth_status": "authorized",
            "data_sync_status": "synced",
            "access_token": "must-not-leak",
            "client_secret": "must-not-leak",
        },
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


@pytest.mark.asyncio
async def test_account_profile_uses_selected_context_and_redacts_auth(session, admin) -> None:
    account = await _account(session, admin)
    adapter = build_runtime_tool_adapter()

    result = await adapter.invoke(
        "account.profile",
        {},
        ToolExecutionContext(session=session, user=admin, account_id=account.id),
    )

    assert result == {
        "account_id": account.id,
        "nickname": "测试账号",
        "platform": "douyin",
        "status": "active",
        "auth_status": "authorized",
        "data_sync_status": "synced",
    }
    assert "access_token" not in result
    assert "client_secret" not in result


@pytest.mark.asyncio
async def test_account_metrics_only_aggregate_selected_account(session, admin) -> None:
    account = await _account(session, admin, nickname="目标账号")
    other = await _account(session, admin, nickname="其他账号")
    content_record = PlatformContentRecord(
        org_id=admin.org_id,
        account_id=account.id,
        platform=Platform.DOUYIN,
        title="Target content",
        content_format="video",
        review_status="published",
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
    )
    session.add(content_record)
    await session.flush()
    session.add_all(
        [
            MetricSnapshot(
                org_id=admin.org_id,
                account_id=account.id,
                platform_content_record_id=content_record.id,
                source=MetricSource.DOUYIN,
                stat_date=date.today(),
                play=120,
                exposure=500,
                completion_rate=0.4,
                completion_rate_5s=0.3,
                bounce_rate_2s=0.6,
                profile_visit_count=12,
                like_rate=0.1,
                comment_rate=0.02,
                share_rate=0.01,
                follower_delta=8,
            ),
            MetricSnapshot(
                org_id=admin.org_id,
                account_id=account.id,
                source=MetricSource.DOUYIN,
                stat_date=date.today() - timedelta(days=2),
                play=80,
                exposure=300,
                completion_rate=0.2,
                completion_rate_5s=0.1,
                bounce_rate_2s=0.8,
                profile_visit_count=4,
                like_rate=0.05,
                comment_rate=0.01,
                share_rate=0,
                follower_delta=2,
            ),
            MetricSnapshot(
                org_id=admin.org_id,
                account_id=other.id,
                source=MetricSource.DOUYIN,
                stat_date=date.today(),
                play=9999,
                exposure=9999,
                completion_rate=1,
                like_rate=1,
                comment_rate=1,
                share_rate=1,
                follower_delta=999,
            ),
        ]
    )
    await session.commit()

    result = await build_runtime_tool_adapter().invoke(
        "account.metrics_summary",
        {"days": 7},
        ToolExecutionContext(session=session, user=admin, account_id=account.id),
    )

    assert result["snapshot_count"] == 2
    assert result["play"] == 200
    assert result["exposure"] == 800
    assert result["follower_delta"] == 10
    assert result["average_completion_rate"] == pytest.approx(0.3)
    assert result["average_completion_rate_5s"] == pytest.approx(0.2)
    assert result["average_bounce_rate_2s"] == pytest.approx(0.7)
    assert result["profile_visit_count"] == 16
    assert result["content_formats"] == {"video": 1}
    assert result["review_statuses"] == {"published": 1}


@pytest.mark.asyncio
async def test_account_data_context_exposes_quality_period_and_evidence(session, admin) -> None:
    account = await _account(session, admin, nickname="证据账号")
    session.add(
        MetricSnapshot(
            org_id=admin.org_id,
            account_id=account.id,
            source=MetricSource.DOUYIN,
            stat_date=date.today(),
            title="证据作品",
            play=320,
            exposure=900,
            completion_rate=0.36,
            like_rate=0.08,
            comment_rate=0.01,
            share_rate=0.02,
            follower_delta=5,
        )
    )
    await session.commit()

    result = await build_runtime_tool_adapter().invoke(
        "account.data_context",
        {"days": 30},
        ToolExecutionContext(session=session, user=admin, account_id=account.id),
    )

    assert result["account_id"] == account.id
    assert result["period"]["days"] == 30
    assert result["period"]["end"] == date.today().isoformat()
    assert result["coverage"]["content_metrics"] == "available"
    assert result["freshness"]["latest_observed_at"] == date.today().isoformat()
    assert result["conflict_count"] == 0
    assert result["metrics"]["play"]["value"] == 320
    assert result["metrics"]["play"]["source"] == "official_api"
    assert result["metrics"]["play"]["evidence_refs"]
    assert result["metrics"]["play"]["evidence_refs"][0]["kind"] == "metric_snapshot"


@pytest.mark.asyncio
async def test_runtime_tools_reject_model_supplied_account_id(session, admin) -> None:
    account = await _account(session, admin)

    with pytest.raises(ToolValidationError):
        await build_runtime_tool_adapter().invoke(
            "account.profile",
            {"account_id": account.id},
            ToolExecutionContext(session=session, user=admin, account_id=account.id),
        )


def test_runtime_tool_catalog_marks_read_only_tools_as_automatic(admin) -> None:
    catalog = runtime_tool_capabilities(admin)

    assert {item["code"] for item in catalog} == {
        "account.data_context",
        "account.metrics_summary",
        "account.profile",
    }
    assert all(item["kind"] == "tool" for item in catalog)
    assert all(item["permission_mode"] == "auto" for item in catalog)
    assert all(item["scope"] == "account" for item in catalog)
