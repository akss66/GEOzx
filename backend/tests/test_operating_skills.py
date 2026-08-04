"""Execution contracts for the first account-operations Skill loop."""

import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta
from time import monotonic
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import (
    Account,
    AgentInvocation,
    AgentRun,
    AgentToolCall,
    BrainTask,
    ContentItem,
    ContentScheduleEntry,
    ConversationThread,
    ConversationTurn,
    Deliverable,
    Event,
    SkillRun,
)
from app.models.enums import (
    AccountStatus,
    AgentCode,
    AgentInvocationStatus,
    BrainTaskStatus,
    ContentStage,
    ContentStatus,
    DeliverableStatus,
    DeliverableType,
    Platform,
    UserRole,
)
from app.orchestrator.runtime_scope import RuntimeScope
from app.orchestrator.skill_runtime import (
    SkillRecoveryConflict,
    SkillRuntime,
    run_bounded_stage,
)
from app.orchestrator.tool_executor import DurableToolExecutor
from app.schemas.brain import RuntimeToolCall
from app.schemas.capability_request import CapabilityRequest
from app.services.agent_runs import acquire_agent_run
from app.services.artifacts import accept_artifact
from app.services.runtime_deliverables import write_runtime_deliverable
from app.services.skill_approvals import (
    SkillApprovalConflict,
    finalize_skill_finish_approval,
)
from app.tools import ToolAdapter, ToolExecutionContext, ToolSpec


async def _scope(session, admin, *, key: str, message: str):
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname=f"account-{key}",
        status=AccountStatus.ACTIVE,
        auth={"auth_status": "authorized", "data_sync_status": "ready"},
    )
    session.add(account)
    await session.flush()
    thread = ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title=key,
    )
    session.add(thread)
    await session.flush()
    turn = ConversationTurn(
        thread_id=thread.id,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id=key,
        user_input=message,
    )
    session.add(turn)
    await session.flush()
    run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        thread_id=thread.id,
        turn_id=turn.id,
        client_message_id=key,
        status="claimed",
        request_payload={"message": message},
    )
    session.add(run)
    await session.commit()
    return account, thread, turn, run


def _capability_request(
    *,
    admin,
    account,
    thread,
    turn,
    run,
    skill_code: str,
    structured_input: dict,
) -> CapabilityRequest:
    return CapabilityRequest(
        org_id=admin.org_id,
        user_id=admin.id,
        account_id=account.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        message=turn.user_input,
        requested_skill_code=skill_code,
        execution_preference="FORMAL_TASK",
        structured_input=structured_input,
    )


class _Tools:
    def __init__(self) -> None:
        self.calls: list[RuntimeToolCall] = []
        class EmptyParams(BaseModel):
            model_config = ConfigDict(extra="forbid")

        class DaysParams(BaseModel):
            model_config = ConfigDict(extra="forbid")

            days: int = 30

        class PublishParams(BaseModel):
            model_config = ConfigDict(extra="forbid")

            content_item_id: int
            title: str

        async def profile(_params: EmptyParams, context: ToolExecutionContext):
            return {
                "account_id": context.account_id,
                "nickname": "测试账号",
                "platform": "douyin",
            }

        async def data_context(
            params: DaysParams,
            context: ToolExecutionContext,
        ):
            return {
                "account_id": context.account_id,
                "period": {"days": params.days},
                "coverage": {"content_metrics": "available"},
                "metrics": {"play": {"value": 1200}},
                "benchmark_count": 1,
                "sources": [
                    {"batch_id": 7, "data_domains": ["account_metrics"]},
                    {"batch_id": 8, "data_domains": ["benchmarks"]},
                ],
            }

        async def prepare_publish_package(
            params: PublishParams,
            context: ToolExecutionContext,
        ):
            return {
                "account_id": context.account_id,
                "content_item_id": params.content_item_id,
                "status": "prepared",
                "publish_package": {"title": params.title},
            }

        self.executor = DurableToolExecutor(
            ToolAdapter(
                [
                    ToolSpec(
                        name="account.profile",
                        handler=profile,
                        params_model=EmptyParams,
                        side_effect_level="read",
                        allowed_roles=frozenset({UserRole.ADMIN, UserRole.USER}),
                    ),
                    ToolSpec(
                        name="account.data_context",
                        handler=data_context,
                        params_model=DaysParams,
                        side_effect_level="read",
                        allowed_roles=frozenset({UserRole.ADMIN, UserRole.USER}),
                    ),
                    ToolSpec(
                        name="publish_package_prepare",
                        handler=prepare_publish_package,
                        params_model=PublishParams,
                        side_effect_level="idempotent_write",
                        allowed_roles=frozenset({UserRole.ADMIN, UserRole.USER}),
                        execution_phase="prepare",
                    ),
                ]
            ),
            _allow_test_account_lane_fallback=True,
        )

    async def execute(self, **kwargs):
        self.calls.append(kwargs["request"])
        return await self.executor.execute(**kwargs)


class _Harness:
    def __init__(self) -> None:
        self.calls: list[AgentCode] = []
        self.upstreams: list[dict] = []

    async def execute(self, *args, **kwargs):
        session = args[0]
        code = kwargs["code"]
        self.calls.append(code)
        self.upstreams.append(dict(kwargs["upstream"]))
        upstream = dict(kwargs["upstream"])
        structured_input = dict(upstream.get("structured_input") or {})
        source_artifacts = list(upstream.get("source_artifacts") or [])
        source_types = {str(item.get("artifact_type")) for item in source_artifacts}
        if code is AgentCode.CONTENT_DIRECTOR and "topic_plan" in source_types:
            topic_payload = next(
                dict(item.get("payload") or {})
                for item in source_artifacts
                if item.get("artifact_type") == "topic_plan"
            )
            constraint_hits: dict[int, list[str]] = {}
            for raw_constraint in structured_input.get("_server_request_constraints") or []:
                constraint = json.loads(raw_constraint)
                requirement = str(constraint.get("raw_requirement") or "")
                for target_index in dict(
                    constraint.get("target_scope") or {}
                ).get("item_indexes") or []:
                    constraint_hits.setdefault(int(target_index), []).append(requirement)
            output = {
                "scripts": [
                    {
                        "script_id": f"script-{index:02d}",
                        "topic_id": topic["topic_id"],
                        "title": topic["title"],
                        "hook": f"第 {index} 个问题，先看结论",
                        "voiceover": f"围绕{topic['title']}给出第 {index} 套完整实测说明。",
                        "shot_list": ["问题开场", "过程实测", "结论总结"],
                        "duration_seconds": structured_input.get("duration_seconds", 60),
                        "cta": f"评论区回复 {index}",
                        "constraints_hit": constraint_hits.get(index, []),
                    }
                    for index, topic in enumerate(topic_payload["topics"], start=1)
                ]
            }
        elif code is AgentCode.CONTENT_DIRECTOR and "topic_count" in structured_input:
            output = {
                "theme": "下周实测内容",
                "topics": [
                    {
                        "topic_id": f"topic-{index:02d}",
                        "title": f"第 {index} 个实测选题",
                        "angle": f"从场景 {index} 验证真实问题",
                        "format": "short_video",
                    }
                    for index in range(1, int(structured_input["topic_count"]) + 1)
                ],
                "posting_notes": ["按计划拍摄并记录真实反馈。"],
            }
        elif source_types == {"video_script"} and isinstance(
            source_artifacts[0].get("payload", {}).get("scripts"),
            list,
        ):
            script_payload = dict(source_artifacts[0].get("payload") or {})
            output = {
                "visuals": [
                    {
                        "visual_id": f"visual-{index:02d}",
                        "script_id": script["script_id"],
                        "topic_id": script["topic_id"],
                        "cover_copy": f"第 {index} 条实测",
                        "composition": "产品主体与实测数据左右对比",
                        "shot_list": ["问题开场", "过程实测", "结果总结"],
                        "asset_checklist": ["产品素材", "实测过程"],
                        "platform_constraints": ["竖屏 9:16", "字幕安全区"],
                    }
                    for index, script in enumerate(script_payload["scripts"], start=1)
                ]
            }
        else:
            output = (
            {
                "title": "玻璃贴膜避坑指南",
                "hook": "贴膜前先看这三个坑。",
                "scenes": ["常见误区", "真实案例", "选择建议"],
                "voiceover": "先讲常见误区，再看真实案例，最后给出选择建议。",
                "shot_list": ["常见误区", "真实案例", "选择建议"],
                "duration_seconds": 60,
                "cta": "评论区说说你的选择。",
                "bgm_suggestion": "轻节奏",
            }
            if code is AgentCode.CONTENT_DIRECTOR
            else {
                "period": "最近30天",
                "summary": "内容已有播放基础，但互动承接不足。",
                "key_metrics": {"play": 1200},
                "highlights": ["案例内容表现较好"],
                "issues": ["评论互动不足"],
                "optimization_suggestions": ["强化结尾提问和私信引导"],
            }
            )
        invocation = AgentInvocation(
            task_id=kwargs["task"].id,
            run_id=kwargs["run_id"],
            skill_run_id=kwargs["skill_run_id"],
            thread_id=kwargs["thread_id"],
            turn_id=kwargs["turn_id"],
            step_key=kwargs["step_key"],
            attempt=kwargs["attempt"],
            agent_code=code,
            agent_name=code.value,
            status=AgentInvocationStatus.DONE,
            output_summary=f"{code.value} completed",
            upstream=[{"trace_only_output": output}],
        )
        session.add(invocation)
        await session.commit()
        await session.refresh(invocation)
        return SimpleNamespace(
            invocation=invocation,
            deliverable=None,
            output=output,
        )


