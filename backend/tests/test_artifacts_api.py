from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import (
    Account,
    AgentQualityScore,
    AgentRun,
    BrainTask,
    ContentItem,
    ConversationThread,
    ConversationTurn,
    Deliverable,
    Project,
    ProjectMembership,
    SkillRun,
)
from app.models.enums import (
    BrainTaskStatus,
    BrainTaskType,
    DeliverableStatus,
    DeliverableType,
    Platform,
    WorkspaceRole,
)


async def _token(client, email: str, password: str) -> str:
    response = await client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _review_payload(*, summary: str = "互动率回升，但涨粉速度仍需改善") -> dict:
    return {
        "period": "2026-07-01 至 2026-07-28",
        "summary": summary,
        "key_metrics": {"互动率": "5.2%", "新增粉丝": 320},
        "highlights": ["知识类内容完播率较高"],
        "issues": ["更新频率不稳定"],
        "optimization_suggestions": ["固定每周发布三条"],
        "evidence_refs": [{"kind": "account_metric_snapshot", "id": 81, "label": "近28天账号指标"}],
        "acceptance_items": [
            {
                "label": "summary",
                "note": "Confirm that this item matches the selected account.",
            }
        ],
    }


async def _seed_artifact(
    session,
    admin,
    *,
    account_name: str,
    version: int = 1,
    status: DeliverableStatus = DeliverableStatus.PENDING_REVIEW,
    payload: dict | None = None,
    skill_code: str = "account_review",
    deliverable_type: DeliverableType = DeliverableType.REVIEW_REPORT,
):
    project = Project(org_id=admin.org_id, name=f"{account_name}项目")
    session.add(project)
    await session.flush()
    account = Account(
        org_id=admin.org_id,
        project_id=project.id,
        platform=Platform.DOUYIN,
        nickname=account_name,
    )
    session.add(account)
    await session.flush()
    content = ContentItem(
        project_id=project.id,
        account_id=account.id,
        created_by_id=admin.id,
        title=f"{account_name}运营复盘",
    )
    thread = ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        project_id=project.id,
        account_id=account.id,
        title=f"{account_name}对话",
    )
    session.add_all([content, thread])
    await session.flush()
    turn = ConversationTurn(
        org_id=admin.org_id,
        thread_id=thread.id,
        created_by_id=admin.id,
        client_message_id=f"artifact-{account_name}-{version}",
        user_input="复盘这个账号",
    )
    session.add(turn)
    await session.flush()
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        content_item_id=content.id,
        title=f"{account_name}复盘任务",
        type=BrainTaskType.REVIEW_OPTIMIZATION,
        status=BrainTaskStatus.COMPLETED,
        progress=100,
        current_focus="复盘完成",
    )
    session.add(task)
    await session.flush()
    run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        task_id=task.id,
        thread_id=thread.id,
        turn_id=turn.id,
        client_message_id=f"run-{account_name}-{version}",
        status="completed",
        phase="completed",
        attempt=1,
    )
    session.add(run)
    await session.flush()
    skill_run = SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        task_id=task.id,
        idempotency_key=f"skill-{account_name}-{version}",
        skill_code=skill_code,
        skill_version=1,
        status="completed",
        quality_score=Decimal("0.91"),
    )
    session.add(skill_run)
    await session.flush()
    deliverable = Deliverable(
        content_item_id=content.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        skill_run_id=skill_run.id,
        agent_code="06-operator",
        type=deliverable_type,
        version=version,
        status=status,
        payload=payload or _review_payload(),
        note="正式复盘成果",
    )
    session.add(deliverable)
    await session.flush()
    quality = AgentQualityScore(
        org_id=admin.org_id,
        task_id=task.id,
        run_id=run.id,
        thread_id=thread.id,
        turn_id=turn.id,
        skill_run_id=skill_run.id,
        deliverable_id=deliverable.id,
        score=91,
        dimensions={"证据充分性": 92},
        issues=["建议补充粉丝转化数据"],
        suggestions=[],
        passed=True,
        iteration=0,
        evidence_refs=[],
    )
    session.add(quality)
    await session.commit()
    return project, account, content, thread, turn, task, run, skill_run, deliverable


def _video_script_payload(presentation_format: str) -> dict:
    return {
        "title": "拍摄稿",
        "hook": "先说结论。",
        "scenes": ["开场", "讲解", "结尾"],
        "duration_seconds": 60,
        "presentation_format": presentation_format,
    }


