"""Release contract for the complete account-scoped operations worker loop.

The first two tests are intentionally end-to-end RED contracts for the two
remaining production gaps. They use the public submission boundary and only
replace the model provider. The manual-publication test locks the durable
lifecycle that is already available today.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.core.security import create_access_token
from app.llm.adapters import CompletionResult
from app.llm.gateway import LLMGateway
from app.models import (
    AgentRun,
    AgentToolCall,
    BrainTask,
    ContentScheduleEntry,
    ConversationTurn,
    RunRevision,
    SkillRun,
)
from app.models.enums import BrainTaskStatus, BrainTaskType, DeliverableStatus, DeliverableType
from app.worker import _execute_v2_conversation_run
from tests.test_artifacts_api import _seed_artifact

WEEKLY_REQUEST = "结合最近数据和对标内容，规划并制作下周抖音内容"
PRICE_STEERING = "第一条不要讲价格"


@pytest.fixture(autouse=True)
def _typed_runtime(monkeypatch):
    async def no_queue(*, run_id: int) -> None:
        del run_id

    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    monkeypatch.setattr(settings, "main_agent_typed_runtime_enabled", True)
    monkeypatch.setattr("app.api.conversations.enqueue_agent_runtime", no_queue)


def _headers(user) -> dict[str, str]:
    token = create_access_token(str(user.id), user.role.value)
    return {"Authorization": f"Bearer {token}"}


class WeeklyOperationsProvider:
    """Deterministic provider boundary; routing/runtime remain production code."""

    provider = "weekly-operations-contract"

    async def complete(self, model, messages, options=None):
        del messages
        if (options or {}).get("response_format") == {"type": "json_object"}:
            content = json.dumps(
                {
                    "mode": "skill",
                    "intent": "weekly_operations",
                    "confidence": 1,
                    "reason": "The request asks for an evidence-backed weekly production package.",
                    "skill_code": "operation_iteration",
                    "requires_account_context": True,
                    "requires_operation_task": True,
                    "missing_field": None,
                    "clarifying_question": None,
                },
                ensure_ascii=False,
            )
        else:
            content = "已完成下周内容规划，请查看 5 条拍摄稿和 7 天手动发布安排。"
        return CompletionResult(content, model, 8, 12, 20)

    async def stream(self, model, messages, options=None):
        del model, messages, options
        yield "正在准备下周内容。"


async def _account_and_thread(client, admin, *, nickname: str):
    account_response = await client.post(
        "/accounts",
        headers=_headers(admin),
        json={"nickname": nickname, "platform": "douyin"},
    )
    assert account_response.status_code == 201
    account = account_response.json()
    thread_response = await client.post(
        "/brain/conversations",
        headers=_headers(admin),
        json={"account_id": account["id"], "title": nickname},
    )
    assert thread_response.status_code == 201
    return account, thread_response.json()


@pytest.mark.asyncio
async def test_weekly_request_reaches_real_operation_worker_without_hidden_source_id(
    client,
    session,
    admin,
    monkeypatch,
) -> None:
    """A plain operator request must not require a server-only artifact id."""

    account, thread = await _account_and_thread(
        client,
        admin,
        nickname="weekly-worker-contract",
    )
    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.gateway",
        LLMGateway(adapters={"deepseek": WeeklyOperationsProvider()}),
    )
    submitted = await client.post(
        f"/brain/conversations/{thread['id']}/turns",
        headers=_headers(admin),
        json={
            "client_message_id": "weekly-worker-source",
            "message": WEEKLY_REQUEST,
        },
    )
    assert submitted.status_code == 202
    run = await session.get(AgentRun, submitted.json()["run"]["id"])
    assert run is not None
    assert "trusted_structured_input" not in run.request_payload

    # This currently raises validation for confirmed_review_artifact_id. The
    # release contract requires the public request to enter the real worker
    # without a client fabricating that server-owned binding.
    result = await _execute_v2_conversation_run(
        session,
        run=run,
        worker_id="weekly-worker-contract",
    )

    await session.refresh(run)
    turn = await session.get(ConversationTurn, submitted.json()["turn"]["id"])
    root = await session.scalar(
        select(SkillRun).where(
            SkillRun.run_id == run.id,
            SkillRun.skill_code == "operation_iteration",
        )
    )
    assert result.status in {"running", "waiting_user", "completed"}
    assert turn is not None and turn.intent["skill_code"] == "operation_iteration"
    assert root is not None
    assert root.input_snapshot["account_id"] == account["id"]
    assert root.input_snapshot["cycle_days"] == 7
    assert root.input_snapshot["topic_count"] == 5
    assert root.output_snapshot["report"]["interrupt"] == {
        "kind": "operation_evidence_required",
        "missing_domains": ["account_or_content_data", "benchmarks"],
    }


@pytest.mark.asyncio
async def test_price_steering_creates_partial_revision_from_scripts_forward(
    client,
    session,
    admin,
) -> None:
    """The exact operator constraint must reuse research instead of full recompute."""

    _account, thread = await _account_and_thread(
        client,
        admin,
        nickname="weekly-steering-contract",
    )
    source_turn = ConversationTurn(
        thread_id=thread["id"],
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id="weekly-steering-source",
        user_input=WEEKLY_REQUEST,
        status="running",
    )
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="下周抖音内容",
        type=BrainTaskType.CONTENT_CREATION,
        status=BrainTaskStatus.RUNNING,
    )
    session.add_all([source_turn, task])
    await session.flush()
    source_run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        task_id=task.id,
        thread_id=thread["id"],
        turn_id=source_turn.id,
        client_message_id="weekly-steering-source-run",
        status="running",
        phase="running",
        request_payload={},
    )
    session.add(source_run)
    await session.flush()
    session.add(
        SkillRun(
            org_id=admin.org_id,
            thread_id=thread["id"],
            turn_id=source_turn.id,
            run_id=source_run.id,
            task_id=task.id,
            idempotency_key="weekly-steering-operation",
            skill_code="operation_iteration",
            skill_version=1,
            status="running",
            input_snapshot={"confirmed_review_artifact_id": 1, "cycle_days": 7},
            output_snapshot={},
        )
    )
    await session.commit()

    response = await client.post(
        f"/brain/conversations/{thread['id']}/turns",
        headers=_headers(admin),
        json={
            "client_message_id": "weekly-price-steering",
            "message": PRICE_STEERING,
            "target_turn_id": source_turn.id,
        },
    )

    assert response.status_code == 202
    assert response.json()["steering_explanation"] == "已补充到当前任务的要求中。"
    assert response.json()["turn"]["target_turn_id"] == source_turn.id
    assert response.json()["turn"]["steering_mode"] == "supplement"
    revision = await session.scalar(
        select(RunRevision).where(RunRevision.source_run_id == source_run.id)
    )
    assert revision is not None
    assert revision.mode == "partial"
    assert revision.changed_constraints == {"offer_terms": {"operation": "changed"}}
    assert revision.direct_affected_steps == ["script_generation"]
    assert revision.affected_steps == [
        "script_generation",
        "visual_brief_generation",
        "quality_review",
        "content_calendar_planning",
        "publishing_preparation",
    ]
    assert "read_account_data" not in revision.affected_steps
    assert "benchmark_analysis" not in revision.affected_steps
    revision_run = await session.get(AgentRun, revision.revision_run_id)
    assert revision_run is not None
    constraint = revision_run.request_payload["structured_input"]["constraints"][0]
    assert constraint == {
        "constraint_type": "OFFER_TERMS",
        "raw_requirement": PRICE_STEERING,
        "target_scope": {
            "kind": "content_item_indexes",
            "item_indexes": [1],
        },
    }
    assert (
        await session.scalar(
            select(func.count(RunRevision.id)).where(RunRevision.source_run_id == source_run.id)
        )
        == 1
    )


@pytest.mark.asyncio
async def test_manual_publish_pending_work_is_public_isolated_and_idempotent(
    client,
    session,
    admin,
    member,
) -> None:
    """Manual publication creates one source-linked data follow-up without auto-publish."""

    seeded = await _seed_artifact(
        session,
        admin,
        account_name="weekly-manual-publish",
        status=DeliverableStatus.APPROVED,
        payload={
            "period": "2026-08-10 至 2026-08-16",
            "items": [
                {"date": f"2026-08-{day:02d}", "title": f"第 {day - 9} 条"} for day in range(10, 17)
            ],
            "operating_notes": ["仅生成手动发布清单，不自动调用平台发布。"],
        },
        skill_code="content_calendar_planning",
        deliverable_type=DeliverableType.PUBLISH_CALENDAR,
    )
    account = seeded[1]
    artifact = seeded[8]
    created_ids: list[int] = []
    for offset in range(7):
        action = await client.post(
            f"/artifacts/{artifact.id}/actions/add_to_schedule",
            headers={
                **_headers(admin),
                "Idempotency-Key": f"weekly-schedule-{offset}",
            },
            json={
                "confirmed": True,
                "scheduled_at": (
                    datetime(2026, 8, 10, 9, tzinfo=UTC) + timedelta(days=offset)
                ).isoformat(),
                "timezone": "Asia/Shanghai",
            },
        )
        assert action.status_code == 200
        created_ids.append(action.json()["resource"]["id"])

    replay = await client.post(
        f"/artifacts/{artifact.id}/actions/add_to_schedule",
        headers={**_headers(admin), "Idempotency-Key": "weekly-schedule-0"},
        json={
            "confirmed": True,
            "scheduled_at": datetime(2026, 8, 10, 9, tzinfo=UTC).isoformat(),
            "timezone": "Asia/Shanghai",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True

    pending = await client.get(
        f"/accounts/{account.id}/pending-work",
        headers=_headers(admin),
    )
    assert pending.status_code == 200
    publish_group = next(
        group for group in pending.json()["groups"] if group["kind"] == "manual_publish"
    )
    initial_data_group = next(
        group for group in pending.json()["groups"] if group["kind"] == "account_data"
    )
    initial_data_ids = {item["id"] for item in initial_data_group["items"]}
    assert publish_group["count"] == 7
    assert (
        await session.scalar(
            select(func.count(AgentToolCall.id)).where(
                AgentToolCall.tool_code == "platform.content_publish"
            )
        )
        == 0
    )

    hidden = await client.get(
        f"/accounts/{account.id}/pending-work",
        headers=_headers(member),
    )
    assert hidden.status_code == 404

    published_entry_id = created_ids[0]
    first = await client.post(
        f"/accounts/{account.id}/pending-work/schedule-entries/{published_entry_id}/publish",
        headers=_headers(admin),
    )
    repeated = await client.post(
        f"/accounts/{account.id}/pending-work/schedule-entries/{published_entry_id}/publish",
        headers=_headers(admin),
    )
    assert first.status_code == 200
    assert repeated.json() == first.json()

    final_pending = await client.get(
        f"/accounts/{account.id}/pending-work",
        headers=_headers(admin),
    )
    groups = {group["kind"]: group for group in final_pending.json()["groups"]}
    assert groups["manual_publish"]["count"] == 6
    assert f"schedule_entry:{published_entry_id}" not in {
        item["id"] for item in groups["manual_publish"]["items"]
    }
    follow_ups = [
        item for item in groups["account_data"]["items"] if item["id"] not in initial_data_ids
    ]
    assert len(follow_ups) == 1
    assert follow_ups == [
        {
            "id": f"account_data:publication:{published_entry_id}",
            "kind": "account_data",
            "action_label": "补录发布后数据",
            "account_id": account.id,
            "thread_id": artifact.thread_id,
            "turn_id": artifact.turn_id,
            "due_at": follow_ups[0]["due_at"],
            "reason": "记录已发布作品的后续表现数据。",
            "next_step_after_completion": "数据确认后，运营大脑会复盘本次发布效果。",
            "target": {"type": "account_data"},
        }
    ]
    assert follow_ups[0]["due_at"] is not None
    assert (
        await session.scalar(
            select(func.count(ContentScheduleEntry.id)).where(
                ContentScheduleEntry.account_id == account.id,
                ContentScheduleEntry.status == "published",
            )
        )
        == 1
    )