class _AcceptingCritic:
    async def review(self, **_kwargs):
        return SimpleNamespace(passed=True, score=95, issues=[], suggestions=[])


class _MalformedWeeklyHarness(_Harness):
    async def execute(self, *args, **kwargs):
        result = await super().execute(*args, **kwargs)
        source_types = {
            str(item.get("artifact_type"))
            for item in kwargs["upstream"].get("source_artifacts") or []
        }
        if kwargs["code"] is AgentCode.CONTENT_DIRECTOR and "topic_plan" in source_types:
            result.output.clear()
            result.output["scripts"] = [
                {
                    "script_id": f"script-{index:02d}",
                    "topic_id": f"topic-{index:02d}",
                    "duration_seconds": 60,
                }
                for index in range(1, 6)
            ]
        return result


@pytest.mark.asyncio
async def test_weekly_operation_keeps_malformed_scripts_pending_for_review(
    session,
    admin,
) -> None:
    account, thread, turn, run = await _scope(
        session,
        admin,
        key="weekly-malformed-scripts",
        message="结合最近数据和对标内容，规划并制作下周抖音内容",
    )
    result = await SkillRuntime(
        tool_executor=_Tools(),
        harness=_MalformedWeeklyHarness(),
        critic=_AcceptingCritic(),
    ).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="operation_iteration",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="operation_iteration",
            structured_input={"cycle_days": 7, "topic_count": 5},
        ),
    )

    nodes = {item["skill_code"]: item for item in result.report["child_skill_graph"]}
    assert result.status == "waiting_user"
    assert nodes["topic_planning"]["status"] == "completed"
    assert nodes["script_generation"]["status"] == "needs_review"
    assert all(
        nodes[code]["status"] == "pending"
        for code in (
            "visual_brief_generation",
            "content_calendar_planning",
            "publishing_preparation",
        )
    )
    script_artifact = await session.get(
        Deliverable,
        nodes["script_generation"]["artifact_id"],
    )
    assert script_artifact is not None
    assert script_artifact.status is DeliverableStatus.PENDING_REVIEW
    quality = script_artifact.payload["quality"]
    assert quality["status"] == "needs_review"
    required = next(item for item in quality["checks"] if item["code"] == "required_fields")
    assert required["passed"] is False
    assert (
        await session.scalar(
            select(func.count(AgentToolCall.id)).where(
                AgentToolCall.status == "waiting_approval"
            )
        )
        == 0
    )