@pytest.mark.asyncio
async def test_account_inspection_uses_verified_business_artifact_type(
    client, session, admin
) -> None:
    payload = {
        **_review_payload(summary="账号体检已完成"),
        "artifact_type": "forged_type_is_ignored",
        "data_sufficiency": "partial",
        "missing_data": ["缺少转化数据"],
        "findings": ["已有内容播放证据"],
        "recommendations": ["补齐转化数据"],
        "optimization_suggestions": ["补齐转化数据"],
        "next_action": "导入最近30天转化数据",
        "participating_experts": [
            "06-operator",
            "01-positioning",
            "02-content-director",
        ],
        "critic": {
            "passed": True,
            "score": 91,
            "raw_tool_log": {"secret": "must not escape"},
        },
    }
    seeded = await _seed_artifact(
        session,
        admin,
        account_name="体检账号",
        payload=payload,
        skill_code="account_inspection",
    )
    account, deliverable = seeded[1], seeded[8]
    token = await _token(client, admin.email, "admin-pw-123")
    headers = _auth(token)

    listing = await client.get(
        f"/artifacts?account_id={account.id}&artifact_type=account_inspection_report",
        headers=headers,
    )
    detail = await client.get(f"/artifacts/{deliverable.id}", headers=headers)
    legacy_filter = await client.get(
        f"/artifacts?account_id={account.id}&artifact_type=review_report",
        headers=headers,
    )
    unknown = await client.get(
        f"/artifacts?account_id={account.id}&artifact_type=unknown_report",
        headers=headers,
    )

    assert listing.status_code == 200
    assert detail.status_code == 200
    projected = listing.json()["data"][0]
    assert detail.json() == projected
    assert projected["artifact_type"] == "account_inspection_report"
    assert {section["key"] for section in projected["sections"]} >= {
        "data_sufficiency",
        "missing_data",
        "findings",
        "recommendations",
        "next_action",
        "participating_experts",
        "critic",
    }
    assert [
        section["title"]
        for section in projected["sections"]
        if section["title"] == "优化建议"
    ] == ["优化建议"]
    assert "raw_tool_log" not in str(projected)
    assert legacy_filter.status_code == 200
    assert legacy_filter.json()["pagination"]["total"] == 0
    assert unknown.status_code == 422
    assert "unknown_report" not in unknown.text


