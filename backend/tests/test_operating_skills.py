"""Execution contracts for the first account-operations Skill loop."""

import asyncio
from time import monotonic
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from app.models import (
    Account,
    AgentInvocation,
    AgentRun,
    AgentToolCall,
    BrainTask,
    ConversationThread,
    ConversationTurn,
    Deliverable,
    SkillRun,
)
from app.models.enums import (
    AccountStatus,
    AgentCode,
    AgentInvocationStatus,
    DeliverableStatus,
    Platform,
    UserRole,
)
from app.orchestrator.skill_runtime import (
    SkillRecoveryConflict,
    SkillRuntime,
    run_bounded_stage,
)
from app.orchestrator.tool_executor import DurableToolExecutor
from app.schemas.capability_request import CapabilityRequest
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
                "sources": [{"batch_id": 7}],
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
            )
        )

    async def execute(self, **kwargs):
        return await self.executor.execute(**kwargs)


class _Harness:
    def __init__(self) -> None:
        self.calls: list[AgentCode] = []

    async def execute(self, *args, **kwargs):
        session = args[0]
        code = kwargs["code"]
        self.calls.append(code)
        output = (
            {
                "title": "玻璃贴膜避坑指南",
                "hook": "贴膜前先看这三个坑。",
                "scenes": ["常见误区", "真实案例", "选择建议"],
                "duration_seconds": 60,
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

    response = await client.post(
        f"/brain/tool-calls/{call.id}/approve",
        headers=headers,
        json={"approved": approved, "comment": "人工审批结论"},
    )

    assert response.status_code == 200
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