@pytest.mark.asyncio
async def test_weekly_operation_builds_one_typed_package_without_intermediate_approval(
    session,
    admin,
) -> None:
    account, thread, turn, run = await _scope(
        session,
        admin,
        key="weekly-typed-package",
        message="结合最近数据和对标内容，规划并制作下周抖音内容",
    )
    tools = _Tools()
    runtime = SkillRuntime(
        tool_executor=tools,
        harness=_Harness(),
        critic=_AcceptingCritic(),
    )

    result = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="operation_iteration",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="operation_iteration",
            structured_input={"cycle_days": 7, "topic_count": 5},
        ),
    )

    assert result.status == "waiting_permission"
    nodes = {item["skill_code"]: item for item in result.report["child_skill_graph"]}
    assert [nodes[code]["status"] for code in nodes] == [
        "completed",
        "completed",
        "completed",
        "completed",
        "waiting_permission",
    ]
    artifacts = {
        code: await session.get(Deliverable, node["artifact_id"])
        for code, node in nodes.items()
    }
    assert all(item is not None for item in artifacts.values())
    assert all(
        item.status is DeliverableStatus.PENDING_REVIEW
        for item in artifacts.values()
        if item is not None
    )
    final = artifacts["publishing_preparation"]
    assert final is not None
    package = final.payload["package"]
    assert [item["topic_id"] for item in package["topics"]] == [
        f"topic-{index:02d}" for index in range(1, 6)
    ]
    assert [item["topic_id"] for item in package["scripts"]] == [
        f"topic-{index:02d}" for index in range(1, 6)
    ]
    assert [item["script_id"] for item in package["visuals"]] == [
        f"script-{index:02d}" for index in range(1, 6)
    ]
    assert [item["slot_type"] for item in package["calendar_slots"]] == [
        "publish",
        "publish",
        "publish",
        "publish",
        "publish",
        "review_buffer",
        "review_buffer",
    ]
    assert sum(call.tool_code == "account.data_context" for call in tools.calls) == 1
    assert all(result["status"] == "passed" for result in package["quality"].values())
    approvals = list(
        await session.scalars(
            select(AgentToolCall).where(AgentToolCall.status == "waiting_approval")
        )
    )
    assert len(approvals) == 1
    publishing_run = await session.scalar(
        select(SkillRun).where(
            SkillRun.run_id == run.id,
            SkillRun.skill_code == "publishing_preparation",
        )
    )
    assert publishing_run is not None
    assert approvals[0].skill_run_id == publishing_run.id
    assert not any(call.tool_code == "platform.content_publish" for call in tools.calls)

    from app.services.composite_skill_runs import lock_composite_finish_approval

    task = await session.get(BrainTask, result.task_id)
    assert task is not None
    approval_lock = await lock_composite_finish_approval(
        session,
        tool_call=approvals[0],
    )
    approval_lock.tool_call.status = "success"
    publish_slots = [
        slot
        for slot in final.payload["package"]["calendar_slots"]
        if slot["slot_type"] == "publish"
    ]
    session.add_all(
        [
            ContentScheduleEntry(
                org_id=task.org_id,
                account_id=account.id,
                content_item_id=final.content_item_id,
                source_artifact_id=final.id,
                source_artifact_version=final.version,
                created_by_id=task.created_by_id,
                scheduled_at=datetime.fromisoformat(slot["scheduled_at"])
                + timedelta(minutes=5),
                timezone=slot["timezone"],
                status="planned",
            )
            for slot in publish_slots
        ]
    )
    await session.flush()
    with pytest.raises(
        SkillApprovalConflict,
        match="SKILL_APPROVAL_SCHEDULE_CONFLICT",
    ):
        await finalize_skill_finish_approval(
            session,
            tool_call=approval_lock.tool_call,
            task=task,
            approved=True,
            comment="拒绝错配重放",
            prelocked=approval_lock.runtime_lock,
        )
    await session.execute(
        delete(ContentScheduleEntry).where(
            ContentScheduleEntry.source_artifact_id == final.id,
            ContentScheduleEntry.source_artifact_version == final.version,
        )
    )
    finalized = await finalize_skill_finish_approval(
        session,
        tool_call=approval_lock.tool_call,
        task=task,
        approved=True,
        comment="确认安排",
        prelocked=approval_lock.runtime_lock,
    )
    assert finalized.handled is True
    assert any(
        intent.event_type == "pending_work.updated" and intent.turn_id is None
        for intent in finalized.publish_intents
    )
    await session.commit()
    rows = list(
        await session.scalars(
            select(ContentScheduleEntry)
            .where(ContentScheduleEntry.source_artifact_id == final.id)
            .order_by(ContentScheduleEntry.scheduled_at)
        )
    )
    assert len(rows) == 5
    assert {item.source_artifact_id for item in rows} == {final.id}
    assert {item.source_artifact_version for item in rows} == {final.version}
    await session.refresh(final)
    assert final.status is DeliverableStatus.APPROVED
    await session.refresh(publishing_run)
    assert publishing_run.output_snapshot["approval"]["schedule_entry_ids"] == [
        item.id for item in rows
    ]
    assert (
        await session.scalar(
            select(func.count(Event.id)).where(
                Event.type == "pending_work.updated",
                Event.content_item_id == final.content_item_id,
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_concurrent_weekly_approval_requests_create_exactly_five_schedule_rows(
    client,
    session,
    admin,
    monkeypatch,
) -> None:
    account, thread, turn, run = await _scope(
        session,
        admin,
        key="weekly-concurrent-approval",
        message="结合最近数据和对标内容，规划并制作下周抖音内容",
    )
    tools = _Tools()
    result = await SkillRuntime(
        tool_executor=tools,
        harness=_Harness(),
        critic=_AcceptingCritic(),
    ).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="operation_iteration",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="operation_iteration",
            structured_input={"cycle_days": 7, "topic_count": 5},
        ),
    )
    publishing_run = await session.scalar(
        select(SkillRun).where(
            SkillRun.run_id == run.id,
            SkillRun.skill_code == "publishing_preparation",
        )
    )
    assert result.status == "waiting_permission"
    assert publishing_run is not None
    call = await session.scalar(
        select(AgentToolCall).where(
            AgentToolCall.skill_run_id == publishing_run.id,
            AgentToolCall.status == "waiting_approval",
        )
    )
    assert call is not None
    artifact_id = call.meta["artifact_id"]
    login = await client.post(
        "/auth/login",
        json={"email": "admin@test.com", "password": "admin-pw-123"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    from app.db import get_session
    from app.main import app
    from app.services.composite_skill_runs import lock_composite_finish_approval
    from app.services.turn_interrupts import request_interrupt

    preapproval_lock = await lock_composite_finish_approval(session, tool_call=call)
    await request_interrupt(
        session,
        user=admin,
        run_id=run.id,
        kind="approval",
        semantic_key=f"tool-approval:{call.id}",
        public_message=f"Confirm {call.tool_name}.",
        action_label="Confirm action",
        response_schema={
            "type": "object",
            "required": ["approved"],
            "properties": {"approved": {"type": "boolean"}},
        },
        skill_run_id=publishing_run.id,
        source_type="tool_call",
        source_id=call.id,
        source_version=1,
        prelocked=preapproval_lock.runtime_lock,
    )
    await session.commit()
    maker = async_sessionmaker(session.bind, expire_on_commit=False)

    async def independent_session():
        async with maker() as request_session:
            yield request_session

    original_override = app.dependency_overrides[get_session]
    app.dependency_overrides[get_session] = independent_session
    published: list[tuple[str, dict[str, object]]] = []

    async def capture_publish(
        event_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> None:
        published.append((event_type, payload))

    monkeypatch.setattr(
        "app.orchestrator.brain_runtime.publish_realtime_event",
        capture_publish,
    )
    try:
        requests = [
            client.post(
                f"/brain/tool-calls/{call.id}/approve",
                headers=headers,
                json={"approved": True, "comment": "确认安排"},
            ),
            client.post(
                f"/brain/tool-calls/{call.id}/approve",
                headers=headers,
                json={"approved": True, "comment": "确认安排"},
            ),
        ]
        if session.bind.dialect.name == "sqlite":
            # The test fixture uses one StaticPool connection, which cannot run
            # independent SAVEPOINT stacks concurrently. Each request still
            # receives an independent Session; PostgreSQL runs the true gather.
            responses = [await request for request in requests]
        else:
            responses = await asyncio.gather(*requests)
    finally:
        app.dependency_overrides[get_session] = original_override

    assert sorted(item.status_code for item in responses) == [200, 200]
    assert any(
        event_type == "pending_work.updated"
        and payload == {"account_id": account.id}
        for event_type, payload in published
    )
    session.expire_all()
    rows = list(
        await session.scalars(
            select(ContentScheduleEntry).where(
                ContentScheduleEntry.source_artifact_id == artifact_id
            )
        )
    )
    assert len(rows) == 5
    artifact = await session.get(Deliverable, artifact_id)
    assert artifact is not None and artifact.status is DeliverableStatus.APPROVED
    assert {item.source_artifact_version for item in rows} == {artifact.version}
    assert (
        await session.scalar(
            select(func.count(AgentToolCall.id)).where(
                AgentToolCall.tool_code == "platform.content_publish"
            )
        )
        == 0
    )


@pytest.mark.asyncio
async def test_fresh_operation_reuses_one_audited_evidence_read_for_topic_planning(
    session,
    admin,
) -> None:
    account, thread, turn, run = await _scope(
        session,
        admin,
        key="fresh-operation-evidence",
        message="结合最近数据和对标内容，规划并制作下周抖音内容",
    )
    tools = _Tools()
    harness = _Harness()
    runtime = SkillRuntime(tool_executor=tools, harness=harness)

    result = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="operation_iteration",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="operation_iteration",
            structured_input={},
        ),
    )

    assert result.status == "waiting_user"
    assert result.report["cycle_days"] == 7
    assert result.report["source_artifacts"] == [
        {
            "kind": "data_import_batch",
            "id": 7,
            "data_domains": ["account_metrics"],
        },
        {
            "kind": "data_import_batch",
            "id": 8,
            "data_domains": ["benchmarks"],
        },
    ]
    assert sum(call.tool_code == "account.data_context" for call in tools.calls) == 1
    root_run = await session.scalar(
        select(SkillRun).where(
            SkillRun.run_id == run.id,
            SkillRun.skill_code == "operation_iteration",
        )
    )
    assert root_run is not None
    data_call = await session.scalar(
        select(AgentToolCall).where(
            AgentToolCall.skill_run_id == root_run.id,
            AgentToolCall.tool_code == "account.data_context",
        )
    )
    topic_run = await session.scalar(
        select(SkillRun).where(
            SkillRun.run_id == run.id,
            SkillRun.skill_code == "topic_planning",
        )
    )
    assert data_call is not None and topic_run is not None
    assert data_call.skill_run_id == root_run.id
    server_context = topic_run.input_snapshot["_server_context"]
    assert server_context["tool_audit_refs"]["account.data_context"] == {
        "tool_call_id": data_call.id,
        "source_skill_run_id": root_run.id,
    }
    assert server_context["preloaded_tool_results"]["account.data_context"]["sources"] == [
        {"batch_id": 7, "data_domains": ["account_metrics"]},
        {"batch_id": 8, "data_domains": ["benchmarks"]},
    ]
    preloaded_upstream = next(
        item
        for item in harness.upstreams[0]["tool_results"]["items"]
        if item["tool_code"] == "account.data_context"
    )
    assert preloaded_upstream == {
        "tool_code": "account.data_context",
        "result": server_context["preloaded_tool_results"]["account.data_context"],
    }

    script_node = next(
        node
        for node in result.report["child_skill_graph"]
        if node["skill_code"] == "script_generation"
    )
    visual_node = next(
        node
        for node in result.report["child_skill_graph"]
        if node["skill_code"] == "visual_brief_generation"
    )
    assert script_node["status"] == "completed"
    assert visual_node["status"] == "needs_review"
    assert result.report["interrupt"]["kind"] == "child_skill_paused"
    assert result.report["interrupt"]["skill_code"] == "visual_brief_generation"
    script_deliverable = await session.get(Deliverable, script_node["artifact_id"])
    assert script_deliverable is not None
    assert script_deliverable.status is DeliverableStatus.PENDING_REVIEW
    visual_run = await session.scalar(
        select(SkillRun).where(
            SkillRun.run_id == run.id,
            SkillRun.skill_code == "visual_brief_generation",
        )
    )
    assert visual_run is not None
    lineage = visual_run.input_snapshot["_server_context"]["lineage_refs"]
    assert lineage == [
        {
            "artifact_id": script_deliverable.id,
            "version": script_deliverable.version,
            "source_skill_run_id": script_deliverable.skill_run_id,
            "parent_skill_run_id": root_run.id,
        }
    ]
    await accept_artifact(session, admin, artifact_id=script_node["artifact_id"])
    await session.refresh(run)
    await session.refresh(root_run)
    claimed = await acquire_agent_run(
        session,
        run.id,
        worker_id="fresh-operation-recovery",
        lease_seconds=60,
    )
    assert claimed is not None
    await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=claimed,
        skill_code="operation_iteration",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=claimed,
            skill_code="operation_iteration",
            structured_input={},
        ),
        lease_owner="fresh-operation-recovery",
        resume_skill_run=root_run,
    )
    assert sum(call.tool_code == "account.data_context" for call in tools.calls) == 1
    assert (
        await session.scalar(
            select(func.count(AgentToolCall.id)).where(
                AgentToolCall.skill_run_id == root_run.id,
                AgentToolCall.tool_code == "account.data_context",
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_bounded_stage_overlaps_isolated_work_and_preserves_definition_order():
    active = 0
    peak = 0
    session_ids: set[int] = set()

    async def execute(index: int) -> str:
        nonlocal active, peak
        session_id = index + 100
        session_ids.add(session_id)
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.04 if index == 0 else 0.01)
        active -= 1
        return f"expert-{index}"

    started = monotonic()
    results = await run_bounded_stage(
        [lambda index=index: execute(index) for index in range(4)],
        limit=3,
    )

    assert results == ["expert-0", "expert-1", "expert-2", "expert-3"]
    assert peak == 3
    assert len(session_ids) == 4
    assert monotonic() - started < 0.075


@pytest.mark.asyncio
async def test_bounded_stage_waits_for_all_failures_before_raising():
    completed: list[str] = []

    async def fail() -> str:
        await asyncio.sleep(0.01)
        raise RuntimeError("expert failed")

    async def finish() -> str:
        await asyncio.sleep(0.02)
        completed.append("audited")
        return "done"

    with pytest.raises(RuntimeError, match="expert failed"):
        await run_bounded_stage([fail, finish], limit=3)

    assert completed == ["audited"]


@pytest.mark.parametrize(
    ("skill_code", "message", "artifact_type", "expert_codes", "expected_status"),
    [
        (
            "topic_planning",
            "给我策划未来一周的五个选题",
            "topic_plan",
            [AgentCode.CONTENT_DIRECTOR],
            "completed",
        ),
        (
            "script_generation",
            "写一个玻璃贴膜避坑短视频脚本",
            "video_script",
            [AgentCode.CONTENT_DIRECTOR],
            "completed",
        ),
        (
            "publishing_preparation",
            "给这个内容生成发布前检查清单",
            "publish_calendar",
            [AgentCode.OPERATOR],
            "waiting_permission",
        ),
        (
            "performance_review",
            "复盘最近30天的账号表现",
            "review_report",
            [AgentCode.OPERATOR, AgentCode.CONTENT_DIRECTOR],
            "completed",
        ),
    ],
)
@pytest.mark.asyncio
async def test_operating_skill_executes_bounded_experts_and_persists_artifact(
    session,
    admin,
    skill_code,
    message,
    artifact_type,
    expert_codes,
    expected_status,
) -> None:
    account, thread, turn, run = await _scope(
        session,
        admin,
        key=f"skill-{skill_code}",
        message=message,
    )
    harness = _Harness()
    runtime = SkillRuntime(tool_executor=_Tools(), harness=harness)

    result = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code=skill_code,
    )

    assert result.status == expected_status
    assert result.artifact_type == artifact_type
    assert result.artifact_id is not None
    assert result.report["account_id"] == account.id
    assert result.report["participating_experts"] == [code.value for code in expert_codes]
    assert harness.calls == expert_codes
    assert await session.scalar(select(func.count(BrainTask.id))) == 1
    assert await session.scalar(select(func.count(SkillRun.id))) == 1
    assert await session.scalar(select(func.count(Deliverable.id))) == 1


@pytest.mark.asyncio
async def test_generic_operating_skill_persists_real_execution_boundaries(
    session,
    admin,
) -> None:
    account, thread, turn, run = await _scope(
        session,
        admin,
        key="generic-durable-boundaries",
        message="复盘最近30天的账号表现",
    )

    result = await SkillRuntime(tool_executor=_Tools(), harness=_Harness()).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="performance_review",
    )
    reliable_types = {
        "step.started",
        "step.completed",
        "step.failed",
        "deliverable.updated",
        "turn.completed",
        "turn.failed",
    }
    events = list(
        await session.scalars(
            select(Event)
            .where(Event.turn_id == turn.id, Event.type.in_(reliable_types))
            .order_by(Event.sequence)
        )
    )

    assert result.status == "completed"
    assert [(event.type, event.payload.get("step")) for event in events] == [
        ("step.started", "read_data"),
        ("step.completed", "read_data"),
        ("step.started", "specialist_work"),
        ("step.completed", "specialist_work"),
        ("step.started", "prepare_deliverable"),
        ("deliverable.updated", None),
        ("step.completed", "prepare_deliverable"),
        ("turn.completed", None),
    ]
    assert [event.sequence for event in events] == list(range(1, 9))
    assert {
        (event.org_id, event.account_id, event.thread_id, event.turn_id, event.run_id)
        for event in events
    } == {(admin.org_id, account.id, thread.id, turn.id, run.id)}
    assert {event.skill_run_id for event in events} == {result.skill_run_id}