@pytest.mark.asyncio
async def test_artifact_list_detail_share_business_projection_and_provenance(
    client, session, admin
):
    seeded = await _seed_artifact(session, admin, account_name="账号A")
    account, turn, task, run, skill_run, deliverable = (
        seeded[1],
        seeded[4],
        seeded[5],
        seeded[6],
        seeded[7],
        seeded[8],
    )
    token = await _token(client, admin.email, "admin-pw-123")
    headers = _auth(token)

    listing = await client.get(
        f"/artifacts?account_id={account.id}&artifact_type=review_report"
        "&status=ready_for_review&page=1&page_size=20",
        headers=headers,
    )

    assert listing.status_code == 200
    body = listing.json()
    assert body["pagination"] == {"page": 1, "page_size": 20, "total": 1, "pages": 1}
    listed = body["data"][0]
    detail = await client.get(f"/artifacts/{deliverable.id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json() == listed
    assert listed["id"] == deliverable.id
    assert listed["account_id"] == account.id
    assert listed["thread_id"] == seeded[3].id
    assert listed["turn_id"] == turn.id
    assert listed["run_id"] == run.id
    assert listed["skill_run_id"] == skill_run.id
    assert listed["task_id"] == task.id
    assert listed["artifact_type"] == "review_report"
    assert listed["title"] == "账号A运营复盘"
    assert listed["version"] == 1
    assert listed["status"] == "ready_for_review"
    assert listed["summary"] == "互动率回升，但涨粉速度仍需改善"
    assert {section["title"] for section in listed["sections"]} >= {
        "复盘周期",
        "核心指标",
        "亮点表现",
        "主要问题",
        "优化建议",
    }
    serialized = str(listed)
    assert "acceptance_items" not in serialized
    assert "Confirm that this item" not in serialized
    assert listed["evidence_refs"] == [
        {"kind": "account_metric_snapshot", "id": 81, "label": "近28天账号指标"}
    ]
    assert listed["evidence_summary"] == {
        "total": 1,
        "groups": [
            {
                "kind": "account_metric_snapshot",
                "label": "账号指标快照",
                "count": 1,
                "metric_count": 0,
                "period": None,
            }
        ],
    }
    assert listed["quality"] == {
        "score": 91.0,
        "passed": True,
        "issues": ["建议补充粉丝转化数据"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "presentation_format",
    ["spoken", "storyboard", "product_video", "image_post", "live_flow"],
)
async def test_video_script_artifact_projects_the_validated_presentation_format(
    client, session, admin, presentation_format: str,
) -> None:
    seeded = await _seed_artifact(
        session,
        admin,
        account_name=f"脚本格式-{presentation_format}",
        payload=_video_script_payload(presentation_format),
        skill_code="script_generation",
        deliverable_type=DeliverableType.VIDEO_SCRIPT,
    )
    token = await _token(client, admin.email, "admin-pw-123")

    detail = await client.get(
        f"/artifacts/{seeded[8].id}",
        headers=_auth(token),
    )
    listing = await client.get(
        f"/artifacts?account_id={seeded[1].id}&artifact_type=video_script",
        headers=_auth(token),
    )

    assert detail.status_code == 200
    assert listing.status_code == 200
    artifact = detail.json()
    assert listing.json()["data"] == [artifact]
    assert artifact["artifact_type"] == "video_script"
    assert artifact["presentation_format"] == presentation_format
    expected_type_labels = {
        "spoken": "口播拍摄稿",
        "storyboard": "分镜拍摄稿",
        "product_video": "产品视频拍摄稿",
        "image_post": "图文发布稿",
        "live_flow": "直播流程与话术稿",
    }
    assert artifact["presentation"]["type_label"] == expected_type_labels[
        presentation_format
    ]


@pytest.mark.asyncio
async def test_spoken_script_presentation_counts_one_artifact_not_its_scenes(
    client, session, admin,
) -> None:
    seeded = await _seed_artifact(
        session,
        admin,
        account_name="单条口播稿",
        payload=_video_script_payload("spoken"),
        skill_code="script_generation",
        deliverable_type=DeliverableType.VIDEO_SCRIPT,
    )
    token = await _token(client, admin.email, "admin-pw-123")

    response = await client.get(
        f"/artifacts/{seeded[8].id}",
        headers=_auth(token),
    )

    assert response.status_code == 200
    artifact = response.json()
    assert artifact["presentation"] == {
        "type_label": "口播拍摄稿",
        "completion_label": "已生成 1 条可直接拍摄的口播稿",
        "status_label": "待确认",
        "detail_action_label": "查看口播拍摄稿",
    }
    assert artifact["next_actions"] == [
        {
            "code": "create_shoot_task",
            "label": "创建拍摄任务",
            "requires_confirmation": True,
        },
        {
            "code": "export",
            "label": "导出内容",
            "requires_confirmation": False,
        },
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("deliverable_type", "payload", "expected_presentation", "forbidden_action"),
    [
        (
            DeliverableType.TOPIC_PLAN,
            {
                "theme": "下周增长选题",
                "topics": [{"title": f"选题 {index}"} for index in range(1, 6)],
                "posting_notes": [],
            },
            {
                "type_label": "选题清单",
                "completion_label": "已规划 5 个可执行选题",
                "status_label": "待确认",
                "detail_action_label": "查看 5 个选题",
            },
            "generate_production_briefs",
        ),
        (
            DeliverableType.PUBLISH_CALENDAR,
            {
                "period": "2026-08-10 至 2026-08-16",
                "items": [{"date": f"2026-08-{day:02d}"} for day in range(10, 17)],
                "operating_notes": [],
            },
            {
                "type_label": "内容排期表",
                "completion_label": "已安排 7 条内容发布顺序",
                "status_label": "待确认",
                "detail_action_label": "查看 7 条发布安排",
            },
            "add_to_schedule",
        ),
    ],
)
async def test_structured_deliverable_counts_drive_presentation_and_actions(
    client,
    session,
    admin,
    deliverable_type: DeliverableType,
    payload: dict,
    expected_presentation: dict,
    forbidden_action: str,
) -> None:
    seeded = await _seed_artifact(
        session,
        admin,
        account_name=f"结构化数量-{deliverable_type.value}",
        payload=payload,
        skill_code="structured_projection",
        deliverable_type=deliverable_type,
    )
    token = await _token(client, admin.email, "admin-pw-123")

    response = await client.get(
        f"/artifacts/{seeded[8].id}",
        headers=_auth(token),
    )

    assert response.status_code == 200
    artifact = response.json()
    assert artifact["presentation"] == expected_presentation
    expected_actions = [
        {
            "code": "export",
            "label": "导出内容",
            "requires_confirmation": False,
        },
    ]
    if deliverable_type == DeliverableType.PUBLISH_CALENDAR:
        expected_actions.insert(
            0,
            {
                "code": "add_to_schedule",
                "label": "加入内容排期",
                "requires_confirmation": True,
            },
        )
    assert artifact["next_actions"] == expected_actions
    if deliverable_type != DeliverableType.PUBLISH_CALENDAR:
        assert forbidden_action not in {
            action["code"] for action in artifact["next_actions"]
        }


@pytest.mark.asyncio
async def test_unknown_business_type_fails_closed_to_operations_report(
    client, session, admin, monkeypatch,
) -> None:
    seeded = await _seed_artifact(session, admin, account_name="未来交付类型")

    async def future_business_type(_session, _deliverable) -> str:
        return "future_deliverable_type"

    monkeypatch.setattr(
        "app.services.artifacts._business_artifact_type",
        future_business_type,
    )
    token = await _token(client, admin.email, "admin-pw-123")

    response = await client.get(
        f"/artifacts/{seeded[8].id}",
        headers=_auth(token),
    )

    assert response.status_code == 200
    artifact = response.json()
    assert artifact["presentation"] == {
        "type_label": "运营报告",
        "completion_label": "已生成运营报告",
        "status_label": "待确认",
        "detail_action_label": "查看完整报告",
    }
    assert artifact["next_actions"] == [
        {
            "code": "export",
            "label": "导出内容",
            "requires_confirmation": False,
        },
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("presentation_format", ["spoken", "product_video"])
async def test_video_script_revision_inherits_missing_presentation_format_from_source(
    client, session, admin, presentation_format: str,
) -> None:
    seeded = await _seed_artifact(
        session,
        admin,
        account_name=f"修订格式-{presentation_format}",
        payload=_video_script_payload(presentation_format),
        skill_code="script_generation",
        deliverable_type=DeliverableType.VIDEO_SCRIPT,
    )
    revision_payload = _video_script_payload("storyboard")
    revision_payload.pop("presentation_format")
    revision_payload["hook"] = "更新后的开场。"
    token = await _token(client, admin.email, "admin-pw-123")

    response = await client.post(
        "/artifact-revisions",
        headers=_auth(token),
        json={"artifact_id": seeded[8].id, "payload": revision_payload},
    )

    assert response.status_code == 201
    assert response.json()["version"] == 2
    assert response.json()["presentation_format"] == presentation_format
    persisted = await session.scalar(
        select(Deliverable).where(
            Deliverable.content_item_id == seeded[2].id,
            Deliverable.type == DeliverableType.VIDEO_SCRIPT,
            Deliverable.version == 2,
        )
    )
    assert persisted is not None
    assert persisted.payload["presentation_format"] == presentation_format


@pytest.mark.asyncio
async def test_artifact_aggregates_raw_field_observations_for_operator_summary(
    client, session, admin
) -> None:
    payload = _review_payload()
    payload["evidence_refs"] = [
        {
            "kind": "field_observation",
            "id": index,
            "label": f"field_observation #{index}",
            "metric": "播放量" if index <= 40 else "互动率",
            "period_start": "2026-07-01",
            "period_end": "2026-07-30",
        }
        for index in range(1, 80)
    ]
    seeded = await _seed_artifact(
        session,
        admin,
        account_name="证据聚合账号",
        payload=payload,
    )
    token = await _token(client, admin.email, "admin-pw-123")

    response = await client.get(
        f"/artifacts/{seeded[8].id}",
        headers=_auth(token),
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["evidence_refs"]) == 79
    assert body["evidence_summary"] == {
        "total": 79,
        "groups": [
            {
                "kind": "field_observation",
                "label": "账号数据字段",
                "count": 79,
                "metric_count": 2,
                "period": "2026-07-01 至 2026-07-30",
            }
        ],
    }


@pytest.mark.asyncio
async def test_artifact_queries_and_actions_are_isolated_by_account(client, session, admin):
    account_a = await _seed_artifact(session, admin, account_name="账号A")
    account_b = await _seed_artifact(session, admin, account_name="账号B")
    legacy_content = ContentItem(
        project_id=account_a[0].id,
        account_id=None,
        created_by_id=admin.id,
        title="未绑定账号旧成果",
    )
    session.add(legacy_content)
    await session.flush()
    account_a_draft = Deliverable(
        content_item_id=account_a[2].id,
        thread_id=account_a[3].id,
        turn_id=account_a[4].id,
        run_id=account_a[6].id,
        skill_run_id=account_a[7].id,
        agent_code="06-operator",
        type=DeliverableType.REVIEW_REPORT,
        version=2,
        status=DeliverableStatus.DRAFT,
        payload=_review_payload(summary="账号A草稿版本"),
    )
    legacy = Deliverable(
        content_item_id=legacy_content.id,
        agent_code="06-operator",
        type=DeliverableType.REVIEW_REPORT,
        version=1,
        status=DeliverableStatus.PENDING_REVIEW,
        payload=_review_payload(),
    )
    session.add_all([account_a_draft, legacy])
    await session.commit()
    token = await _token(client, admin.email, "admin-pw-123")
    headers = _auth(token)

    listing = await client.get(
        f"/artifacts?account_id={account_a[1].id}&page=1&page_size=1",
        headers=headers,
    )
    second_page = await client.get(
        f"/artifacts?account_id={account_a[1].id}&page=2&page_size=1",
        headers=headers,
    )

    assert listing.status_code == 200
    assert listing.json()["pagination"] == {
        "page": 1,
        "page_size": 1,
        "total": 2,
        "pages": 2,
    }
    assert [row["id"] for row in listing.json()["data"]] == [account_a_draft.id]
    assert second_page.status_code == 200
    assert [row["id"] for row in second_page.json()["data"]] == [account_a[8].id]
    hidden = await client.get(f"/artifacts/{account_b[8].id}", headers=headers)
    assert hidden.status_code == 200  # admin can view it only as its own account-scoped identity
    assert hidden.json()["account_id"] == account_b[1].id
    legacy_detail = await client.get(f"/artifacts/{legacy.id}", headers=headers)
    assert legacy_detail.status_code == 404


@pytest.mark.asyncio
async def test_artifact_types_filter_is_validated_before_pagination(client, session, admin):
    seeded = await _seed_artifact(session, admin, account_name="多类型分页账号")
    topic = Deliverable(
        content_item_id=seeded[2].id,
        thread_id=seeded[3].id,
        turn_id=seeded[4].id,
        run_id=seeded[6].id,
        skill_run_id=seeded[7].id,
        agent_code="02-content-director",
        type=DeliverableType.TOPIC_PLAN,
        version=1,
        status=DeliverableStatus.PENDING_REVIEW,
        payload={"theme": "厨房收纳", "topics": ["抽屉整理"], "period": "本周"},
    )
    later_review = Deliverable(
        content_item_id=seeded[2].id,
        thread_id=seeded[3].id,
        turn_id=seeded[4].id,
        run_id=seeded[6].id,
        skill_run_id=seeded[7].id,
        agent_code="06-operator",
        type=DeliverableType.REVIEW_REPORT,
        version=2,
        status=DeliverableStatus.PENDING_REVIEW,
        payload=_review_payload(summary="排在选题之后的复盘"),
    )
    session.add_all([topic, later_review])
    await session.commit()
    token = await _token(client, admin.email, "admin-pw-123")

    response = await client.get(
        f"/artifacts?account_id={seeded[1].id}&artifact_types=topic_plan&page=1&page_size=1",
        headers=_auth(token),
    )

    assert response.status_code == 200
    assert response.json()["pagination"] == {"page": 1, "page_size": 1, "total": 1, "pages": 1}
    assert [row["id"] for row in response.json()["data"]] == [topic.id]

    invalid = await client.get(
        f"/artifacts?account_id={seeded[1].id}&artifact_types=topic_plan&artifact_types=unknown_type",
        headers=_auth(token),
    )
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_artifact_created_date_filter_runs_before_pagination_with_inclusive_utc_days(
    client, session, admin
):
    seeded = await _seed_artifact(session, admin, account_name="日期分页账号")
    matching = seeded[8]
    matching.created_at = datetime(2026, 7, 10, 23, 59, 59, tzinfo=UTC)
    newer_outside_range = Deliverable(
        content_item_id=seeded[2].id,
        thread_id=seeded[3].id,
        turn_id=seeded[4].id,
        run_id=seeded[6].id,
        skill_run_id=seeded[7].id,
        agent_code="06-operator",
        type=DeliverableType.REVIEW_REPORT,
        version=2,
        status=DeliverableStatus.PENDING_REVIEW,
        payload=_review_payload(summary="日期范围外的较新复盘"),
        created_at=datetime(2026, 7, 11, 0, 0, 0, tzinfo=UTC),
    )
    session.add(newer_outside_range)
    await session.commit()
    token = await _token(client, admin.email, "admin-pw-123")

    response = await client.get(
        f"/artifacts?account_id={seeded[1].id}"
        "&created_from=2026-07-10&created_to=2026-07-10&page=1&page_size=1",
        headers=_auth(token),
    )

    assert response.status_code == 200
    assert response.json()["pagination"] == {
        "page": 1,
        "page_size": 1,
        "total": 1,
        "pages": 1,
    }
    assert [row["id"] for row in response.json()["data"]] == [matching.id]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "expected_detail"),
    [
        ("created_from=not-a-date", "valid date"),
        (
            "created_from=2026-07-11&created_to=2026-07-10",
            "created_from must be on or before created_to",
        ),
    ],
)
async def test_artifact_created_date_filter_rejects_invalid_or_reversed_ranges(
    client, session, admin, query, expected_detail
):
    seeded = await _seed_artifact(session, admin, account_name=f"无效日期-{query}")
    token = await _token(client, admin.email, "admin-pw-123")

    response = await client.get(
        f"/artifacts?account_id={seeded[1].id}&{query}",
        headers=_auth(token),
    )

    assert response.status_code == 422
    assert expected_detail in response.text


@pytest.mark.asyncio
async def test_member_cannot_enumerate_view_or_change_another_account_artifact(
    client, session, admin, member
):
    account_a = await _seed_artifact(session, admin, account_name="成员可见账号")
    account_b = await _seed_artifact(session, admin, account_name="成员隐藏账号")
    session.add(
        ProjectMembership(
            project_id=account_a[0].id,
            user_id=member.id,
            role=WorkspaceRole.EDITOR,
        )
    )
    await session.commit()
    token = await _token(client, member.email, "user-pw-123")
    headers = _auth(token)

    visible_list = await client.get(f"/artifacts?account_id={account_a[1].id}", headers=headers)
    hidden_list = await client.get(f"/artifacts?account_id={account_b[1].id}", headers=headers)
    hidden_detail = await client.get(f"/artifacts/{account_b[8].id}", headers=headers)
    hidden_revision = await client.post(
        "/artifact-revisions",
        headers=headers,
        json={
            "artifact_id": account_b[8].id,
            "payload": _review_payload(summary="越权修改"),
        },
    )
    hidden_acceptance = await client.post(
        "/artifact-acceptances",
        headers=headers,
        json={"artifact_id": account_b[8].id},
    )

    assert visible_list.status_code == 200
    assert [row["id"] for row in visible_list.json()["data"]] == [account_a[8].id]
    assert hidden_list.status_code == 404
    assert hidden_detail.status_code == 404
    assert hidden_revision.status_code == 404
    assert hidden_acceptance.status_code == 404


@pytest.mark.asyncio
async def test_artifact_revision_increments_version_and_rejects_stale_source(
    client, session, admin
):
    seeded = await _seed_artifact(session, admin, account_name="版本账号")
    source = seeded[8]
    token = await _token(client, admin.email, "admin-pw-123")
    headers = _auth(token)
    revised_payload = _review_payload(summary="第二版复盘已补充转化数据")

    created = await client.post(
        "/artifact-revisions",
        headers=headers,
        json={
            "artifact_id": source.id,
            "payload": revised_payload,
            "note": "补充转化数据",
        },
    )

    assert created.status_code == 201
    revision = created.json()
    assert revision["version"] == 2
    assert revision["status"] == "ready_for_review"
    assert revision["thread_id"] == seeded[3].id
    assert revision["turn_id"] == seeded[4].id
    assert revision["run_id"] == seeded[6].id
    assert revision["skill_run_id"] == seeded[7].id
    await session.refresh(source)
    assert source.status == DeliverableStatus.SUPERSEDED

    stale = await client.post(
        "/artifact-revisions",
        headers=headers,
        json={"artifact_id": source.id, "payload": revised_payload},
    )
    assert stale.status_code == 409
    versions = list(
        await session.scalars(
            select(Deliverable)
            .where(
                Deliverable.content_item_id == seeded[2].id,
                Deliverable.type == DeliverableType.REVIEW_REPORT,
            )
            .order_by(Deliverable.version)
        )
    )
    assert [row.version for row in versions] == [1, 2]


@pytest.mark.asyncio
async def test_account_inspection_revision_accepts_business_recommendations_alias(
    client, session, admin
):
    seeded = await _seed_artifact(
        session,
        admin,
        account_name="inspection-revision",
        skill_code="account_inspection",
    )
    source = seeded[8]
    token = await _token(client, admin.email, "admin-pw-123")
    headers = _auth(token)
    revised_payload = _review_payload(summary="补齐数据后的账号体检")
    revised_payload["recommendations"] = revised_payload.pop(
        "optimization_suggestions"
    )

    response = await client.post(
        "/artifact-revisions",
        headers=headers,
        json={"artifact_id": source.id, "payload": revised_payload},
    )

    assert response.status_code == 201
    persisted = await session.scalar(
        select(Deliverable).where(
            Deliverable.content_item_id == seeded[2].id,
            Deliverable.type == DeliverableType.REVIEW_REPORT,
            Deliverable.version == 2,
        )
    )
    assert persisted is not None
    assert persisted.payload["optimization_suggestions"] == revised_payload[
        "recommendations"
    ]
    assert "recommendations" not in persisted.payload


@pytest.mark.asyncio
async def test_artifact_acceptance_is_idempotent_until_a_newer_version_exists(
    client, session, admin
):
    seeded = await _seed_artifact(
        session,
        admin,
        account_name="采纳账号",
        status=DeliverableStatus.DRAFT,
    )
    older = seeded[8]
    selected = Deliverable(
        content_item_id=seeded[2].id,
        thread_id=seeded[3].id,
        turn_id=seeded[4].id,
        run_id=seeded[6].id,
        skill_run_id=seeded[7].id,
        agent_code=older.agent_code,
        type=older.type,
        version=2,
        status=DeliverableStatus.PENDING_REVIEW,
        payload=_review_payload(summary="待采纳第二版"),
    )
    session.add(selected)
    await session.commit()
    token = await _token(client, admin.email, "admin-pw-123")
    headers = _auth(token)

    first = await client.post(
        "/artifact-acceptances",
        headers=headers,
        json={"artifact_id": selected.id},
    )
    idempotent = await client.post(
        "/artifact-acceptances",
        headers=headers,
        json={"artifact_id": selected.id},
    )
    later_draft = Deliverable(
        content_item_id=seeded[2].id,
        thread_id=seeded[3].id,
        turn_id=seeded[4].id,
        run_id=seeded[6].id,
        skill_run_id=seeded[7].id,
        agent_code=older.agent_code,
        type=older.type,
        version=3,
        status=DeliverableStatus.DRAFT,
        payload=_review_payload(summary="并发产生的第三版"),
    )
    session.add(later_draft)
    await session.commit()
    stale = await client.post(
        "/artifact-acceptances",
        headers=headers,
        json={"artifact_id": selected.id},
    )

    assert first.status_code == 200
    assert idempotent.status_code == 200
    assert idempotent.json() == first.json()
    assert first.json()["status"] == "accepted"
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "ARTIFACT_VERSION_CONFLICT"
    assert stale.json()["detail"]["details"] == {
        "artifact_id": selected.id,
        "selected_version": 2,
        "latest_version": 3,
    }
    await session.refresh(older)
    await session.refresh(selected)
    await session.refresh(later_draft)
    assert older.status == DeliverableStatus.SUPERSEDED
    assert selected.status == DeliverableStatus.APPROVED
    assert later_draft.status == DeliverableStatus.DRAFT


@pytest.mark.asyncio
async def test_artifact_first_acceptance_rejects_stale_version_without_mutation(
    client,
    session,
    admin,
) -> None:
    seeded = await _seed_artifact(
        session,
        admin,
        account_name="stale-first-acceptance",
    )
    source = seeded[8]
    token = await _token(client, admin.email, "admin-pw-123")
    headers = _auth(token)
    second = await client.post(
        "/artifact-revisions",
        headers=headers,
        json={
            "artifact_id": source.id,
            "payload": _review_payload(summary="second version"),
        },
    )
    assert second.status_code == 201
    second_id = second.json()["id"]
    third = await client.post(
        "/artifact-revisions",
        headers=headers,
        json={
            "artifact_id": second_id,
            "payload": _review_payload(summary="third version"),
        },
    )
    assert third.status_code == 201
    third_id = third.json()["id"]

    stale = await client.post(
        "/artifact-acceptances",
        headers=headers,
        json={"artifact_id": second_id},
    )

    assert stale.status_code == 409
    assert stale.json()["detail"] == {
        "code": "ARTIFACT_VERSION_CONFLICT",
        "message": "成果版本已更新，请刷新后重试",
        "details": {
            "artifact_id": second_id,
            "selected_version": 2,
            "latest_version": 3,
        },
    }
    second_row = await session.get(Deliverable, second_id)
    third_row = await session.get(Deliverable, third_id)
    assert second_row is not None
    assert third_row is not None
    assert second_row.status == DeliverableStatus.SUPERSEDED
    assert third_row.status == DeliverableStatus.PENDING_REVIEW
    assert third_row.thread_id == seeded[3].id
    assert third_row.turn_id == seeded[4].id
    assert third_row.run_id == seeded[6].id
    assert third_row.skill_run_id == seeded[7].id


@pytest.mark.asyncio
async def test_reviewer_cannot_revise_or_accept_artifact(client, session, admin, member):
    seeded = await _seed_artifact(session, admin, account_name="只读账号")
    session.add(
        ProjectMembership(
            project_id=seeded[0].id,
            user_id=member.id,
            role=WorkspaceRole.REVIEWER,
        )
    )
    await session.commit()
    token = await _token(client, member.email, "user-pw-123")
    headers = _auth(token)

    revision = await client.post(
        "/artifact-revisions",
        headers=headers,
        json={"artifact_id": seeded[8].id, "payload": _review_payload()},
    )
    acceptance = await client.post(
        "/artifact-acceptances",
        headers=headers,
        json={"artifact_id": seeded[8].id},
    )

    assert revision.status_code == 403
    assert acceptance.status_code == 403


@pytest.mark.asyncio
async def test_corrupt_cross_account_provenance_is_hidden_before_pagination_and_actions(
    client, session, admin
):
    account_a = await _seed_artifact(session, admin, account_name="provenance-a")
    account_b = await _seed_artifact(session, admin, account_name="provenance-b")
    corrupt_turn = Deliverable(
        content_item_id=account_a[2].id,
        thread_id=account_a[3].id,
        turn_id=account_b[4].id,
        agent_code="06-operator",
        type=DeliverableType.REVIEW_REPORT,
        version=2,
        status=DeliverableStatus.PENDING_REVIEW,
        payload=_review_payload(summary="foreign turn must never be exposed"),
    )
    corrupt_run = Deliverable(
        content_item_id=account_a[2].id,
        thread_id=account_a[3].id,
        turn_id=account_a[4].id,
        run_id=account_b[6].id,
        agent_code="06-operator",
        type=DeliverableType.REVIEW_REPORT,
        version=3,
        status=DeliverableStatus.PENDING_REVIEW,
        payload=_review_payload(summary="foreign run must never be exposed"),
    )
    corrupt_skill_run = Deliverable(
        content_item_id=account_a[2].id,
        thread_id=account_a[3].id,
        turn_id=account_a[4].id,
        run_id=account_a[6].id,
        skill_run_id=account_b[7].id,
        agent_code="06-operator",
        type=DeliverableType.REVIEW_REPORT,
        version=4,
        status=DeliverableStatus.PENDING_REVIEW,
        payload=_review_payload(summary="foreign skill run must never be exposed"),
    )
    corrupt_quality_task = Deliverable(
        content_item_id=account_a[2].id,
        thread_id=account_a[3].id,
        turn_id=account_a[4].id,
        run_id=account_a[6].id,
        skill_run_id=account_a[7].id,
        agent_code="06-operator",
        type=DeliverableType.REVIEW_REPORT,
        version=5,
        status=DeliverableStatus.PENDING_REVIEW,
        payload=_review_payload(summary="foreign quality task must never be exposed"),
    )
    corrupt_artifacts = [
        corrupt_turn,
        corrupt_run,
        corrupt_skill_run,
        corrupt_quality_task,
    ]
    session.add_all(corrupt_artifacts)
    await session.flush()
    session.add(
        AgentQualityScore(
            org_id=admin.org_id,
            task_id=account_b[5].id,
            run_id=account_a[6].id,
            thread_id=account_a[3].id,
            turn_id=account_a[4].id,
            skill_run_id=account_a[7].id,
            deliverable_id=corrupt_quality_task.id,
            score=99,
            dimensions={},
            issues=["cross-account-quality-must-not-leak"],
            suggestions=[],
            passed=True,
            iteration=0,
            evidence_refs=[],
        )
    )
    await session.commit()
    token = await _token(client, admin.email, "admin-pw-123")
    headers = _auth(token)

    listing = await client.get(
        f"/artifacts?account_id={account_a[1].id}&page=1&page_size=20",
        headers=headers,
    )

    assert listing.status_code == 200
    assert listing.json()["pagination"]["total"] == 1
    assert [row["id"] for row in listing.json()["data"]] == [account_a[8].id]
    listed = listing.json()["data"][0]
    assert listed["turn_id"] == account_a[4].id != account_b[4].id
    assert listed["run_id"] == account_a[6].id != account_b[6].id
    assert listed["skill_run_id"] == account_a[7].id != account_b[7].id
    assert listed["task_id"] == account_a[5].id != account_b[5].id
    serialized = str(listing.json())
    assert "cross-account-quality-must-not-leak" not in serialized
    for corrupt in corrupt_artifacts:
        detail = await client.get(f"/artifacts/{corrupt.id}", headers=headers)
        revision = await client.post(
            "/artifact-revisions",
            headers=headers,
            json={"artifact_id": corrupt.id, "payload": _review_payload()},
        )
        acceptance = await client.post(
            "/artifact-acceptances",
            headers=headers,
            json={"artifact_id": corrupt.id},
        )
        assert detail.status_code == 404
        assert revision.status_code == 409
        assert revision.json()["detail"]["code"] == "ARTIFACT_LINEAGE_CONFLICT"
        assert acceptance.status_code == 404


@pytest.mark.asyncio
async def test_artifact_projection_recursively_removes_internal_data_but_keeps_art_prompts(
    client, session, admin
):
    unsafe_payload = _review_payload()
    unsafe_payload["key_metrics"] = {
        "engagement": "5.2%",
        "raw_tool_log": {"authorization": "secret-tool-output"},
        "nested": {
            "debug_trace": "private-debug-trace",
            "model-config": {"temperature": 0.9},
            "rawToolLog": "private-camel-tool-log",
            "systemPrompt": "private-camel-system-prompt",
            "policy_kernel": "private-policy",
            "business_value": "retained-business-value",
        },
    }
    unsafe_payload["highlights"] = [
        "legitimate-highlight",
        "Please CONFIRM   that the selected account matches this artifact.",
    ]
    unsafe_payload["internal_checklist"] = ["private-checklist-copy"]
    unsafe_payload["runtime_trace"] = {"step": "private-runtime-step"}
    seeded = await _seed_artifact(
        session,
        admin,
        account_name="sanitizer-account",
        payload=unsafe_payload,
    )
    art_prompt = Deliverable(
        content_item_id=seeded[2].id,
        thread_id=seeded[3].id,
        turn_id=seeded[4].id,
        run_id=seeded[6].id,
        skill_run_id=seeded[7].id,
        agent_code="03-art-director",
        type=DeliverableType.ART_PROMPT,
        version=1,
        status=DeliverableStatus.PENDING_REVIEW,
        payload={
            "visual_style": "clean editorial",
            "prompts": ["sunlit glass office", "close-up installation detail"],
            "negative_prompt": "watermark, distorted hands",
            "aspect_ratio": "9:16",
            "system_prompt": "private-system-prompt",
            "debug": {"trace": "private-art-debug"},
        },
    )
    session.add(art_prompt)
    await session.commit()
    token = await _token(client, admin.email, "admin-pw-123")
    headers = _auth(token)

    review = await client.get(f"/artifacts/{seeded[8].id}", headers=headers)
    art = await client.get(f"/artifacts/{art_prompt.id}", headers=headers)

    assert review.status_code == 200
    review_serialized = str(review.json())
    assert "retained-business-value" in review_serialized
    assert "legitimate-highlight" in review_serialized
    for private_value in [
        "secret-tool-output",
        "private-debug-trace",
        "private-camel-tool-log",
        "private-camel-system-prompt",
        "private-policy",
        "private-checklist-copy",
        "private-runtime-step",
        "selected account matches",
    ]:
        assert private_value not in review_serialized

    assert art.status_code == 200
    art_sections = {section["key"]: section["content"] for section in art.json()["sections"]}
    assert art_sections["prompts"] == [
        "sunlit glass office",
        "close-up installation detail",
    ]
    assert art_sections["negative_prompt"] == "watermark, distorted hands"
    assert "private-system-prompt" not in str(art.json())
    assert "private-art-debug" not in str(art.json())


@pytest.mark.asyncio
async def test_every_deliverable_status_maps_and_filters_to_business_status(client, session, admin):
    seeded = await _seed_artifact(
        session,
        admin,
        account_name="status-account",
        status=DeliverableStatus.DRAFT,
    )
    statuses = [
        (DeliverableStatus.DRAFT, "draft", "草稿"),
        (DeliverableStatus.PENDING_REVIEW, "ready_for_review", "待确认"),
        (DeliverableStatus.APPROVED, "accepted", "已确认"),
        (DeliverableStatus.REJECTED, "revision_requested", "正在修改"),
        (DeliverableStatus.SUPERSEDED, "superseded", "历史版本"),
    ]
    artifacts = {DeliverableStatus.DRAFT: seeded[8]}
    for version, (internal_status, _business_status, _status_label) in enumerate(
        statuses[1:], start=2
    ):
        row = Deliverable(
            content_item_id=seeded[2].id,
            thread_id=seeded[3].id,
            turn_id=seeded[4].id,
            run_id=seeded[6].id,
            skill_run_id=seeded[7].id,
            agent_code="06-operator",
            type=DeliverableType.REVIEW_REPORT,
            version=version,
            status=internal_status,
            payload=_review_payload(summary=f"status-{internal_status.value}"),
        )
        session.add(row)
        artifacts[internal_status] = row
    await session.commit()
    token = await _token(client, admin.email, "admin-pw-123")
    headers = _auth(token)

    for internal_status, business_status, status_label in statuses:
        response = await client.get(
            f"/artifacts?account_id={seeded[1].id}&status={business_status}",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["pagination"]["total"] == 1
        assert response.json()["data"][0]["id"] == artifacts[internal_status].id
        assert response.json()["data"][0]["status"] == business_status
        assert response.json()["data"][0]["presentation"]["status_label"] == status_label
        if business_status in {"draft", "revision_requested", "superseded"}:
            assert response.json()["data"][0]["next_actions"] == []


@pytest.mark.asyncio
async def test_invalid_revision_does_not_supersede_source(client, session, admin):
    seeded = await _seed_artifact(session, admin, account_name="invalid-revision")
    source = seeded[8]
    token = await _token(client, admin.email, "admin-pw-123")
    headers = _auth(token)

    response = await client.post(
        "/artifact-revisions",
        headers=headers,
        json={
            "artifact_id": source.id,
            "payload": {"period": "missing required fields"},
        },
    )

    assert response.status_code == 422
    await session.refresh(source)
    assert source.status == DeliverableStatus.PENDING_REVIEW
    versions = list(
        await session.scalars(
            select(Deliverable).where(
                Deliverable.content_item_id == seeded[2].id,
                Deliverable.type == DeliverableType.REVIEW_REPORT,
            )
        )
    )
    assert [row.version for row in versions] == [1]


@pytest.mark.asyncio
async def test_revision_integrity_race_rolls_back_without_duplicate_version(
    client, session, admin, monkeypatch
):
    seeded = await _seed_artifact(session, admin, account_name="revision-race")
    source = seeded[8]
    content_item_id = source.content_item_id
    token = await _token(client, admin.email, "admin-pw-123")
    headers = _auth(token)
    original_flush = session.flush

    async def collide_on_revision(objects=None):
        if any(
            isinstance(row, Deliverable)
            and row.content_item_id == source.content_item_id
            and row.type == source.type
            and row.version == 2
            for row in session.new
        ):
            raise IntegrityError("INSERT deliverables", {}, Exception("unique collision"))
        return await original_flush(objects)

    monkeypatch.setattr(session, "flush", collide_on_revision)
    response = await client.post(
        "/artifact-revisions",
        headers=headers,
        json={
            "artifact_id": source.id,
            "payload": _review_payload(summary="race loser"),
        },
    )

    assert response.status_code == 409
    await session.refresh(source)
    assert source.status == DeliverableStatus.PENDING_REVIEW
    versions = list(
        await session.scalars(
            select(Deliverable).where(
                Deliverable.content_item_id == content_item_id,
                Deliverable.type == DeliverableType.REVIEW_REPORT,
            )
        )
    )
    assert [row.version for row in versions] == [1]
