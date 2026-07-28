from decimal import Decimal

import pytest
from sqlalchemy import select

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
        "evidence_refs": [
            {"kind": "account_metric_snapshot", "id": 81, "label": "近28天账号指标"}
        ],
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
        skill_code="account_review",
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
        type=DeliverableType.REVIEW_REPORT,
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
    assert listed["quality"] == {
        "score": 91.0,
        "passed": True,
        "issues": ["建议补充粉丝转化数据"],
    }


@pytest.mark.asyncio
async def test_artifact_queries_and_actions_are_isolated_by_account(
    client, session, admin
):
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

    visible_list = await client.get(
        f"/artifacts?account_id={account_a[1].id}", headers=headers
    )
    hidden_list = await client.get(
        f"/artifacts?account_id={account_b[1].id}", headers=headers
    )
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
        json={"artifact_id": source.id, "payload": revised_payload, "note": "补充转化数据"},
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
async def test_artifact_acceptance_is_idempotent_and_supersedes_other_active_versions(
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
    second = await client.post(
        "/artifact-acceptances",
        headers=headers,
        json={"artifact_id": selected.id},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["status"] == "accepted"
    await session.refresh(older)
    await session.refresh(selected)
    await session.refresh(later_draft)
    assert older.status == DeliverableStatus.SUPERSEDED
    assert selected.status == DeliverableStatus.APPROVED
    assert later_draft.status == DeliverableStatus.SUPERSEDED


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