@pytest.mark.asyncio
async def test_deliverable_and_updated_event_rollback_together(session, admin) -> None:
    account, thread, turn, run = await _scope(
        session,
        admin,
        key="deliverable-event-rollback",
        message="生成报告",
    )
    content = ContentItem(
        created_by_id=admin.id,
        account_id=account.id,
        title="rollback report",
        current_stage=ContentStage.OPERATION,
        status=ContentStatus.IN_PROGRESS,
    )
    session.add(content)
    await session.flush()
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        content_item_id=content.id,
        title="rollback task",
        status=BrainTaskStatus.RUNNING,
        progress=0,
        current_focus="running",
    )
    session.add(task)
    await session.flush()
    run.task_id = task.id
    skill_run = SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        task_id=task.id,
        idempotency_key="skill:rollback",
        skill_code="performance_review",
        skill_version=1,
        status="running",
        input_snapshot={},
        output_snapshot={},
    )
    session.add(skill_run)
    await session.commit()
    scope = await RuntimeScope.from_conversation(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
    )
    scope = await scope.bind_task(session, task)
    scope = await scope.bind_skill(session, skill_run)

    deliverable = await write_runtime_deliverable(
        session,
        scope=scope,
        content=content,
        agent_code="06-operator",
        deliverable_type=DeliverableType.REVIEW_REPORT,
        status=DeliverableStatus.PENDING_REVIEW,
        payload={"summary": "pending transaction"},
    )
    deliverable_id = deliverable.id
    durable_event = await session.scalar(
        select(Event).where(
            Event.turn_id == turn.id,
            Event.type == "deliverable.updated",
        )
    )
    assert durable_event is not None
    durable_event_id = durable_event.id

    await session.rollback()

    assert await session.get(Deliverable, deliverable_id) is None
    assert await session.get(Event, durable_event_id) is None


@pytest.mark.asyncio
async def test_failed_stage_context_does_not_leak_into_next_execution(
    session,
    admin,
) -> None:
    class PausingTools:
        async def execute(self, **_kwargs):
            return SimpleNamespace(status="failed", result=None, tool_call=None)

    _first_account, first_thread, first_turn, first_run = await _scope(
        session,
        admin,
        key="stage-context-first",
        message="先失败",
    )
    first = await SkillRuntime(
        tool_executor=PausingTools(),
        harness=_Harness(),
    ).execute(
        session,
        user=admin,
        thread=first_thread,
        turn=first_turn,
        run=first_run,
        skill_code="performance_review",
    )
    _second_account, second_thread, second_turn, second_run = await _scope(
        session,
        admin,
        key="stage-context-second",
        message="再成功",
    )
    second = await SkillRuntime(
        tool_executor=_Tools(),
        harness=_Harness(),
    ).execute(
        session,
        user=admin,
        thread=second_thread,
        turn=second_turn,
        run=second_run,
        skill_code="performance_review",
    )
    leaked_failures = list(
        await session.scalars(
            select(Event).where(
                Event.turn_id == second_turn.id,
                Event.type == "step.failed",
            )
        )
    )

    assert first.status == "failed"
    assert second.status == "completed"
    assert leaked_failures == []


@pytest.mark.asyncio
async def test_content_publishing_persists_only_its_real_publish_stage(
    session,
    admin,
) -> None:
    from tests.test_content_publishing_skill import _PublishingTools
    from tests.test_visual_brief_skill import _source

    account, thread, turn, run = await _scope(
        session,
        admin,
        key="durable-content-publishing",
        message="发布这个内容",
    )
    source = await _source(
        session,
        account_id=account.id,
        created_by_id=admin.id,
        status=DeliverableStatus.APPROVED,
    )
    result = await SkillRuntime(tool_executor=_PublishingTools()).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="content_publishing",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="content_publishing",
            structured_input={"approved_publish_artifact_id": source.id},
        ),
    )
    events = list(
        await session.scalars(
            select(Event)
            .where(
                Event.turn_id == turn.id,
                Event.type.in_(
                    {
                        "step.started",
                        "step.completed",
                        "step.failed",
                        "turn.completed",
                    }
                ),
            )
            .order_by(Event.sequence)
        )
    )

    assert result.status == "completed"
    assert [(event.type, event.payload.get("step")) for event in events] == [
        ("step.started", "publish_content"),
        ("step.completed", "publish_content"),
        ("turn.completed", None),
    ]


