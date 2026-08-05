"""Release contract for the complete account-scoped operations worker loop.

The first two tests are intentionally end-to-end RED contracts for the two
remaining production gaps. They use the public submission boundary and only
replace the model provider. The manual-publication test locks the durable
lifecycle that is already available today.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.core.security import create_access_token
from app.llm.adapters import CompletionResult
from app.llm.gateway import LLMGateway
from app.models import (
    Account,
    AgentRun,
    AgentToolCall,
    BrainTask,
    ContentScheduleEntry,
    ConversationThread,
    ConversationTurn,
    Deliverable,
    Event,
    RunRevision,
    SkillRun,
    TurnInterrupt,
)
from app.models.enums import BrainTaskStatus, BrainTaskType, DeliverableStatus, DeliverableType
from app.orchestrator.skill_runtime import SkillRuntime
from app.services.composite_skill_runs import lock_composite_finish_approval
from app.services.skill_approvals import SkillApprovalConflict, finalize_skill_finish_approval
from app.services.turn_execution import execute_revision_task_run
from app.worker import _execute_v2_conversation_run
from tests.test_artifacts_api import _seed_artifact
from tests.test_operating_skills import (
    _AcceptingCritic,
    _capability_request,
    _Harness,
    _Tools,
)

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
@pytest.mark.parametrize("approval_revision_mode", ["partial", "full_recompute"])
async def test_price_steering_reuses_topics_and_reruns_only_downstream_artifacts(
    client,
    session,
    admin,
    monkeypatch,
    approval_revision_mode,
) -> None:
    """A completed weekly package revises scripts forward without another data read."""

    account_data, thread_data = await _account_and_thread(
        client,
        admin,
        nickname="weekly-steering-execution-contract",
    )
    account = await session.get(Account, account_data["id"])
    thread = await session.get(ConversationThread, thread_data["id"])
    assert account is not None and thread is not None
    account_id = account.id
    source_turn = ConversationTurn(
        thread_id=thread.id,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id="weekly-steering-execution-source",
        user_input=WEEKLY_REQUEST,
        status="running",
    )
    session.add(source_turn)
    await session.flush()
    source_run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        thread_id=thread.id,
        turn_id=source_turn.id,
        client_message_id="weekly-steering-execution-source-run",
        status="claimed",
        phase="request",
        request_payload={"message": WEEKLY_REQUEST},
    )
    session.add(source_run)
    await session.commit()

    tools = _Tools()
    runtime = SkillRuntime(
        tool_executor=tools,
        harness=_Harness(),
        critic=_AcceptingCritic(),
    )
    source_result = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=source_turn,
        run=source_run,
        skill_code="operation_iteration",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=source_turn,
            run=source_run,
            skill_code="operation_iteration",
            structured_input={"cycle_days": 7, "topic_count": 5},
        ),
    )
    assert source_result.status == "waiting_permission"
    source_root = await session.scalar(
        select(SkillRun).where(
            SkillRun.run_id == source_run.id,
            SkillRun.skill_code == "operation_iteration",
        )
    )
    assert source_root is not None
    source_nodes = {
        item["skill_code"]: item
        for item in source_root.output_snapshot["report"]["child_skill_graph"]
    }
    source_artifacts = {
        code: await session.get(Deliverable, node["artifact_id"])
        for code, node in source_nodes.items()
    }
    assert all(item is not None for item in source_artifacts.values())
    source_payloads = {
        code: deepcopy(item.payload)
        for code, item in source_artifacts.items()
        if item is not None
    }
    source_versions = {
        code: item.version
        for code, item in source_artifacts.items()
        if item is not None
    }
    source_publishing_run = await session.scalar(
        select(SkillRun).where(
            SkillRun.run_id == source_run.id,
            SkillRun.skill_code == "publishing_preparation",
        )
    )
    assert source_publishing_run is not None
    final_call = await session.scalar(
        select(AgentToolCall).where(
            AgentToolCall.skill_run_id == source_publishing_run.id,
            AgentToolCall.status == "waiting_approval",
        )
    )
    source_task = await session.get(BrainTask, source_result.task_id)
    assert final_call is not None and source_task is not None
    approval_lock = await lock_composite_finish_approval(
        session,
        tool_call=final_call,
    )
    approval_lock.tool_call.status = "success"
    await finalize_skill_finish_approval(
        session,
        tool_call=approval_lock.tool_call,
        task=source_task,
        approved=True,
        comment="确认源周运营包",
        prelocked=approval_lock.runtime_lock,
    )
    await session.commit()
    source_package_artifact = source_artifacts["publishing_preparation"]
    assert source_package_artifact is not None
    assert source_package_artifact.type == DeliverableType.PUBLISH_PACKAGE
    source_schedule_rows = list(
        await session.scalars(
            select(ContentScheduleEntry)
            .where(
                ContentScheduleEntry.source_artifact_id == source_package_artifact.id,
                ContentScheduleEntry.source_artifact_version
                == source_package_artifact.version,
            )
            .order_by(ContentScheduleEntry.id)
        )
    )
    assert len(source_schedule_rows) == 5
    assert {row.status for row in source_schedule_rows} == {"planned"}

    steered = await client.post(
        f"/brain/conversations/{thread.id}/turns",
        headers=_headers(admin),
        json={
            "client_message_id": "weekly-price-steering-execution",
            "message": PRICE_STEERING,
            "target_turn_id": source_turn.id,
        },
    )
    assert steered.status_code == 202
    revision = await session.scalar(
        select(RunRevision).where(RunRevision.source_run_id == source_run.id)
    )
    assert revision is not None and revision.mode == "partial"
    revision_run = await session.get(AgentRun, revision.revision_run_id)
    assert revision_run is not None
    monkeypatch.setattr("app.services.turn_execution.skill_runtime", runtime)

    revision_status = await execute_revision_task_run(
        session,
        run=revision_run,
        task=source_task,
        worker_id="weekly-steering-execution-contract",
    )

    assert revision_status == "waiting_permission"
    revision_root = await session.get(SkillRun, revision.revision_skill_run_id)
    assert revision_root is not None
    revision_nodes = {
        item["skill_code"]: item
        for item in revision_root.output_snapshot["report"]["child_skill_graph"]
    }
    assert revision_nodes["topic_planning"]["artifact_id"] == source_nodes[
        "topic_planning"
    ]["artifact_id"]
    topic_artifact = await session.get(
        Deliverable,
        revision_nodes["topic_planning"]["artifact_id"],
    )
    assert topic_artifact is not None
    assert [item["topic_id"] for item in topic_artifact.payload["topics"]] == [
        f"topic-{index:02d}" for index in range(1, 6)
    ]
    for code in (
        "script_generation",
        "visual_brief_generation",
        "content_calendar_planning",
        "publishing_preparation",
    ):
        revised_artifact = await session.get(
            Deliverable,
            revision_nodes[code]["artifact_id"],
        )
        assert revised_artifact is not None
        assert revised_artifact.version == source_versions[code] + 1, code
        source_artifact = source_artifacts[code]
        assert source_artifact is not None
        await session.refresh(source_artifact)
        assert source_artifact.payload == source_payloads[code]
    revised_script = await session.get(
        Deliverable,
        revision_nodes["script_generation"]["artifact_id"],
    )
    assert revised_script is not None
    assert PRICE_STEERING in revised_script.payload["scripts"][0]["constraints_hit"]
    assert sum(call.tool_code == "account.data_context" for call in tools.calls) == 1

    # Revision execution alone must not disturb the already-approved source schedule.
    for source_row in source_schedule_rows:
        await session.refresh(source_row)
    assert {row.status for row in source_schedule_rows} == {"planned"}
    revised_package = await session.get(
        Deliverable,
        revision_nodes["publishing_preparation"]["artifact_id"],
    )
    revised_publishing_run = await session.scalar(
        select(SkillRun).where(
            SkillRun.run_id == revision_run.id,
            SkillRun.skill_code == "publishing_preparation",
        )
    )
    assert revised_package is not None
    assert revised_package.type == DeliverableType.PUBLISH_PACKAGE
    assert revised_publishing_run is not None
    revised_final_call = await session.scalar(
        select(AgentToolCall).where(
            AgentToolCall.skill_run_id == revised_publishing_run.id,
            AgentToolCall.status == "waiting_approval",
        )
    )
    assert revised_final_call is not None
    revision.mode = approval_revision_mode
    await session.commit()
    source_package_artifact_id = source_package_artifact.id
    source_package_artifact_version = source_package_artifact.version
    published_source_row_id = source_schedule_rows[0].id
    revised_package_id = revised_package.id
    revised_package_version = revised_package.version
    revised_final_call_id = revised_final_call.id

    # A source slot that has already executed makes replacement unsafe.
    source_schedule_rows[0].status = "published"
    source_schedule_rows[0].published_at = datetime.now(UTC)
    await session.commit()
    conflict_lock = await lock_composite_finish_approval(
        session,
        tool_call=revised_final_call,
    )
    conflict_lock.tool_call.status = "success"
    with pytest.raises(
        SkillApprovalConflict,
        match="SKILL_APPROVAL_REVISION_SCHEDULE_PUBLISHED",
    ):
        await finalize_skill_finish_approval(
            session,
            tool_call=conflict_lock.tool_call,
            task=source_task,
            approved=True,
            comment="确认修订周运营包",
            prelocked=conflict_lock.runtime_lock,
        )
    await session.rollback()
    published_source_row = await session.get(ContentScheduleEntry, published_source_row_id)
    assert published_source_row is not None
    assert published_source_row.status == "published"
    assert published_source_row.published_at is not None
    assert (
        await session.scalar(
            select(func.count(ContentScheduleEntry.id)).where(
                ContentScheduleEntry.source_artifact_id == revised_package_id,
                ContentScheduleEntry.source_artifact_version == revised_package_version,
            )
        )
        == 0
    )

    # Restore the fixture to the safe pre-publication state, then approve atomically.
    published_source_row.status = "planned"
    published_source_row.published_at = None
    await session.commit()
    revised_final_call = await session.get(AgentToolCall, revised_final_call_id)
    assert revised_final_call is not None
    approval_lock = await lock_composite_finish_approval(
        session,
        tool_call=revised_final_call,
    )
    approval_lock.tool_call.status = "success"
    await finalize_skill_finish_approval(
        session,
        tool_call=approval_lock.tool_call,
        task=source_task,
        approved=True,
        comment="确认修订周运营包",
        prelocked=approval_lock.runtime_lock,
    )
    await session.commit()

    source_schedule_rows = list(
        await session.scalars(
            select(ContentScheduleEntry).where(
                ContentScheduleEntry.source_artifact_id == source_package_artifact_id,
                ContentScheduleEntry.source_artifact_version
                == source_package_artifact_version,
            )
        )
    )
    revised_schedule_rows = list(
        await session.scalars(
            select(ContentScheduleEntry).where(
                ContentScheduleEntry.source_artifact_id == revised_package_id,
                ContentScheduleEntry.source_artifact_version == revised_package_version,
            )
        )
    )
    assert len(source_schedule_rows) == len(revised_schedule_rows) == 5
    assert {row.status for row in source_schedule_rows} == {"superseded"}
    assert {row.status for row in revised_schedule_rows} == {"planned"}
    assert all(row.published_at is None for row in source_schedule_rows)
    assert (
        await session.scalar(
            select(func.count(ContentScheduleEntry.id)).where(
                ContentScheduleEntry.account_id == account_id,
                ContentScheduleEntry.status == "planned",
            )
        )
        == 5
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("approval_revision_mode", "approval_conflict"),
    [
        ("partial", None),
        ("full_recompute", None),
        (
            "manual_reconciliation",
            "SKILL_APPROVAL_REVISION_SCOPE_CONFLICT",
        ),
    ],
)
async def test_unapproved_weekly_package_is_superseded_before_revision_runs(
    client,
    session,
    admin,
    monkeypatch,
    approval_revision_mode,
    approval_conflict,
) -> None:
    """Steering replaces the sole pending gate instead of waiting behind it."""

    account_data, thread_data = await _account_and_thread(
        client,
        admin,
        nickname="weekly-unapproved-steering-contract",
    )
    account = await session.get(Account, account_data["id"])
    thread = await session.get(ConversationThread, thread_data["id"])
    assert account is not None and thread is not None
    source_turn = ConversationTurn(
        thread_id=thread.id,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id="weekly-unapproved-steering-source",
        user_input=WEEKLY_REQUEST,
        status="running",
    )
    session.add(source_turn)
    await session.flush()
    source_run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        thread_id=thread.id,
        turn_id=source_turn.id,
        client_message_id="weekly-unapproved-steering-source-run",
        status="claimed",
        phase="request",
        request_payload={"message": WEEKLY_REQUEST},
    )
    session.add(source_run)
    await session.commit()
    tools = _Tools()
    runtime = SkillRuntime(
        tool_executor=tools,
        harness=_Harness(),
        critic=_AcceptingCritic(),
    )
    source_result = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=source_turn,
        run=source_run,
        skill_code="operation_iteration",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=source_turn,
            run=source_run,
            skill_code="operation_iteration",
            structured_input={"cycle_days": 7, "topic_count": 5},
        ),
    )
    assert source_result.status == "waiting_permission"
    source_task = await session.get(BrainTask, source_result.task_id)
    source_root = await session.scalar(
        select(SkillRun).where(
            SkillRun.run_id == source_run.id,
            SkillRun.skill_code == "operation_iteration",
        )
    )
    assert source_task is not None and source_root is not None
    source_nodes = {
        item["skill_code"]: item
        for item in source_root.output_snapshot["report"]["child_skill_graph"]
    }
    source_package = await session.get(
        Deliverable,
        source_nodes["publishing_preparation"]["artifact_id"],
    )
    source_final_call = await session.scalar(
        select(AgentToolCall).where(
            AgentToolCall.skill_run_id == source_package.skill_run_id,
            AgentToolCall.status == "waiting_approval",
        )
    ) if source_package is not None else None
    assert source_package is not None
    assert source_package.type == DeliverableType.PUBLISH_PACKAGE
    assert source_package.status == DeliverableStatus.PENDING_REVIEW
    assert source_final_call is not None
    old_interrupt = TurnInterrupt(
        org_id=admin.org_id,
        account_id=account.id,
        thread_id=thread.id,
        turn_id=source_turn.id,
        run_id=source_run.id,
        skill_run_id=source_package.skill_run_id,
        kind="approval",
        status="pending",
        public_message="请确认原周运营发布包。",
        action_label="确认并创建手动发布任务",
        response_schema={"type": "object"},
        source_type="tool_call",
        source_id=source_final_call.id,
        source_version=1,
        semantic_key="weekly-source-final-approval",
        version=1,
    )
    session.add(old_interrupt)
    await session.commit()
    assert (
        await session.scalar(
            select(func.count(ContentScheduleEntry.id)).where(
                ContentScheduleEntry.source_artifact_id == source_package.id
            )
        )
        == 0
    )

    auth_headers = _headers(admin)
    thread_id = thread.id
    source_turn_id = source_turn.id
    source_run_id = source_run.id
    source_task_id = source_task.id
    source_root_id = source_root.id
    source_package_id = source_package.id
    source_final_call_id = source_final_call.id
    old_interrupt_id = old_interrupt.id
    source_output = deepcopy(source_root.output_snapshot)
    source_root.output_snapshot = {
        **source_output,
        "report": {
            **source_output["report"],
            "child_skill_graph": [
                node
                for node in source_output["report"]["child_skill_graph"]
                if node["skill_code"] != "publishing_preparation"
            ],
        },
    }
    await session.commit()
    with pytest.raises(
        RuntimeError,
        match="REVISION_SOURCE_PENDING_STATE_CONFLICT",
    ):
        await client.post(
            f"/brain/conversations/{thread_id}/turns",
            headers=auth_headers,
            json={
                "client_message_id": "weekly-unapproved-malformed-steering",
                "message": PRICE_STEERING,
                "target_turn_id": source_turn_id,
            },
        )
    await session.rollback()
    assert (
        await session.scalar(
            select(func.count(RunRevision.id)).where(
                RunRevision.source_run_id == source_run_id
            )
        )
        == 0
    )
    assert (
        await session.scalar(
            select(func.count(AgentToolCall.id)).where(
                AgentToolCall.thread_id == thread_id,
                AgentToolCall.status == "waiting_approval",
            )
        )
        == 1
    )
    thread = await session.get(ConversationThread, thread_id)
    source_turn = await session.get(ConversationTurn, source_turn_id)
    source_run = await session.get(AgentRun, source_run_id)
    source_task = await session.get(BrainTask, source_task_id)
    source_root = await session.get(SkillRun, source_root_id)
    source_package = await session.get(Deliverable, source_package_id)
    source_final_call = await session.get(AgentToolCall, source_final_call_id)
    old_interrupt = await session.get(TurnInterrupt, old_interrupt_id)
    assert all(
        item is not None
        for item in (
            thread,
            source_turn,
            source_run,
            source_task,
            source_root,
            source_package,
            source_final_call,
            old_interrupt,
        )
    )
    source_root.output_snapshot = source_output
    await session.commit()

    steered = await client.post(
        f"/brain/conversations/{thread_id}/turns",
        headers=auth_headers,
        json={
            "client_message_id": "weekly-unapproved-price-steering",
            "message": PRICE_STEERING,
            "target_turn_id": source_turn.id,
        },
    )
    assert steered.status_code == 202
    revision = await session.scalar(
        select(RunRevision).where(RunRevision.source_run_id == source_run.id)
    )
    assert revision is not None
    revision_run = await session.get(AgentRun, revision.revision_run_id)
    await session.refresh(source_run)
    await session.refresh(source_root)
    await session.refresh(source_package)
    await session.refresh(source_final_call)
    await session.refresh(old_interrupt)
    assert revision_run is not None
    assert revision_run.status == "queued"
    assert revision.status == "planned"
    assert source_run.status == "stopped"
    assert source_root.status == "stopped"
    assert source_package.status == DeliverableStatus.SUPERSEDED
    assert source_final_call.status == "failed"
    assert source_final_call.error == "SUPERSEDED_BY_REVISION"
    assert old_interrupt.status == "superseded"
    assert old_interrupt.version == 2
    supersede_event = await session.scalar(
        select(Event).where(
            Event.turn_id == source_turn.id,
            Event.type == "deliverable.updated",
            Event.payload["deliverable_id"].as_integer() == source_package.id,
        ).order_by(Event.id.desc())
    )
    terminal_event = await session.scalar(
        select(Event).where(
            Event.turn_id == source_turn.id,
            Event.type == "turn.stopped",
        )
    )
    interrupt_event = await session.scalar(
        select(Event).where(
            Event.turn_id == source_turn.id,
            Event.type == "turn.interrupt_cancelled",
            Event.payload["interrupt_id"].as_integer() == old_interrupt.id,
        )
    )
    assert supersede_event is not None
    assert supersede_event.payload["status"] == "superseded"
    assert supersede_event.payload["metadata"] == {
        "kind": "approval_superseded",
        "source_id": source_final_call.id,
        "status": "superseded",
    }
    assert terminal_event is not None
    assert terminal_event.payload["error_code"] == "SUPERSEDED_BY_REVISION"
    assert interrupt_event is not None
    assert interrupt_event.payload["status"] == "superseded"
    assert interrupt_event.payload["version"] == 2

    monkeypatch.setattr("app.services.turn_execution.skill_runtime", runtime)
    revision_status = await execute_revision_task_run(
        session,
        run=revision_run,
        task=source_task,
        worker_id="weekly-unapproved-steering-contract",
    )
    assert revision_status == "waiting_permission"
    revision_root = await session.get(SkillRun, revision.revision_skill_run_id)
    assert revision_root is not None
    revision_nodes = {
        item["skill_code"]: item
        for item in revision_root.output_snapshot["report"]["child_skill_graph"]
    }
    revised_package = await session.get(
        Deliverable,
        revision_nodes["publishing_preparation"]["artifact_id"],
    )
    revised_publishing_run = await session.scalar(
        select(SkillRun).where(
            SkillRun.run_id == revision_run.id,
            SkillRun.skill_code == "publishing_preparation",
        )
    )
    assert revised_package is not None and revised_publishing_run is not None
    revised_final_call = await session.scalar(
        select(AgentToolCall).where(
            AgentToolCall.skill_run_id == revised_publishing_run.id,
            AgentToolCall.status == "waiting_approval",
        )
    )
    assert revised_final_call is not None
    revised_package_id = revised_package.id
    revision.mode = approval_revision_mode
    if approval_revision_mode == "manual_reconciliation":
        revision.manual_reconciliation_reason = "external_write_ambiguous"
        revision.fork_checkpoint_id = None
    await session.commit()
    assert (
        await session.scalar(
            select(func.count(AgentToolCall.id)).where(
                AgentToolCall.turn_id.in_([source_turn.id, revision_run.turn_id]),
                AgentToolCall.status == "waiting_approval",
            )
        )
        == 1
    )
    assert (
        await session.scalar(
            select(func.count(TurnInterrupt.id)).where(
                TurnInterrupt.run_id.in_([source_run.id, revision_run.id]),
                TurnInterrupt.status == "pending",
            )
        )
        == 0
    )

    approval_lock = await lock_composite_finish_approval(
        session,
        tool_call=revised_final_call,
    )
    approval_lock.tool_call.status = "success"
    if approval_conflict is not None:
        with pytest.raises(SkillApprovalConflict, match=approval_conflict):
            await finalize_skill_finish_approval(
                session,
                tool_call=approval_lock.tool_call,
                task=source_task,
                approved=True,
                comment="确认修订周运营包",
                prelocked=approval_lock.runtime_lock,
            )
        await session.rollback()
        assert (
            await session.scalar(
                select(func.count(ContentScheduleEntry.id)).where(
                    ContentScheduleEntry.source_artifact_id == revised_package_id,
                )
            )
            == 0
        )
        return
    await finalize_skill_finish_approval(
        session,
        tool_call=approval_lock.tool_call,
        task=source_task,
        approved=True,
        comment="确认修订周运营包",
        prelocked=approval_lock.runtime_lock,
    )
    await session.commit()
    assert (
        await session.scalar(
            select(func.count(ContentScheduleEntry.id)).where(
                ContentScheduleEntry.source_artifact_id == revised_package.id,
                ContentScheduleEntry.status == "planned",
            )
        )
        == 5
    )
    assert sum(call.tool_code == "account.data_context" for call in tools.calls) == 1


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
