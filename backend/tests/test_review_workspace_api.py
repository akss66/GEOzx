from datetime import UTC, date, datetime, timedelta

import pytest

from app.models import (
    Account,
    AccountMetricSnapshot,
    ContentItem,
    DataImportBatch,
    MetricSnapshot,
    OptimizationSuggestion,
    PlatformAccountAuth,
    Project,
    ProjectMembership,
)
from app.models.enums import (
    DataSourceKind,
    ImportBatchStatus,
    MetricSource,
    Platform,
    WorkspaceRole,
)


async def _token(client, email: str, password: str) -> str:
    response = await client.post("/auth/login", json={"email": email, "password": password})
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _review_account(session, admin, *, nickname: str = "复盘账号"):
    project = Project(org_id=admin.org_id, name=f"{nickname}项目")
    session.add(project)
    await session.flush()
    account = Account(
        org_id=admin.org_id,
        project_id=project.id,
        platform=Platform.DOUYIN,
        nickname=nickname,
        auth={"auth_status": "authorized", "data_sync_status": "healthy"},
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return project, account


@pytest.mark.asyncio
async def test_review_workspace_requires_an_explicit_accessible_account(
    client, admin, member, session
):
    _project, account = await _review_account(session, admin)
    token = await _token(client, "user@test.com", "user-pw-123")

    response = await client.get(
        "/metrics/review-workspace",
        headers=_auth(token),
        params={"account_id": account.id, "days": 30},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_review_workspace_explains_missing_goal_and_metrics(client, admin, session):
    _project, account = await _review_account(session, admin)
    account.auth = {"auth_status": "unauthorized", "data_sync_status": "not_configured"}
    await session.commit()
    token = await _token(client, "admin@test.com", "admin-pw-123")

    response = await client.get(
        "/metrics/review-workspace",
        headers=_auth(token),
        params={"account_id": account.id, "days": 30},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["account"]["id"] == account.id
    assert body["data_status"]["has_data"] is False
    assert body["data_status"]["sources"] == []
    assert body["goal"]["status"] == "not_configured"
    assert body["goal"]["achievement_percent"] is None
    assert body["evidence"] == []
    assert body["attributions"] == []
    assert "真实指标" in "".join(body["data_status"]["missing_reasons"])
    assert "目标" in "".join(body["data_status"]["missing_reasons"])
    assert "not_configured" not in "".join(body["data_status"]["missing_reasons"])
    assert "尚未配置" in "".join(body["data_status"]["missing_reasons"])


@pytest.mark.asyncio
async def test_review_workspace_builds_account_scoped_narrative_and_goal_progress(
    client, admin, session
):
    project, account = await _review_account(session, admin, nickname="主账号")
    _other_project, other_account = await _review_account(session, admin, nickname="其他账号")
    content = ContentItem(project_id=project.id, account_id=account.id, title="高完播实测")
    other_content = ContentItem(
        project_id=_other_project.id,
        account_id=other_account.id,
        title="不应出现",
    )
    session.add_all([content, other_content])
    await session.flush()

    today = date.today()
    current_day = today - timedelta(days=1)
    previous_day = today - timedelta(days=8)
    session.add_all(
        [
            MetricSnapshot(
                org_id=admin.org_id,
                content_item_id=content.id,
                account_id=account.id,
                source=MetricSource.DOUYIN,
                stat_date=current_day,
                title=content.title,
                play=2000,
                exposure=5000,
                completion_rate=0.40,
                like_rate=0.08,
                comment_rate=0.02,
                share_rate=0.01,
                follower_delta=20,
            ),
            MetricSnapshot(
                org_id=admin.org_id,
                content_item_id=content.id,
                account_id=account.id,
                source=MetricSource.DOUYIN,
                stat_date=previous_day,
                title=content.title,
                play=1000,
                exposure=3000,
                completion_rate=0.30,
                like_rate=0.05,
                comment_rate=0.01,
                share_rate=0.01,
                follower_delta=10,
            ),
            MetricSnapshot(
                org_id=admin.org_id,
                content_item_id=other_content.id,
                account_id=other_account.id,
                source=MetricSource.DOUYIN,
                stat_date=current_day,
                title=other_content.title,
                play=999999,
                exposure=999999,
                completion_rate=0.99,
                follower_delta=999,
            ),
            OptimizationSuggestion(
                org_id=admin.org_id,
                content_item_id=content.id,
                target_stage="content_direction",
                suggestion="保留结论前置的前三秒结构",
            ),
            OptimizationSuggestion(
                org_id=admin.org_id,
                content_item_id=other_content.id,
                target_stage="editing",
                suggestion="其他账号建议不应出现",
            ),
        ]
    )
    await session.commit()

    token = await _token(client, "admin@test.com", "admin-pw-123")
    goal_response = await client.put(
        f"/metrics/review-goals/{account.id}",
        headers=_auth(token),
        json={
            "period_days": 7,
            "target_play": 4000,
            "target_completion_rate": 0.50,
            "target_follower_delta": 40,
        },
    )
    assert goal_response.status_code == 200

    response = await client.get(
        "/metrics/review-workspace",
        headers=_auth(token),
        params={"account_id": account.id, "days": 7},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data_status"]["has_data"] is True
    assert body["data_status"]["sources"] == ["official_api"]
    assert body["totals"]["play"] == 2000
    assert body["totals"]["avg_completion_rate"] == 0.4
    assert body["totals"]["follower_delta"] == 20
    assert body["goal"]["status"] == "behind"
    assert body["goal"]["achievement_percent"] == 60.0
    play_change = next(row for row in body["changes"] if row["metric"] == "play")
    assert play_change["direction"] == "up"
    assert play_change["delta_percent"] == 100.0
    assert [row["title"] for row in body["evidence"]] == ["高完播实测"]
    assert [row["title"] for row in body["attributions"]] == ["高完播实测"]
    assert [row["suggestion"] for row in body["suggestions"]] == [
        "保留结论前置的前三秒结构"
    ]
    assert "不应出现" not in str(body)


@pytest.mark.asyncio
async def test_only_account_operator_or_lead_can_change_review_goal(
    client, admin, member, session
):
    project, account = await _review_account(session, admin)
    membership = ProjectMembership(
        project_id=project.id,
        user_id=member.id,
        role=WorkspaceRole.REVIEWER,
    )
    session.add(membership)
    await session.commit()
    token = await _token(client, "user@test.com", "user-pw-123")
    payload = {"period_days": 30, "target_play": 10000}

    denied = await client.put(
        f"/metrics/review-goals/{account.id}",
        headers=_auth(token),
        json=payload,
    )
    membership.role = WorkspaceRole.OPERATOR
    await session.commit()
    allowed = await client.put(
        f"/metrics/review-goals/{account.id}",
        headers=_auth(token),
        json=payload,
    )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["period_days"] == 30
    assert allowed.json()["target_play"] == 10000


@pytest.mark.asyncio
async def test_legacy_review_endpoints_and_suggestions_respect_project_scope(
    client, admin, member, session
):
    visible_project, visible_account = await _review_account(
        session,
        admin,
        nickname="可见账号",
    )
    hidden_project, hidden_account = await _review_account(
        session,
        admin,
        nickname="隐藏账号",
    )
    visible_content = ContentItem(
        project_id=visible_project.id,
        account_id=visible_account.id,
        title="可见内容",
    )
    hidden_content = ContentItem(
        project_id=hidden_project.id,
        account_id=hidden_account.id,
        title="隐藏内容",
    )
    session.add_all([visible_content, hidden_content])
    await session.flush()
    session.add_all(
        [
            ProjectMembership(
                project_id=visible_project.id,
                user_id=member.id,
                role=WorkspaceRole.OPERATOR,
            ),
            MetricSnapshot(
                org_id=admin.org_id,
                content_item_id=visible_content.id,
                account_id=visible_account.id,
                source=MetricSource.DOUYIN,
                stat_date=date.today(),
                title=visible_content.title,
                play=100,
            ),
            MetricSnapshot(
                org_id=admin.org_id,
                content_item_id=hidden_content.id,
                account_id=hidden_account.id,
                source=MetricSource.DOUYIN,
                stat_date=date.today(),
                title=hidden_content.title,
                play=9999,
            ),
            OptimizationSuggestion(
                org_id=admin.org_id,
                content_item_id=visible_content.id,
                suggestion="可见建议",
            ),
            OptimizationSuggestion(
                org_id=admin.org_id,
                content_item_id=hidden_content.id,
                suggestion="隐藏建议",
            ),
        ]
    )
    await session.commit()
    token = await _token(client, "user@test.com", "user-pw-123")
    headers = _auth(token)

    overview = await client.get("/metrics/overview?days=30", headers=headers)
    snapshots = await client.get("/metrics/performance-snapshots", headers=headers)
    suggestions = await client.get("/optimization-suggestions", headers=headers)

    assert overview.status_code == 200
    assert overview.json()["total_play"] == 100
    assert [row["title"] for row in snapshots.json()] == ["可见内容"]
    assert [row["suggestion"] for row in suggestions.json()] == ["可见建议"]


@pytest.mark.asyncio
async def test_review_workspace_uses_account_level_imports_when_content_attribution_is_missing(
    client, admin, session
):
    _project, account = await _review_account(session, admin, nickname="Only daily play")
    last_sync_at = datetime(2026, 7, 21, 7, 45, tzinfo=UTC)
    session.add(
        PlatformAccountAuth(
            org_id=admin.org_id,
            account_id=account.id,
            platform=account.platform.value,
            auth_status="authorized",
            data_sync_status="healthy",
            last_sync_at=last_sync_at,
        )
    )
    batch = DataImportBatch(
        org_id=admin.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=ImportBatchStatus.COMMITTED,
        template_code="douyin_daily_play_v1",
        content_sha256="6" * 64,
        period_start=date.today() - timedelta(days=6),
        period_end=date.today(),
        committed_at=datetime.now(UTC),
    )
    session.add(batch)
    await session.flush()
    session.add(
        AccountMetricSnapshot(
            org_id=admin.org_id,
            account_id=account.id,
            import_batch_id=batch.id,
            source_kind=DataSourceKind.PLATFORM_EXPORT,
            stat_date=date.today(),
            total_play=81,
        )
    )
    await session.commit()

    token = await _token(client, "admin@test.com", "admin-pw-123")
    response = await client.get(
        "/metrics/review-workspace",
        headers=_auth(token),
        params={"account_id": account.id, "days": 7},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data_status"]["has_data"] is True
    assert body["data_status"]["sources"] == ["platform_export"]
    assert body["data_status"]["coverage"]["account_metrics"] == "available"
    assert body["data_status"]["coverage"]["content_metrics"] == "missing"
    assert "当前周期仅有账号级趋势数据，作品归因尚未补齐" in body["data_status"]["missing_reasons"]
    assert body["data_status"]["latest_synced_at"].startswith("2026-07-21T07:45:00")
    assert body["data_status"]["latest_confirmed_at"] is not None
    assert body["data_status"]["days_since_observed"] == 0
    assert body["data_status"]["days_since_confirmed"] == 0
    assert body["data_status"]["source_summary"][0]["source_kind"] == "platform_export"
    assert body["data_status"]["source_summary"][0]["data_domains"] == ["account_metrics"]
    assert body["totals"]["play"] == 81
    assert body["totals"]["exposure"] is None
    assert body["totals"]["avg_completion_rate"] is None
    assert body["totals"]["avg_engagement_rate"] is None
    assert body["totals"]["follower_delta"] is None
    assert body["trend"][-1]["play"] == 81
    assert body["trend"][-1]["exposure"] is None
    assert body["engagement"][-1]["completion_rate"] is None
    assert body["engagement"][-1]["like_rate"] is None
    assert body["attributions"] == []
    assert body["evidence"] == []


@pytest.mark.asyncio
async def test_review_workspace_reports_import_provenance_for_content_metrics(
    client, admin, session
):
    project, account = await _review_account(session, admin, nickname="Imported content")
    content = ContentItem(project_id=project.id, account_id=account.id, title="Imported work")
    session.add(content)
    await session.flush()
    batch = DataImportBatch(
        org_id=admin.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=ImportBatchStatus.COMMITTED,
        template_code="douyin_work_list_v1",
        content_sha256="8" * 64,
        period_start=date.today(),
        period_end=date.today(),
        committed_at=datetime.now(UTC),
    )
    session.add(batch)
    await session.flush()
    session.add(
        MetricSnapshot(
            org_id=admin.org_id,
            content_item_id=content.id,
            account_id=account.id,
            import_batch_id=batch.id,
            source=MetricSource.DOUYIN,
            stat_date=date.today(),
            title=content.title,
            play=81,
        )
    )
    await session.commit()

    token = await _token(client, "admin@test.com", "admin-pw-123")
    response = await client.get(
        "/metrics/review-workspace",
        headers=_auth(token),
        params={"account_id": account.id, "days": 7},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data_status"]["sources"] == ["platform_export"]
    assert body["data_status"]["source_summary"][0]["source_kind"] == "platform_export"


@pytest.mark.asyncio
async def test_review_workspace_marks_unavailable_changes_and_goal_metrics_for_account_only_data(
    client, admin, session
):
    _project, account = await _review_account(session, admin, nickname="Goal gap account")
    batch = DataImportBatch(
        org_id=admin.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=ImportBatchStatus.COMMITTED,
        template_code="douyin_daily_play_v1",
        content_sha256="7" * 64,
        period_start=date.today() - timedelta(days=6),
        period_end=date.today(),
        committed_at=datetime.now(UTC),
    )
    session.add(batch)
    await session.flush()
    session.add(
        AccountMetricSnapshot(
            org_id=admin.org_id,
            account_id=account.id,
            import_batch_id=batch.id,
            source_kind=DataSourceKind.PLATFORM_EXPORT,
            stat_date=date.today(),
            total_play=81,
        )
    )
    await session.commit()

    token = await _token(client, "admin@test.com", "admin-pw-123")
    goal_response = await client.put(
        f"/metrics/review-goals/{account.id}",
        headers=_auth(token),
        json={
            "period_days": 7,
            "target_play": 100,
            "target_completion_rate": 0.4,
            "target_follower_delta": 10,
        },
    )
    assert goal_response.status_code == 200

    response = await client.get(
        "/metrics/review-workspace",
        headers=_auth(token),
        params={"account_id": account.id, "days": 7},
    )

    assert response.status_code == 200
    body = response.json()
    completion_change = next(row for row in body["changes"] if row["metric"] == "completion_rate")
    follower_change = next(row for row in body["changes"] if row["metric"] == "follower_delta")
    play_component = next(item for item in body["goal"]["components"] if item["metric"] == "play")
    completion_component = next(
        item for item in body["goal"]["components"] if item["metric"] == "completion_rate"
    )
    follower_component = next(
        item for item in body["goal"]["components"] if item["metric"] == "follower_delta"
    )

    assert completion_change["current"] is None
    assert completion_change["previous"] is None
    assert completion_change["delta_percent"] is None
    assert completion_change["direction"] == "unavailable"
    assert follower_change["current"] is None
    assert follower_change["previous"] is None
    assert follower_change["delta_percent"] is None
    assert follower_change["direction"] == "unavailable"
    assert body["goal"]["status"] == "insufficient_data"
    assert body["goal"]["achievement_percent"] is None
    assert play_component["current"] == 81
    assert play_component["achievement_percent"] == 81.0
    assert completion_component["current"] is None
    assert completion_component["achievement_percent"] is None
    assert completion_component["status"] == "unavailable"
    assert follower_component["current"] is None
    assert follower_component["achievement_percent"] is None
    assert follower_component["status"] == "unavailable"