@pytest.mark.asyncio
async def test_operation_iteration_real_runtime_child_lifecycle_retry_gate(
    session,
    admin,
    monkeypatch,
) -> None:
    from tests.test_operation_iteration_skill import _artifact

    account, thread, turn, run = await _scope(
        session,
        admin,
        key="durable-operation-iteration",
        message="根据复盘安排下周运营",
    )
    review = await _artifact(
        session,
        admin,
        account,
        kind=DeliverableType.REVIEW_REPORT,
        status=DeliverableStatus.APPROVED,
    )
    tools = _Tools()
    harness = _Harness()

    class ObservedRuntime(SkillRuntime):
        def __init__(self) -> None:
            super().__init__(
                tool_executor=tools,
                harness=harness,
            )
            self.child_requests: list[CapabilityRequest] = []

        async def _execute_child_skill(self, *args, capability_request, **kwargs):
            parent_stage_started = await args[0].scalar(
                select(Event.id).where(
                    Event.turn_id == turn.id,
                    Event.type == "step.started",
                    Event.payload["step"].as_string() == "prepare_deliverable",
                )
            )
            assert parent_stage_started is not None
            self.child_requests.append(capability_request)
            return await super()._execute_child_skill(
                *args,
                capability_request=capability_request,
                **kwargs,
            )

    runtime = ObservedRuntime()
    price_constraint = {
        "constraint_type": "OFFER_TERMS",
        "raw_requirement": "第一条不要讲价格",
        "target_scope": {
            "kind": "content_item_indexes",
            "item_indexes": [1],
        },
    }

    async def external_counts() -> dict[str, int]:
        return {
            "provider": len(harness.calls),
            "tool": len(tools.calls),
            "expert": int(
                await session.scalar(
                    select(func.count(AgentInvocation.id)).where(
                        AgentInvocation.run_id == run.id
                    )
                )
                or 0
            ),
            "durable_tool": int(
                await session.scalar(
                    select(func.count(AgentToolCall.id)).where(
                        AgentToolCall.skill_run_id.in_(
                            select(SkillRun.id).where(SkillRun.run_id == run.id)
                        )
                    )
                )
                or 0
            ),
        }

    result = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="operation_iteration",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="operation_iteration",
            structured_input={
                "confirmed_review_artifact_id": review.id,
                "cycle_days": 7,
                "topic_count": 4,
                "script_duration_seconds": 30,
                "constraints": [price_constraint],
            },
        ),
    )
    events = list(
        await session.scalars(
            select(Event)
            .where(
                Event.turn_id == turn.id,
                Event.type.in_(
                    {
                        "step.started",
                        "step.completed",
                        "step.failed",
                        "deliverable.updated",
                        "turn.completed",
                    }
                ),
            )
            .order_by(Event.sequence)
        )
    )

    assert result.status == "waiting_user"
    nodes = {node["skill_code"]: node for node in result.report["child_skill_graph"]}
    script_node = nodes["script_generation"]
    topic_node = nodes["topic_planning"]
    assert script_node["input"] == {"duration_seconds": 30}
    assert topic_node["input"] == {"days": 7, "topic_count": 4}
    assert topic_node["status"] == script_node["status"] == "completed"
    assert isinstance(topic_node["artifact_id"], int)
    assert isinstance(script_node["artifact_id"], int)
    requests = {
        request.requested_skill_code: {
            "structured_input": request.structured_input,
            "constraints": request.constraints,
        }
        for request in runtime.child_requests
    }
    assert requests == {
        "topic_planning": {
            "structured_input": {"days": 7, "topic_count": 4},
            "constraints": [],
        },
        "script_generation": {
            "structured_input": {"duration_seconds": 30},
            "constraints": [
                '{"constraint_type":"OFFER_TERMS","raw_requirement":"第一条不要讲价格",'
                '"target_scope":{"item_indexes":[1],"kind":"content_item_indexes"}}'
            ],
        },
    }
    child_runs = {
        row.skill_code: row
        for row in await session.scalars(
            select(SkillRun).where(
                SkillRun.run_id == run.id,
                SkillRun.skill_code.in_({"topic_planning", "script_generation"}),
            )
        )
    }
    assert child_runs["topic_planning"].input_snapshot == {
        "account_id": account.id,
        "days": 7,
        "topic_count": 4,
    }
    assert child_runs["script_generation"].input_snapshot == {
        "account_id": account.id,
        "duration_seconds": 30,
        "presentation_format": "storyboard",
        "_server_request_constraints": requests["script_generation"]["constraints"],
    }
    assert all(row.status == "completed" for row in child_runs.values())
    data_context = next(
        call for call in tools.calls if call.tool_code == "account.data_context"
    )
    assert data_context.arguments["days"] == 7
    assert [upstream["structured_input"] for upstream in harness.upstreams] == [
        {"account_id": account.id, "days": 7, "topic_count": 4},
        {
            "account_id": account.id,
            "duration_seconds": 30,
            "presentation_format": "storyboard",
            "_server_request_constraints": requests["script_generation"]["constraints"],
        },
    ]
    visual_node = nodes["visual_brief_generation"]
    assert visual_node["status"] == "waiting_user"
    assert visual_node["error_code"] == "DEPENDENCY_ARTIFACT_APPROVAL_REQUIRED"
    assert nodes["content_calendar_planning"]["status"] == "pending"
    assert nodes["publishing_preparation"]["status"] == "pending"
    assert result.report["required_children_completed"] is False
    assert result.report["interrupt"] == {
        "kind": "artifact_approval_required",
        "skill_code": "visual_brief_generation",
        "source_artifact_ids": [script_node["artifact_id"]],
    }
    await session.refresh(run)
    parent_run = await session.scalar(
        select(SkillRun).where(
            SkillRun.run_id == run.id,
            SkillRun.skill_code == "operation_iteration",
        )
    )
    task = await session.get(BrainTask, result.task_id)
    assert run.status == "waiting_user"
    assert parent_run is not None and parent_run.status == "waiting_permission"
    assert task is not None and task.progress < 100
    assert sum(event.type == "turn.completed" for event in events) == 0
    assert await external_counts() == {
        "provider": 2,
        "tool": 3,
        "expert": 2,
        "durable_tool": 3,
    }

    calls_before_retry = (len(tools.calls), len(harness.calls))
    replay = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="operation_iteration",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="operation_iteration",
            structured_input={
                "confirmed_review_artifact_id": review.id,
                "cycle_days": 7,
                "topic_count": 4,
                "script_duration_seconds": 30,
                "constraints": [price_constraint],
            },
        ),
    )
    assert replay.skill_run_id == result.skill_run_id
    assert (len(tools.calls), len(harness.calls)) == calls_before_retry
    assert await external_counts() == {
        "provider": 2,
        "tool": 3,
        "expert": 2,
        "durable_tool": 3,
    }

    # Simulate a crash after child commits but before the parent snapshot commits.
    stale_parent_output = deepcopy(parent_run.output_snapshot)
    stale_graph = stale_parent_output["report"]["child_skill_graph"]
    for stale_node in stale_graph[:2]:
        stale_node["status"] = "pending"
        stale_node["artifact_id"] = None
    parent_run.output_snapshot = stale_parent_output
    await session.commit()

    await accept_artifact(session, admin, artifact_id=script_node["artifact_id"])
    await session.refresh(run)
    await session.refresh(parent_run)
    assert run.status == "queued"
    assert parent_run.status == "running"
    claimed = await acquire_agent_run(
        session,
        run.id,
        worker_id="operation-test-worker",
        lease_seconds=60,
    )
    assert claimed is not None
    resumed = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=claimed,
        skill_code="operation_iteration",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=claimed,
            skill_code="operation_iteration",
            structured_input={
                "confirmed_review_artifact_id": review.id,
                "cycle_days": 7,
                "topic_count": 4,
                "script_duration_seconds": 30,
                "constraints": [price_constraint],
            },
        ),
        lease_owner="operation-test-worker",
        resume_skill_run=parent_run,
    )
    resumed_nodes = {
        node["skill_code"]: node for node in resumed.report["child_skill_graph"]
    }
    visual_node = resumed_nodes["visual_brief_generation"]
    assert resumed.status == "waiting_user"
    assert visual_node["status"] == "needs_review"
    assert visual_node["input"] == {}
    assert resumed_nodes["content_calendar_planning"]["status"] == "pending"
    assert resumed.report["interrupt"]["kind"] == "child_skill_paused"
    assert resumed.report["interrupt"]["skill_code"] == "visual_brief_generation"
    assert isinstance(resumed.report["interrupt"]["child_skill_run_id"], int)
    assert resumed.report["interrupt"]["source_artifact_ids"] == [
        visual_node["artifact_id"]
    ]
    assert runtime.child_requests[-1].requested_skill_code == "visual_brief_generation"
    assert runtime.child_requests[-1].structured_input == {
        "source_artifact_ids": [script_node["artifact_id"]]
    }
    child_ids_after_resume = {
        row.skill_code: row.id
        for row in await session.scalars(
            select(SkillRun).where(SkillRun.run_id == run.id)
        )
    }
    assert child_ids_after_resume["topic_planning"] == child_runs["topic_planning"].id
    assert child_ids_after_resume["script_generation"] == child_runs["script_generation"].id
    assert await external_counts() == {
        "provider": 4,
        "tool": 4,
        "expert": 4,
        "durable_tool": 4,
    }

    visual_child_id = resumed.report["interrupt"]["child_skill_run_id"]
    await accept_artifact(session, admin, artifact_id=visual_node["artifact_id"])
    visual_child = await session.get(SkillRun, visual_child_id)
    assert visual_child is not None and visual_child.status == "completed"
    external_after_visual_accept = await external_counts()
    await accept_artifact(session, admin, artifact_id=visual_node["artifact_id"])
    await session.refresh(visual_child)
    assert visual_child.id == visual_child_id and visual_child.status == "completed"
    assert await external_counts() == external_after_visual_accept
    await session.refresh(run)
    await session.refresh(parent_run)
    claimed = await acquire_agent_run(
        session,
        run.id,
        worker_id="operation-test-worker",
        lease_seconds=60,
    )
    assert claimed is not None
    calendar_result = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=claimed,
        skill_code="operation_iteration",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=claimed,
            skill_code="operation_iteration",
            structured_input={
                "confirmed_review_artifact_id": review.id,
                "cycle_days": 7,
                "topic_count": 4,
                "script_duration_seconds": 30,
                "constraints": [price_constraint],
            },
        ),
        lease_owner="operation-test-worker",
        resume_skill_run=parent_run,
    )
    calendar_nodes = {
        node["skill_code"]: node
        for node in calendar_result.report["child_skill_graph"]
    }
    calendar_node = calendar_nodes["content_calendar_planning"]
    assert calendar_result.status == "waiting_user"
    assert calendar_node["status"] == "needs_review"
    assert runtime.child_requests[-1].requested_skill_code == "content_calendar_planning"
    assert runtime.child_requests[-1].structured_input == {
        "source_artifact_ids": [visual_node["artifact_id"]],
        "days": 7,
    }
    publishing_node = calendar_nodes["publishing_preparation"]
    assert publishing_node["status"] == "pending"
    assert calendar_result.report["interrupt"]["kind"] == "child_skill_paused"
    assert calendar_result.report["interrupt"]["skill_code"] == (
        "content_calendar_planning"
    )
    assert isinstance(calendar_result.report["interrupt"]["child_skill_run_id"], int)
    assert calendar_result.report["interrupt"]["source_artifact_ids"] == [
        calendar_node["artifact_id"]
    ]
    assert await external_counts() == {
        "provider": 5,
        "tool": 5,
        "expert": 5,
        "durable_tool": 5,
    }

    await accept_artifact(session, admin, artifact_id=calendar_node["artifact_id"])
    await session.refresh(run)
    await session.refresh(parent_run)
    claimed = await acquire_agent_run(
        session,
        run.id,
        worker_id="operation-test-worker",
        lease_seconds=60,
    )
    assert claimed is not None
    publishing_result = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=claimed,
        skill_code="operation_iteration",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=claimed,
            skill_code="operation_iteration",
            structured_input={
                "confirmed_review_artifact_id": review.id,
                "cycle_days": 7,
                "topic_count": 4,
                "script_duration_seconds": 30,
                "constraints": [price_constraint],
            },
        ),
        lease_owner="operation-test-worker",
        resume_skill_run=parent_run,
    )
    publishing_nodes = {
        node["skill_code"]: node
        for node in publishing_result.report["child_skill_graph"]
    }
    publishing_node = publishing_nodes["publishing_preparation"]
    assert publishing_result.status == "waiting_permission"
    assert publishing_node["status"] == "waiting_permission"
    assert runtime.child_requests[-1].requested_skill_code == "publishing_preparation"
    assert runtime.child_requests[-1].structured_input == {
        "content_item_id": task.content_item_id
    }
    assert await external_counts() == {
        "provider": 6,
        "tool": 6,
        "expert": 6,
        "durable_tool": 6,
    }
    publishing_child = await session.scalar(
        select(SkillRun).where(
            SkillRun.run_id == run.id,
            SkillRun.skill_code == "publishing_preparation",
        )
    )
    assert publishing_child is not None
    approval_call = await session.scalar(
        select(AgentToolCall).where(
            AgentToolCall.skill_run_id == publishing_child.id,
            AgentToolCall.status == "waiting_approval",
        )
    )
    assert approval_call is not None
    from app.services.composite_skill_runs import lock_composite_finish_approval

    approval_lock = await lock_composite_finish_approval(
        session, tool_call=approval_call
    )
    approval_call = approval_lock.tool_call
    approval_call.status = "success"
    approval_commit_spy = AsyncMock(wraps=session.commit)
    monkeypatch.setattr(session, "commit", approval_commit_spy)
    finish_result = await finalize_skill_finish_approval(
        session,
        tool_call=approval_call,
        task=task,
        approved=True,
        comment="approved",
        prelocked=approval_lock.runtime_lock,
    )
    assert finish_result.handled is True
    assert finish_result.publish_intents == ()
    approval_commit_spy.assert_not_awaited()
    await session.commit()
    assert approval_commit_spy.await_count == 1
    await session.refresh(run)
    await session.refresh(parent_run)
    await session.refresh(publishing_child)
    assert publishing_child.status == "completed"
    assert parent_run.status == "running"
    assert run.status == "queued"

    plan_deliverables = list(
        await session.scalars(
            select(Deliverable).where(
                Deliverable.skill_run_id == parent_run.id,
                Deliverable.agent_code == AgentCode.DECISION.value,
            )
        )
    )
    assert plan_deliverables == []
    assert parent_run.output_snapshot["report"]["child_skill_graph"][-1]["status"] == (
        "waiting_permission"
    )

    external_counts_before_finish = (len(tools.calls), len(harness.calls))
    claimed = await acquire_agent_run(
        session,
        run.id,
        worker_id="operation-test-worker",
        lease_seconds=60,
    )
    assert claimed is not None
    completed = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=claimed,
        skill_code="operation_iteration",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=claimed,
            skill_code="operation_iteration",
            structured_input={
                "confirmed_review_artifact_id": review.id,
                "cycle_days": 7,
                "topic_count": 4,
                "script_duration_seconds": 30,
                "constraints": [price_constraint],
            },
        ),
        lease_owner="operation-test-worker",
        resume_skill_run=parent_run,
    )
    assert completed.status == "completed"
    assert completed.report["required_children_completed"] is True
    assert all(
        node["status"] == "completed"
        for node in completed.report["child_skill_graph"]
        if node["required"]
    )
    assert (len(tools.calls), len(harness.calls)) == external_counts_before_finish
    assert await external_counts() == {
        "provider": 6,
        "tool": 6,
        "expert": 6,
        "durable_tool": 6,
    }
    await session.refresh(run)
    await session.refresh(parent_run)
    await session.refresh(task)
    await session.refresh(turn)
    assert run.status == "completed"
    assert parent_run.status == "completed"
    assert task.status == BrainTaskStatus.COMPLETED
    assert task.progress == 100
    assert turn.status == "completed"
    terminal_events = list(
        await session.scalars(
            select(Event).where(
                Event.turn_id == turn.id,
                Event.type == "turn.completed",
            )
        )
    )
    assert len(terminal_events) == 1
    final_plan_deliverables = list(
        await session.scalars(
            select(Deliverable).where(
                Deliverable.skill_run_id == parent_run.id,
                Deliverable.agent_code == AgentCode.DECISION.value,
            )
        )
    )
    assert len(final_plan_deliverables) == 1
    assert final_plan_deliverables[0].payload["required_children_completed"] is True
    assert all(
        node["status"] == "completed"
        for node in final_plan_deliverables[0].payload["child_skill_graph"]
        if node["required"]
    )


