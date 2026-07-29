"""Execution contracts for the first account-operations Skill loop."""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from app.models import (
    Account,
    AgentInvocation,
    AgentRun,
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
    Platform,
    UserRole,
)
from app.orchestrator.skill_runtime import SkillRuntime
from app.orchestrator.tool_executor import DurableToolExecutor
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


class _Tools:
    def __init__(self) -> None:
        class EmptyParams(BaseModel):
            model_config = ConfigDict(extra="forbid")

        class DaysParams(BaseModel):
            model_config = ConfigDict(extra="forbid")

            days: int = 30

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

        self.executor = DurableToolExecutor(
            ToolAdapter(
                [
                    ToolSpec(
                        name="account.profile",
                        handler=profile,
                        params_model=EmptyParams,
                        allowed_roles=frozenset({UserRole.ADMIN, UserRole.USER}),
                    ),
                    ToolSpec(
                        name="account.data_context",
                        handler=data_context,
                        params_model=DaysParams,
                        allowed_roles=frozenset({UserRole.ADMIN, UserRole.USER}),
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


@pytest.mark.parametrize(
    ("skill_code", "message", "artifact_type", "expert_codes"),
    [
        (
            "topic_planning",
            "给我策划未来一周的五个选题",
            "topic_plan",
            [AgentCode.CONTENT_DIRECTOR],
        ),
        (
            "script_generation",
            "写一个玻璃贴膜避坑短视频脚本",
            "video_script",
            [AgentCode.CONTENT_DIRECTOR],
        ),
        (
            "publishing_preparation",
            "给这个内容生成发布前检查清单",
            "publish_calendar",
            [AgentCode.OPERATOR],
        ),
        (
            "performance_review",
            "复盘最近30天的账号表现",
            "review_report",
            [AgentCode.OPERATOR, AgentCode.CONTENT_DIRECTOR],
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

    assert result.status == "completed"
    assert result.artifact_type == artifact_type
    assert result.artifact_id is not None
    assert result.report["account_id"] == account.id
    assert result.report["participating_experts"] == [
        code.value for code in expert_codes
    ]
    assert harness.calls == expert_codes
    assert await session.scalar(select(func.count(BrainTask.id))) == 1
    assert await session.scalar(select(func.count(SkillRun.id))) == 1
    assert await session.scalar(select(func.count(Deliverable.id))) == 1
