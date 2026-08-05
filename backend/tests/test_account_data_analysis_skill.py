"""Contract tests for evidence-grounded account data analysis."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

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
    BrainTaskStatus,
    Platform,
    UserRole,
)
from app.orchestrator.agent_harness import AgentHarnessError
from app.orchestrator.skill_runtime import (
    SkillRuntime,
    validate_account_analysis_grounding,
)
from app.orchestrator.skills.account_data_analysis import (
    AccountDataAnalysisAnswer,
    AccountDataAnalysisCriticOutcome,
)
from app.orchestrator.tool_executor import DurableToolExecutor
from app.schemas.capability_request import CapabilityRequest
from app.tools import ToolAdapter, ToolExecutionContext, ToolSpec


async def _conversation_scope(session, admin, *, key: str):
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname=f"analysis-{key}",
        status=AccountStatus.ACTIVE,
        auth={"auth_status": "authorized", "data_sync_status": "ready"},
    )
    session.add(account)
    await session.flush()
    thread = ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title=f"thread-{key}",
    )
    session.add(thread)
    await session.flush()
    turn = ConversationTurn(
        thread_id=thread.id,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id=key,
        user_input="最近30天播放量为什么下降？",
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
        request_payload={"message": turn.user_input},
    )
    session.add(run)
    await session.commit()
    request = CapabilityRequest(
        org_id=admin.org_id,
        user_id=admin.id,
        account_id=account.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        message=turn.user_input,
        structured_input={
            "question": turn.user_input,
            "days": 30,
            "comparison": "auto",
            "requested_metrics": ["play"],
            "top_n": 5,
        },
    )
    return account, thread, turn, run, request


def _tool_result(*, sufficient: bool = True) -> dict:
    answerability = {
        "status": "sufficient" if sufficient else "insufficient",
        "confidence": 0.9 if sufficient else 0,
        "supported_claims": ["play:current", "play:trend"] if sufficient else [],
        "unsupported_claims": [] if sufficient else ["play:current"],
        "missing_metrics": [] if sufficient else ["play"],
        "missing_periods": [],
        "reasons": [] if sufficient else ["没有已确认的播放量数据"],
    }
    facts = (
        [
            {
                "metric_code": "play",
                "label": "播放量",
                "unit": "count",
                "current_value": 700,
                "previous_value": 1000,
                "absolute_change": -300,
                "relative_change": -0.3,
                "direction": "down",
                "current_period": {
                    "days": 30,
                    "start": "2026-07-07",
                    "end": "2026-08-05",
                },
                "comparison_period": {
                    "days": 30,
                    "start": "2026-06-07",
                    "end": "2026-07-06",
                },
                "sample_count": 30,
                "evidence_hashes": ["a" * 64],
            }
        ]
        if sufficient
        else []
    )
    evidence = (
        [
            {
                "source_type": "platform_export",
                "source_id": "account_metric_snapshot:7",
                "account_id": 1,
                "batch_id": 7,
                "metric_code": "play",
                "period_start": "2026-08-05",
                "period_end": "2026-08-05",
                "observed_at": "2026-08-05",
                "value": 700,
                "unit": "count",
                "content_hash": "a" * 64,
            }
        ]
        if sufficient
        else []
    )
    return {
        "account_id": 1,
        "query_window": {
            "days": 30,
            "start": "2026-07-07",
            "end": "2026-08-05",
        },
        "comparison_window": {
            "days": 30,
            "start": "2026-06-07",
            "end": "2026-07-06",
        },
        "answerability": answerability,
        "facts": facts,
        "content_rankings": [],
        "data_quality": {
            "latest_observed_at": "2026-08-05" if sufficient else None,
            "days_since_observed": 0 if sufficient else None,
            "conflict_count": 0,
            "current_sample_count": 30 if sufficient else 0,
            "comparison_sample_count": 30 if sufficient else 0,
        },
        "evidence_refs": evidence,
    }


class _FakeTools:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[str] = []
        calls = self.calls
        tool_result = self.result

        class Params(BaseModel):
            model_config = ConfigDict(extra="forbid")

            days: int
            comparison: str
            metric_codes: list[str]
            top_n: int

        async def analyze(_params: Params, context: ToolExecutionContext) -> dict:
            calls.append("account.metrics_analysis")
            return {
                **tool_result,
                "account_id": context.account_id,
                "evidence_refs": [
                    {**item, "account_id": context.account_id}
                    for item in tool_result["evidence_refs"]
                ],
            }

        self.executor = DurableToolExecutor(
            ToolAdapter(
                [
                    ToolSpec(
                        name="account.metrics_analysis",
                        handler=analyze,
                        params_model=Params,
                        side_effect_level="read",
                        allowed_roles=frozenset({UserRole.ADMIN, UserRole.USER}),
                    )
                ]
            )
        )

    async def execute(self, **kwargs):
        return await self.executor.execute(**kwargs)


class _FakeHarness:
    def __init__(self) -> None:
        self.calls: list[AgentCode] = []

    async def execute(self, *args, **kwargs):
        self.calls.append(kwargs["code"])
        session = args[0]
        scope = kwargs["scope"]
        invocation = AgentInvocation(
            task_id=kwargs["task"].id,
            run_id=scope.run_id,
            skill_run_id=scope.skill_run_id,
            thread_id=scope.thread_id,
            turn_id=scope.turn_id,
            step_key=kwargs["step_key"],
            attempt=kwargs["attempt"],
            agent_code=kwargs["code"],
            agent_name=kwargs["code"].value,
            status=AgentInvocationStatus.DONE,
            output_summary="播放量较上一周期下降，建议先验证开头承接。",
            upstream=[{"trace_only_output": {"summary": "analysis"}}],
        )
        session.add(invocation)
        await session.commit()
        await session.refresh(invocation)
        return SimpleNamespace(
            invocation=invocation,
            deliverable=None,
            output={
                "conclusion": "播放量较上一周期下降。",
                "interpretation": ["播放量从1000下降到700，降幅30%。"],
                "recommendations": [
                    {
                        "action": "连续测试三组不同开头",
                        "rationale": "先验证开头承接是否与播放下滑相关",
                        "validation_metric": "play",
                        "observation_days": 7,
                    }
                ],
                "data_limits": ["现有数据只能说明相关变化，不能证明因果"],
                "next_action": "执行7天开头测试并复查播放量",
            },
        )


class _PassingCritic:
    def __init__(self, outcomes: list[bool] | None = None) -> None:
        self.calls = 0
        self.outcomes = list(outcomes or [True])

    async def review(self, **kwargs):
        passed = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        return SimpleNamespace(
            passed=passed,
            score=94 if passed else 62,
            issues=[] if passed else ["建议不够具体"],
            suggestions=[] if passed else ["补充可验证动作"],
        )


class _FailingHarness(_FakeHarness):
    async def execute(self, *args, **kwargs):
        self.calls.append(kwargs["code"])
        raise AgentHarnessError("operator unavailable")


@pytest.mark.asyncio
async def test_insufficient_data_finishes_without_invoking_expert(session, admin) -> None:
    _account, thread, turn, run, request = await _conversation_scope(
        session,
        admin,
        key="analysis-insufficient",
    )
    tools = _FakeTools(_tool_result(sufficient=False))
    harness = _FakeHarness()

    result = await SkillRuntime(
        tool_executor=tools,
        harness=harness,
        critic=_PassingCritic(),
    ).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="account_data_analysis",
        capability_request=request,
    )

    assert result.status == "completed"
    assert tools.calls == ["account.metrics_analysis"]
    assert harness.calls == []
    assert result.report["answerability"]["status"] == "insufficient"
    assert result.report["recommendations"] == []
    assert result.report["participating_experts"] == []
    assert len(list(await session.scalars(select(Deliverable)))) == 1


@pytest.mark.asyncio
async def test_grounded_analysis_uses_operator_once_and_preserves_tool_facts(
    session,
    admin,
) -> None:
    account, thread, turn, run, request = await _conversation_scope(
        session,
        admin,
        key="analysis-grounded",
    )
    tool_result = _tool_result()
    tool_result["account_id"] = account.id
    tool_result["evidence_refs"] = [
        {**item, "account_id": account.id} for item in tool_result["evidence_refs"]
    ]
    tools = _FakeTools(tool_result)
    harness = _FakeHarness()
    critic = _PassingCritic()

    result = await SkillRuntime(
        tool_executor=tools,
        harness=harness,
        critic=critic,
    ).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="account_data_analysis",
        capability_request=request,
    )

    assert result.status == "completed"
    assert harness.calls == [AgentCode.OPERATOR]
    assert critic.calls == 1
    assert result.report["key_facts"] == tool_result["facts"]
    assert result.report["evidence_refs"] == tool_result["evidence_refs"]
    assert result.report["participating_experts"] == ["06-operator"]


@pytest.mark.asyncio
async def test_critic_allows_only_one_operator_redo(session, admin) -> None:
    account, thread, turn, run, request = await _conversation_scope(
        session,
        admin,
        key="analysis-critic-redo",
    )
    tool_result = _tool_result()
    tool_result["account_id"] = account.id
    tool_result["evidence_refs"] = [
        {**item, "account_id": account.id} for item in tool_result["evidence_refs"]
    ]
    harness = _FakeHarness()
    critic = _PassingCritic([False, True])

    result = await SkillRuntime(
        tool_executor=_FakeTools(tool_result),
        harness=harness,
        critic=critic,
    ).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="account_data_analysis",
        capability_request=request,
    )

    assert harness.calls == [AgentCode.OPERATOR, AgentCode.OPERATOR]
    assert critic.calls == 2
    assert result.report["critic"]["passed"] is True
    assert result.report["critic"]["iterations"] == 2


@pytest.mark.asyncio
async def test_expert_failure_returns_fact_only_answer_and_closes_terminal_state(
    session,
    admin,
) -> None:
    account, thread, turn, run, request = await _conversation_scope(
        session,
        admin,
        key="analysis-expert-failure",
    )
    tool_result = _tool_result()
    tool_result["account_id"] = account.id
    tool_result["evidence_refs"] = [
        {**item, "account_id": account.id} for item in tool_result["evidence_refs"]
    ]
    harness = _FailingHarness()

    result = await SkillRuntime(
        tool_executor=_FakeTools(tool_result),
        harness=harness,
        critic=_PassingCritic(),
    ).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="account_data_analysis",
        capability_request=request,
    )

    assert result.status == "completed"
    assert result.report["key_facts"] == tool_result["facts"]
    assert result.report["recommendations"] == []
    assert result.report["participating_experts"] == []
    skill_run = await session.get(SkillRun, result.skill_run_id)
    task = await session.get(BrainTask, result.task_id)
    await session.refresh(run)
    await session.refresh(turn)
    assert skill_run is not None and skill_run.status == "completed"
    assert task is not None and task.status is BrainTaskStatus.COMPLETED
    assert task.progress == 100
    assert run.status == "completed"
    assert turn.status == "completed"


def _answer(tool_result: dict) -> AccountDataAnalysisAnswer:
    return AccountDataAnalysisAnswer(
        account_id=tool_result["account_id"],
        question="最近30天播放量为什么下降？",
        answerability=tool_result["answerability"],
        conclusion="播放量较上一周期下降。",
        key_facts=tool_result["facts"],
        interpretation=["播放量从1000下降到700，降幅30%。"],
        recommendations=[],
        data_limits=["当前证据不能证明因果"],
        next_action="继续观察播放量",
        evidence_refs=tool_result["evidence_refs"],
        participating_experts=["06-operator"],
        critic=AccountDataAnalysisCriticOutcome(
            passed=True,
            score=90,
            iterations=1,
        ),
    )


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("interpretation", ["播放量从1000上升到1700。"], "numeric claim"),
        ("interpretation", ["播放量上升30%。"], "direction claim"),
        ("interpretation", ["低完播率导致播放下降。"], "causal claim"),
    ],
)
def test_grounding_rejects_modified_or_unsupported_interpretation(
    field: str,
    value: list[str],
    error: str,
) -> None:
    tool_result = _tool_result()
    answer = _answer(tool_result).model_copy(update={field: value})

    with pytest.raises(ValueError, match=error):
        validate_account_analysis_grounding(answer, tool_result)


def test_grounding_rejects_invented_evidence() -> None:
    tool_result = _tool_result()
    invented = {
        **tool_result["evidence_refs"][0],
        "source_id": "account_metric_snapshot:999",
        "content_hash": "b" * 64,
    }
    payload = _answer(tool_result).model_dump(mode="json")
    payload["evidence_refs"] = [invented]
    answer = AccountDataAnalysisAnswer.model_validate(payload)

    with pytest.raises(ValueError, match="evidence refs"):
        validate_account_analysis_grounding(answer, tool_result)