@pytest.mark.asyncio
async def test_composite_accept_before_pause_recheck_never_loses_wakeup(
    session,
    admin,
    monkeypatch,
) -> None:
    from app.services.composite_skill_runs import (
        pause_composite_parent_for_artifacts,
    )
    from tests.test_operation_iteration_skill import _artifact

    account, thread, turn, run = await _scope(
        session,
        admin,
        key="composite-accept-before-pause",
        message="根据复盘安排下周运营",
    )
    review = await _artifact(
        session,
        admin,
        account,
        kind=DeliverableType.REVIEW_REPORT,
        status=DeliverableStatus.APPROVED,
    )
    result = await SkillRuntime(tool_executor=_Tools(), harness=_Harness()).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="operation_iteration",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="operation_iteration",
            structured_input={"confirmed_review_artifact_id": review.id},
        ),
    )
    parent = await session.get(SkillRun, result.skill_run_id)
    task = await session.get(BrainTask, result.task_id)
    script_node = next(
        node
        for node in result.report["child_skill_graph"]
        if node["skill_code"] == "script_generation"
    )
    assert parent is not None and task is not None
    script_artifact = await session.get(Deliverable, script_node["artifact_id"])
    assert script_artifact is not None
    script_artifact.version = 2
    historical_run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        task_id=task.id,
        thread_id=thread.id,
        turn_id=turn.id,
        client_message_id="composite-accept-historical-run",
        status="completed",
        phase="completed",
        request_payload={},
        result_payload={},
    )
    session.add(historical_run)
    await session.flush()
    historical_skill = SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=historical_run.id,
        task_id=task.id,
        idempotency_key="composite-accept-historical-skill",
        skill_code="script_generation",
        skill_version=1,
        status="completed",
        input_snapshot={},
        output_snapshot={"status": "completed"},
    )
    session.add(historical_skill)
    await session.flush()
    historical_artifact = Deliverable(
        content_item_id=script_artifact.content_item_id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=historical_run.id,
        skill_run_id=historical_skill.id,
        agent_code=script_artifact.agent_code,
        type=script_artifact.type,
        version=1,
        status=DeliverableStatus.PENDING_REVIEW,
        payload={"summary": "historical script"},
    )
    session.add(historical_artifact)
    parent.status = "running"
    run.status = "running"
    turn.status = "running"
    task.status = BrainTaskStatus.RUNNING
    await session.commit()

    maker = async_sessionmaker(session.bind, expire_on_commit=False)
    async with maker() as approval_session:
        concurrent_admin = await approval_session.get(type(admin), admin.id)
        assert concurrent_admin is not None
        await accept_artifact(
            approval_session,
            concurrent_admin,
            artifact_id=script_node["artifact_id"],
        )

    await session.refresh(parent)
    await session.refresh(run)
    commit_spy = AsyncMock(wraps=session.commit)
    monkeypatch.setattr(session, "commit", commit_spy)
    paused = await pause_composite_parent_for_artifacts(
        session,
        parent_skill_run=parent,
        source_artifact_ids=[script_node["artifact_id"]],
    )

    assert paused is False
    await session.refresh(historical_artifact)
    assert historical_artifact.status is DeliverableStatus.SUPERSEDED
    assert parent.status == "running"
    assert run.status == "running"
    commit_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_topic_skill_honors_structured_days_and_topic_count(session, admin) -> None:
    account, thread, turn, run = await _scope(
        session,
        admin,
        key="typed-topic-input",
        message="规划未来14天的10个选题",
    )
    runtime = SkillRuntime(tool_executor=_Tools(), harness=_Harness())

    result = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="topic_planning",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="topic_planning",
            structured_input={"days": 14, "topic_count": 10},
        ),
    )

    skill_run = await session.scalar(select(SkillRun).where(SkillRun.run_id == run.id))
    assert skill_run is not None
    assert skill_run.input_snapshot == {
        "account_id": account.id,
        "days": 14,
        "topic_count": 10,
    }
    assert result.report["period"] == "未来 14 天"
    assert len(result.report["topics"]) == 10


