from datetime import UTC, date, datetime, timedelta

import pytest

from app.config import settings
from app.models import (
    Account,
    AccountMetricSnapshot,
    DataImportBatch,
    MetricSnapshot,
    PlatformContentRecord,
)
from app.models.enums import (
    AccountStatus,
    ContentIdentityConfidence,
    DataSourceKind,
    ImportBatchStatus,
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
    assert result["data_status"] == "available"
    assert result["data_period"] == {
        "start": date.today().isoformat(),
        "end": date.today().isoformat(),
    }
    assert result["pending_imports"] == []
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
async def test_account_data_context_prefers_account_level_exports_over_content_rollups(
    session, admin
) -> None:
    account = await _account(session, admin, nickname="账号级导出优先")
    batch = DataImportBatch(
        org_id=account.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=ImportBatchStatus.COMMITTED,
        template_code="douyin_daily_like_v1",
        content_sha256="7" * 64,
        period_start=date.today(),
        period_end=date.today(),
        row_count=1,
        committed_at=datetime.now(UTC),
    )
    session.add(batch)
    await session.flush()
    session.add_all(
        [
            AccountMetricSnapshot(
                org_id=account.org_id,
                account_id=account.id,
                import_batch_id=batch.id,
                source_kind=DataSourceKind.PLATFORM_EXPORT,
                stat_date=date.today(),
                total_play=500,
                follower_count=7994,
                like_count=-21,
                profile_visit_count=12,
            ),
            AccountMetricSnapshot(
                org_id=account.org_id,
                account_id=account.id,
                import_batch_id=batch.id,
                source_kind=DataSourceKind.PLATFORM_EXPORT,
                stat_date=date.today() - timedelta(days=1),
                follower_count=7900,
            ),
            MetricSnapshot(
                org_id=admin.org_id,
                account_id=account.id,
                source=MetricSource.DOUYIN,
                stat_date=date.today(),
                play=320,
                exposure=900,
                completion_rate=0.36,
                like_count=32,
                like_rate=0.1,
                comment_rate=0.01,
                share_rate=0.02,
                follower_delta=5,
            ),
        ]
    )
    await session.commit()

    result = await build_runtime_tool_adapter().invoke(
        "account.data_context",
        {"days": 30},
        ToolExecutionContext(session=session, user=admin, account_id=account.id),
    )

    assert result["metrics"]["play"]["value"] == 500
    assert result["metrics"]["follower_count"]["value"] == 7994
    assert result["metrics"]["like_count"]["value"] == -21
    assert result["metrics"]["profile_visit_count"]["value"] == 12
    assert result["metrics"]["like_count"]["source"] == "platform_export"


@pytest.mark.asyncio
async def test_account_data_context_reports_preview_without_claiming_available_data(
    session,
    admin,
) -> None:
    account = await _account(session, admin, nickname="待确认导入账号")
    batch = DataImportBatch(
        org_id=account.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=ImportBatchStatus.PREVIEW_READY,
        template_code="douyin_period_aggregate_v1",
        content_sha256="8" * 64,
        period_start=date(2026, 5, 2),
        period_end=date(2026, 7, 31),
        row_count=1,
    )
    session.add(batch)
    await session.commit()
    await session.refresh(batch)

    result = await build_runtime_tool_adapter().invoke(
        "account.data_context",
        {"days": 30},
        ToolExecutionContext(session=session, user=admin, account_id=account.id),
    )

    assert result["data_status"] == "pending_import"
    assert result["data_period"] is None
    assert result["pending_imports"] == [
        {
            "batch_id": batch.id,
            "status": "preview_ready",
            "template_code": "douyin_period_aggregate_v1",
            "row_count": 1,
            "period_start": "2026-05-02",
            "period_end": "2026-07-31",
        }
    ]


@pytest.mark.asyncio
async def test_account_data_context_reports_empty_when_no_data_or_import_exists(
    session,
    admin,
) -> None:
    account = await _account(session, admin, nickname="空账号")

    result = await build_runtime_tool_adapter().invoke(
        "account.data_context",
        {"days": 30},
        ToolExecutionContext(session=session, user=admin, account_id=account.id),
    )

    assert result["data_status"] == "empty"
    assert result["data_period"] is None
    assert result["pending_imports"] == []


@pytest.mark.asyncio
async def test_runtime_tools_reject_model_supplied_account_id(session, admin) -> None:
    account = await _account(session, admin)

    with pytest.raises(ToolValidationError):
        await build_runtime_tool_adapter().invoke(
            "account.profile",
            {"account_id": account.id},
            ToolExecutionContext(session=session, user=admin, account_id=account.id),
        )


def test_runtime_tool_catalog_exposes_read_and_prepare_phases(admin) -> None:
    catalog = runtime_tool_capabilities(admin)

    assert {item["code"] for item in catalog} == {
        "account.data_context",
        "account.metrics_summary",
        "account.profile",
        "publish_package_prepare",
    }
    assert all(item["kind"] == "tool" for item in catalog)
    assert all(item["permission_mode"] == "auto" for item in catalog)
    assert all(item["scope"] == "account" for item in catalog)
    assert {
        item["execution_phase"]
        for item in catalog
        if item["code"].startswith("account.")
    } == {"read"}
    prepare_tool = next(item for item in catalog if item["code"] == "publish_package_prepare")
    assert prepare_tool["execution_phase"] == "prepare"


def test_runtime_tool_catalog_exposes_confirm_tool_only_in_explicit_test_mode(
    admin,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(
        settings,
        "llm_deterministic_test_provider_enabled",
        True,
        raising=False,
    )

    catalog = runtime_tool_capabilities(admin)
    confirm_tool = next(item for item in catalog if item["code"] == "test.confirm_action")

    assert confirm_tool["permission_mode"] == "confirm"
    assert confirm_tool["scope"] == "account"