@pytest.mark.asyncio
async def test_script_skill_prefers_explicit_duration_over_expert_default(session, admin) -> None:
    account, thread, turn, run = await _scope(
        session,
        admin,
        key="typed-script-input",
        message="生成一个30秒脚本",
    )
    runtime = SkillRuntime(tool_executor=_Tools(), harness=_Harness())

    result = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="script_generation",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="script_generation",
            structured_input={
                "duration_seconds": 30,
                "presentation_format": "product_video",
            },
        ),
    )

    skill_run = await session.scalar(select(SkillRun).where(SkillRun.run_id == run.id))
    assert skill_run is not None
    assert skill_run.input_snapshot == {
        "account_id": account.id,
        "duration_seconds": 30,
        "presentation_format": "product_video",
    }
    assert result.report["duration_seconds"] == 30
    assert result.report["presentation_format"] == "product_video"


@pytest.mark.asyncio
async def test_skill_recovery_rejects_changed_structured_input(session, admin) -> None:
    account, thread, turn, run = await _scope(
        session,
        admin,
        key="typed-recovery-conflict",
        message="规划未来14天的10个选题",
    )
    runtime = SkillRuntime(tool_executor=_Tools(), harness=_Harness())
    original_request = _capability_request(
        admin=admin,
        account=account,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="topic_planning",
        structured_input={"days": 14, "topic_count": 10},
    )
    await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="topic_planning",
        capability_request=original_request,
    )

    changed_request = original_request.model_copy(
        update={"structured_input": {"days": 7, "topic_count": 10}}
    )
    with pytest.raises(SkillRecoveryConflict, match="SKILL_RECOVERY_INPUT_CONFLICT"):
        await runtime.execute(
            session,
            user=admin,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="topic_planning",
            capability_request=changed_request,
        )


@pytest.mark.asyncio
async def test_publishing_preparation_executes_declared_prepare_tool(session, admin) -> None:
    account, thread, turn, run = await _scope(
        session,
        admin,
        key="publishing-tool-plan",
        message="为这条内容生成发布准备包",
    )
    runtime = SkillRuntime(tool_executor=_Tools(), harness=_Harness())

    result = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="publishing_preparation",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="publishing_preparation",
            structured_input={},
        ),
    )

    calls = list(
        await session.scalars(
            select(AgentToolCall).where(AgentToolCall.skill_run_id == result.skill_run_id)
        )
    )
    assert result.status == "waiting_permission"
    assert [(call.tool_code, call.status) for call in calls] == [
        ("publish_package_prepare", "waiting_approval")
    ]
    assert calls[0].requires_human_confirmation is True
    assert calls[0].permission_mode == "confirm"
    skill_run = await session.get(SkillRun, result.skill_run_id)
    assert skill_run is not None
    assert skill_run.status == "waiting_permission"
    await session.refresh(run)
    await session.refresh(turn)
    assert run.status == "waiting_permission"
    assert turn.status == "waiting_permission"
    assert (
        await session.scalar(
            select(func.count(Event.id)).where(
                Event.turn_id == turn.id,
                Event.type.in_(
                    {
                        "turn.completed",
                        "turn.failed",
                        "turn.blocked",
                        "turn.cancelled",
                        "turn.stopped",
                    }
                ),
            )
        )
        == 0
    )
    paused_events = list(
        await session.scalars(
            select(Event).where(
                Event.turn_id == turn.id,
                Event.type == "turn.paused",
            )
        )
    )
    assert len(paused_events) == 1
    assert paused_events[0].payload == {
        "status": "waiting_permission",
        "message": paused_events[0].payload["message"],
    }


async def _nested_finish_approval_scope(session, admin, *, key: str):
    account, thread, turn, run = await _scope(
        session,
        admin,
        key=key,
        message="Prepare the nested publishing package",
    )
    content = ContentItem(
        account_id=account.id,
        created_by_id=admin.id,
        title=f"content-{key}",
        current_stage=ContentStage.OPERATION,
        status=ContentStatus.IN_PROGRESS,
    )
    session.add(content)
    await session.flush()
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        content_item_id=content.id,
        title=f"task-{key}",
        status=BrainTaskStatus.PENDING_CONFIRMATION,
        progress=90,
        current_focus="Waiting for nested publishing approval",
        runtime_mode="skill",
    )
    session.add(task)
    await session.flush()
    run.task_id = task.id
    run.status = "waiting_permission"
    run.phase = "waiting_permission"
    turn.status = "waiting_permission"
    parent = SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        task_id=task.id,
        idempotency_key=f"{key}:parent",
        skill_code="operation_iteration",
        skill_version=1,
        status="waiting_permission",
        input_snapshot={},
        output_snapshot={
            "status": "waiting_permission",
            "report": {
                "required_children_completed": False,
                "child_skill_graph": [
                    {
                        "skill_code": "publishing_preparation",
                        "status": "waiting_permission",
                    }
                ],
                "interrupt": {
                    "kind": "child_skill_paused",
                    "skill_code": "publishing_preparation",
                    "source_artifact_ids": [],
                },
            },
        },
    )
    session.add(parent)
    await session.flush()
    child = SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        task_id=task.id,
        idempotency_key=f"{key}:child",
        skill_code="publishing_preparation",
        skill_version=1,
        status="waiting_permission",
        input_snapshot={},
        output_snapshot={
            "status": "waiting_permission",
            "artifact_type": "publish_package",
            "report": {"summary": "Ready for approval"},
            "composite_parent_skill_run_id": parent.id,
        },
    )
    session.add(child)
    await session.flush()
    artifact = Deliverable(
        content_item_id=content.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        skill_run_id=child.id,
        agent_code=AgentCode.OPERATOR.value,
        type=DeliverableType.REVIEW_REPORT,
        version=1,
        status=DeliverableStatus.PENDING_REVIEW,
        payload={"summary": "Ready for approval"},
    )
    session.add(artifact)
    await session.flush()
    call = AgentToolCall(
        org_id=admin.org_id,
        task_id=task.id,
        skill_run_id=child.id,
        thread_id=thread.id,
        turn_id=turn.id,
        tool_code="nested_finish_approval",
        tool_name="Nested finish approval",
        idempotency_key=f"{key}:approval",
        side_effect_level="read",
        status="waiting_approval",
        permission_mode="confirm",
        requires_human_confirmation=True,
        meta={"approval_stage": "before_finish", "artifact_id": artifact.id},
    )
    session.add(call)
    await session.commit()
    return turn, run, task, parent, child, artifact, call


@pytest.mark.asyncio
async def test_nested_finish_rejection_commits_once_then_replays_stable_events(
    client, session, admin, monkeypatch
) -> None:
    turn, run, task, parent, child, artifact, call = await _nested_finish_approval_scope(
        session, admin, key="nested-reject-transaction"
    )
    published: list[int] = []

    async def capture_publish(_event_type, _payload, **kwargs) -> None:
        published.append(kwargs["event_id"])

    monkeypatch.setattr(
        "app.orchestrator.brain_runtime.publish_realtime_event", capture_publish
    )
    login = await client.post(
        "/auth/login",
        json={"email": "admin@test.com", "password": "admin-pw-123"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    payload = {"approved": False, "comment": "Reject the required child"}

    first = await client.post(
        f"/brain/tool-calls/{call.id}/approve", headers=headers, json=payload
    )

    assert first.status_code == 200
    await session.refresh(run)
    await session.refresh(turn)
    await session.refresh(task)
    await session.refresh(parent)
    await session.refresh(child)
    await session.refresh(artifact)
    await session.refresh(call)
    assert (run.status, turn.status, task.status) == (
        "blocked",
        "blocked",
        BrainTaskStatus.FAILED,
    )
    assert (parent.status, child.status) == ("blocked", "blocked")
    assert artifact.status is DeliverableStatus.REJECTED
    assert call.status == "failed"
    durable_runtime_events = list(
        await session.scalars(
            select(Event)
            .where(Event.turn_id == turn.id, Event.type.like("brain.runtime.%"))
            .order_by(Event.id)
        )
    )
    durable_ids = [event.id for event in durable_runtime_events]
    assert durable_ids
    published_events = list(
        await session.scalars(
            select(Event).where(Event.id.in_(published)).order_by(Event.id)
        )
    )
    pending_ids = [
        event.id for event in published_events if event.type == "pending_work.updated"
    ]
    assert pending_ids
    assert set(published) == {*durable_ids, *pending_ids}
    assert all(
        event.type.startswith("brain.runtime.")
        or event.type == "pending_work.updated"
        for event in published_events
    )
    first_publish = list(published)
    event_count = await session.scalar(
        select(func.count(Event.id)).where(Event.turn_id == turn.id)
    )

    duplicate = await client.post(
        f"/brain/tool-calls/{call.id}/approve", headers=headers, json=payload
    )
    opposite = await client.post(
        f"/brain/tool-calls/{call.id}/approve",
        headers=headers,
        json={"approved": True, "comment": "Conflicting retry"},
    )

    assert duplicate.status_code == 200
    assert opposite.status_code == 409
    # Replays preserve the durable event set; separate runtime and account
    # listeners may publish their categories in a different safe order.
    assert sorted(published) == sorted(first_publish + first_publish)
    assert await session.scalar(
        select(func.count(Event.id)).where(Event.turn_id == turn.id)
    ) == event_count


@pytest.mark.asyncio
async def test_nested_finish_rejection_rollback_publishes_nothing(
    session, admin, monkeypatch
) -> None:
    from app.services.composite_skill_runs import lock_composite_finish_approval
    from app.services.runtime_state import publish_runtime_state_intents

    turn, run, task, parent, child, artifact, call = await _nested_finish_approval_scope(
        session, admin, key="nested-reject-rollback"
    )
    published: list[int] = []

    async def capture_publish(_event_type, _payload, **kwargs) -> None:
        published.append(kwargs["event_id"])

    monkeypatch.setattr(
        "app.orchestrator.brain_runtime.publish_realtime_event", capture_publish
    )
    approval_lock = await lock_composite_finish_approval(session, tool_call=call)
    locked_call = approval_lock.tool_call
    locked_call.status = "failed"
    locked_call.meta = {
        **dict(locked_call.meta or {}),
        "decision": {"approved": False, "comment": "crash before commit"},
    }
    result = await finalize_skill_finish_approval(
        session,
        tool_call=locked_call,
        task=task,
        approved=False,
        comment="crash before commit",
        prelocked=approval_lock.runtime_lock,
    )

    assert result.handled is True
    assert result.publish_intents
    assert all(
        isinstance(intent.event_id, int)
        and isinstance(intent.event_type, str)
        and (intent.turn_id is None or isinstance(intent.turn_id, int))
        for intent in result.publish_intents
    )
    assert published == []
    await session.rollback()
    for row in (run, turn, task, parent, child, artifact, call):
        await session.refresh(row)
    assert (run.status, turn.status, task.status) == (
        "waiting_permission",
        "waiting_permission",
        BrainTaskStatus.PENDING_CONFIRMATION,
    )
    assert (parent.status, child.status) == (
        "waiting_permission",
        "waiting_permission",
    )
    assert artifact.status is DeliverableStatus.PENDING_REVIEW
    assert call.status == "waiting_approval"
    await publish_runtime_state_intents(session, result.publish_intents)
    assert published == []
    assert await session.scalar(
        select(func.count(Event.id)).where(Event.turn_id == turn.id)
    ) == 0


@pytest.mark.parametrize(
    ("approved", "expected_runtime_status", "expected_deliverable_status"),
    [
        (True, "completed", DeliverableStatus.APPROVED),
        (False, "blocked", DeliverableStatus.REJECTED),
    ],
)
@pytest.mark.asyncio
async def test_publishing_finish_approval_converges_typed_skill_without_legacy_resume(
    client,
    session,
    admin,
    approved,
    expected_runtime_status,
    expected_deliverable_status,
    monkeypatch,
) -> None:
    account, thread, turn, run = await _scope(
        session,
        admin,
        key=f"publishing-finish-approval-{approved}",
        message="为这条内容生成发布准备包",
    )
    result = await SkillRuntime(tool_executor=_Tools(), harness=_Harness()).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="publishing_preparation",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="publishing_preparation",
            structured_input={},
        ),
    )
    call = await session.scalar(
        select(AgentToolCall).where(AgentToolCall.skill_run_id == result.skill_run_id)
    )
    assert call is not None
    login = await client.post(
        "/auth/login",
        json={"email": "admin@test.com", "password": "admin-pw-123"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    import app.api.brain as brain_api

    original_scope_lock = brain_api.lock_composite_finish_approval
    approval_scope_checked = False

    async def tracked_scope_lock(*args, **kwargs):
        nonlocal approval_scope_checked
        result = await original_scope_lock(*args, **kwargs)
        approval_scope_checked = True
        return result

    def reject_prelock_autoflush(*_args, **_kwargs) -> None:
        assert approval_scope_checked, "approval rows autoflushed before scope lock"

    monkeypatch.setattr(brain_api, "lock_composite_finish_approval", tracked_scope_lock)
    event.listen(session.sync_session, "before_flush", reject_prelock_autoflush)
    api_commit_spy = AsyncMock(wraps=session.commit)
    monkeypatch.setattr(session, "commit", api_commit_spy)

    response = await client.post(
        f"/brain/tool-calls/{call.id}/approve",
        headers=headers,
        json={"approved": approved, "comment": "人工审批结论"},
    )

    assert response.status_code == 200
    assert api_commit_spy.await_count == 1
    assert approval_scope_checked
    await session.refresh(run)
    await session.refresh(turn)
    skill_run = await session.get(SkillRun, result.skill_run_id)
    deliverable = await session.get(Deliverable, result.artifact_id)
    assert skill_run is not None
    assert deliverable is not None
    assert run.status == expected_runtime_status
    assert turn.status == expected_runtime_status
    assert skill_run.status == expected_runtime_status
    assert deliverable.status is expected_deliverable_status
    terminal_events = list(
        await session.scalars(
            select(Event).where(
                Event.turn_id == turn.id,
                Event.type.in_(
                    {
                        "turn.completed",
                        "turn.failed",
                        "turn.blocked",
                        "turn.cancelled",
                        "turn.stopped",
                    }
                ),
            )
        )
    )
    assert [event.type for event in terminal_events] == [
        "turn.completed" if approved else "turn.blocked"
    ]
