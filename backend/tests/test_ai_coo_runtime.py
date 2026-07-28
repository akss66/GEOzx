from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.models import (
    Account,
    BrainTask,
    DecisionTrace,
    StrategyPlan,
    TaskBrief,
)
from app.models.enums import AgentCode, Platform
from app.orchestrator.ai_coo_runtime import (
    ai_coo_operating_service,
    validate_strategy_draft_evidence,
)
from app.orchestrator.brain_intelligence import (
    OperatingStrategyModelPlan,
    brain_intelligence,
)
from app.orchestrator.brain_runtime import BrainRuntimeGraph
from app.prompts import prompt_registry
from app.schemas.ai_coo import AccountSituationOut, OperatingStrategyDraft
from app.schemas.brain import IntentDecision, route_decision_from_legacy_intent
from app.schemas.conversation import TurnExecutionMode, TurnRouteDecision


async def _task_with_account(session, admin) -> tuple[Account, BrainTask]:
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="真实运营账号",
    )
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="诊断账号定位并给出下一步",
        runtime_mode="coo_v1",
        thread_id="brain-task-coo-runtime",
    )
    task.brief = TaskBrief(
        goal="诊断这个抖音账号的定位问题",
        platforms=[Platform.DOUYIN.value],
        account_ids=[],
        cycle="current",
        content_goal="账号定位诊断",
        risk_constraints=[],
        expected_outputs=["账号定位诊断"],
        confirmation_actions=[],
    )
    session.add_all([account, task])
    await session.flush()
    task.brief.account_ids = [account.id]
    await session.commit()
    return account, task


@pytest.mark.asyncio
async def test_operating_context_uses_data_collection_strategy_when_evidence_is_missing(
    session,
    admin,
) -> None:
    account, task = await _task_with_account(session, admin)

    result = await ai_coo_operating_service.prepare(
        session,
        task=task,
        run_id=None,
        required_expert_codes=[AgentCode.POSITIONING.value],
    )

    assert result.account_id == account.id
    assert result.situation_summary["data_sufficiency"] == "insufficient"
    assert result.situation_summary["diagnosis"] == []
    assert result.evidence_refs == []
    assert result.memory_context.account.account_id == account.id
    assert result.memory_context.account.situation_summary["data_sufficiency"] == (
        "insufficient"
    )
    assert result.strategy_status == "data_collection_required"
    assert result.task_plan == [
        {
            "order": 1,
            "agent_code": AgentCode.POSITIONING.value,
            "purpose": "账号定位诊断",
            "status": "planned",
        }
    ]

    strategy = await session.scalar(
        select(StrategyPlan).where(StrategyPlan.task_id == task.id)
    )
    assert strategy is not None
    assert strategy.strategy["mode"] == "evidence_first"
    assert strategy.situation_snapshot["diagnosis"] == []
    assert strategy.evidence_refs == []

    trace = await session.scalar(
        select(DecisionTrace).where(DecisionTrace.task_id == task.id)
    )
    assert trace is not None
    assert trace.selected_option["key"] == "collect_baseline"
    assert trace.evidence_refs == []


@pytest.mark.asyncio
async def test_operating_context_plans_only_the_required_experts(
    session,
    admin,
) -> None:
    _account, task = await _task_with_account(session, admin)

    result = await ai_coo_operating_service.prepare(
        session,
        task=task,
        run_id=None,
        required_expert_codes=[
            AgentCode.POSITIONING.value,
            AgentCode.CONTENT_DIRECTOR.value,
        ],
    )

    assert [step["agent_code"] for step in result.task_plan] == [
        AgentCode.POSITIONING.value,
        AgentCode.CONTENT_DIRECTOR.value,
    ]
    assert AgentCode.ADVERTISER.value not in {
        step["agent_code"] for step in result.task_plan
    }


@pytest.mark.asyncio
async def test_operating_context_is_idempotent_for_one_task(
    session,
    admin,
) -> None:
    _account, task = await _task_with_account(session, admin)
    required = [AgentCode.POSITIONING.value]

    first = await ai_coo_operating_service.prepare(
        session,
        task=task,
        run_id=None,
        required_expert_codes=required,
    )
    second = await ai_coo_operating_service.prepare(
        session,
        task=task,
        run_id=None,
        required_expert_codes=required,
    )

    assert second.strategy_plan_id == first.strategy_plan_id
    assert second.decision_trace_id == first.decision_trace_id
    assert (
        await session.scalar(
            select(func.count(StrategyPlan.id)).where(StrategyPlan.task_id == task.id)
        )
        == 1
    )
    assert (
        await session.scalar(
            select(func.count(DecisionTrace.id)).where(DecisionTrace.task_id == task.id)
        )
        == 1
    )


@pytest.mark.asyncio
async def test_operating_context_persists_evidence_grounded_model_strategy(
    session,
    admin,
    monkeypatch,
) -> None:
    account, task = await _task_with_account(session, admin)
    evidence_id = "account_metric_snapshot:41:engagement_rate"
    captured_memory = {}

    async def fake_situation(*_args, **_kwargs):
        return AccountSituationOut.model_validate(
            {
                "account_id": account.id,
                "generated_at": datetime.now(UTC),
                "data_sufficiency": "partial",
                "conclusion": "已建立部分真实数据基线",
                "diagnosis": [],
                "evidence_refs": [
                    {
                        "source_type": "account_metric_snapshot",
                        "source_id": "41",
                        "metric": "engagement_rate",
                        "value": 0.012,
                        "time_range": {
                            "start": "2026-07-20",
                            "end": "2026-07-20",
                        },
                        "collected_at": datetime.now(UTC),
                        "freshness": "fresh",
                    }
                ],
                "missing_data": ["有效咨询量"],
                "confidence": 0.45,
            }
        )

    async def fake_strategy(*_args, **kwargs):
        captured_memory.update(kwargs["memory_context"])
        prompt = prompt_registry.load("main-agent.strategy-planning")
        return OperatingStrategyModelPlan(
            draft=OperatingStrategyDraft.model_validate(
                {
                "account_stage": "growth",
                "main_problem": "互动基础存在，但缺少转化基线",
                "data_sufficiency": "partial",
                "missing_data": ["有效咨询量"],
                "confidence": 0.7,
                "diagnosis": [
                    {
                        "issue": "已有互动率基线，但无法判断咨询转化",
                        "evidence_ids": [evidence_id],
                    }
                ],
                "strategy": {
                    "period_days": 30,
                    "primary_action": "提高真实案例内容占比",
                    "content_mix": {"真实案例": 60, "专业知识": 40},
                    "stage_goals": ["建立咨询转化基线"],
                    "content_direction": ["真实案例"],
                    "user_strategy": ["高意向用户"],
                    "conversion_path": ["内容", "主页", "咨询"],
                },
                "kpis": [
                    {
                        "metric": "互动率",
                        "target": "不低于当前基线",
                        "evidence_ids": [evidence_id],
                    }
                ],
                "risks": ["咨询数据仍需人工录入"],
                "rationale_summary": "先保持互动基线并补齐咨询数据。",
                "required_expert_codes": [
                    AgentCode.POSITIONING.value,
                    AgentCode.OPERATOR.value,
                ],
                }
            ),
            prompt=prompt,
            model="deepseek-chat",
        )

    monkeypatch.setattr(ai_coo_operating_service, "_situation", fake_situation)
    monkeypatch.setattr(
        brain_intelligence,
        "plan_operating_strategy",
        fake_strategy,
        raising=False,
    )

    result = await ai_coo_operating_service.prepare(
        session,
        task=task,
        run_id=None,
        required_expert_codes=[AgentCode.POSITIONING.value],
    )

    strategy = await session.get(StrategyPlan, result.strategy_plan_id)
    assert strategy is not None
    assert strategy.strategy["stage_goals"] == ["建立咨询转化基线"]
    assert strategy.strategy["primary_action"] == "提高真实案例内容占比"
    assert strategy.kpis[0]["metric"] == "互动率"
    assert strategy.situation_snapshot["account_stage"] == "growth"
    assert strategy.prompt_id == "main-agent.strategy-planning"
    assert strategy.prompt_version == "1.0.0"
    assert strategy.prompt_hash
    assert captured_memory["account"]["account_id"] == account.id
    assert captured_memory["experience"]["items"] == []
    assert [step["agent_code"] for step in result.task_plan] == [
        AgentCode.POSITIONING.value,
        AgentCode.OPERATOR.value,
    ]


def test_smart_runtime_enters_ai_coo_operating_nodes_before_dynamic_dispatch() -> None:
    graph = BrainRuntimeGraph()._smart_graph.get_graph()
    nodes = set(graph.nodes)
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert {
        "goal_understanding",
        "context_resolution",
        "situation_awareness",
        "strategy_planning",
        "task_planning",
    }.issubset(nodes)
    assert ("__start__", "goal_understanding") in edges
    assert ("goal_understanding", "context_resolution") in edges
    assert ("context_resolution", "situation_awareness") in edges
    assert ("situation_awareness", "strategy_planning") in edges
    assert ("strategy_planning", "task_planning") in edges
    assert ("task_planning", "decide_next") in edges
    assert ("smart_summarize", "wait_for_measurement") in edges


@pytest.mark.asyncio
async def test_diagnostic_skill_route_bypasses_strategy_and_selects_positioning_once(
    session,
    admin,
    monkeypatch,
) -> None:
    _account, task = await _task_with_account(session, admin)
    runtime = BrainRuntimeGraph()
    captured_states: list[dict] = []

    async def capture_diagnostic(state, *, config):
        captured_states.append(state)

    async def ignore_runtime_output(*_args, **_kwargs):
        return None

    monkeypatch.setattr(runtime._diagnostic_graph, "ainvoke", capture_diagnostic)
    monkeypatch.setattr(runtime, "_record_event", ignore_runtime_output)
    monkeypatch.setattr(runtime, "_stream_main_agent_turn", ignore_runtime_output)
    route = TurnRouteDecision(
        mode=TurnExecutionMode.SKILL,
        intent="account_positioning_diagnosis",
        confidence=1,
        reason="diagnosis only",
        skill_code="account_positioning_diagnosis",
        requires_account_context=True,
        requires_operation_task=True,
    )

    await runtime.start_routed(
        session,
        task,
        route_decision=route,
    )

    graph = runtime._diagnostic_graph.get_graph()
    nodes = set(graph.nodes)
    assert {
        "context_resolution",
        "dispatch_round",
        "observe_round",
        "critic_review",
        "smart_summarize",
    }.issubset(nodes)
    assert {
        "situation_awareness",
        "strategy_planning",
        "task_planning",
    }.isdisjoint(nodes)
    assert len(captured_states) == 1
    assert captured_states[0]["selected_experts"] == [AgentCode.POSITIONING.value]


def test_query_route_has_a_deterministic_tool_data_card_without_strategy_nodes() -> None:
    graph = BrainRuntimeGraph()._query_graph.get_graph()
    nodes = set(graph.nodes)

    assert {"context_resolution", "query_data_card"}.issubset(nodes)
    assert {
        "situation_awareness",
        "strategy_planning",
        "task_planning",
    }.isdisjoint(nodes)


async def _run_diagnostic_critic_routes(
    monkeypatch,
    critic_routes: list[str],
) -> list[str]:
    runtime = BrainRuntimeGraph()
    calls: list[str] = []
    remaining_routes = iter(critic_routes)

    async def context_resolution(state):
        calls.append("context")
        return {**state, "status": "context_resolved"}

    async def dispatch_round(state):
        calls.append("dispatch")
        return {**state, "status": "round_dispatched"}

    async def observe_round(state):
        calls.append("observe")
        return {**state, "status": "round_observed"}

    async def critic_review(state):
        route = next(remaining_routes)
        calls.append(f"critic:{route}")
        return {
            **state,
            "status": f"critic_{route}",
            "critic_route": route,
            "critic_iteration": int(state.get("critic_iteration", 0))
            + (1 if route == "improve" else 0),
        }

    async def smart_summarize(state):
        calls.append("summary")
        return {**state, "status": "completed"}

    monkeypatch.setattr(runtime, "_context_resolution", context_resolution)
    monkeypatch.setattr(runtime, "_dispatch_round", dispatch_round)
    monkeypatch.setattr(runtime, "_observe_round", observe_round)
    monkeypatch.setattr(runtime, "_critic_review", critic_review)
    monkeypatch.setattr(runtime, "_smart_summarize", smart_summarize)
    runtime._compile_graphs(None)

    await runtime._diagnostic_graph.ainvoke(
        {
            "task_id": 1,
            "thread_id": "diagnostic-critic-routing",
            "critic_iteration": 0,
        }
    )
    return calls


@pytest.mark.asyncio
async def test_diagnostic_critic_pass_emits_formal_summary(monkeypatch) -> None:
    calls = await _run_diagnostic_critic_routes(monkeypatch, ["pass"])

    assert calls == ["context", "dispatch", "observe", "critic:pass", "summary"]


@pytest.mark.asyncio
async def test_diagnostic_critic_improve_reruns_before_formal_summary(monkeypatch) -> None:
    calls = await _run_diagnostic_critic_routes(
        monkeypatch,
        ["improve", "pass"],
    )

    assert calls == [
        "context",
        "dispatch",
        "observe",
        "critic:improve",
        "dispatch",
        "observe",
        "critic:pass",
        "summary",
    ]


@pytest.mark.asyncio
async def test_diagnostic_critic_human_never_emits_formal_summary(monkeypatch) -> None:
    calls = await _run_diagnostic_critic_routes(monkeypatch, ["human"])

    assert calls == ["context", "dispatch", "observe", "critic:human"]


@pytest.mark.asyncio
async def test_diagnostic_critic_rework_is_bounded_before_human_handoff(
    monkeypatch,
) -> None:
    calls = await _run_diagnostic_critic_routes(
        monkeypatch,
        ["improve", "improve", "human"],
    )

    assert calls.count("dispatch") == 3
    assert calls[-1] == "critic:human"
    assert "summary" not in calls


def test_legacy_analysis_with_positioning_hint_routes_to_diagnostic_skill() -> None:
    route = route_decision_from_legacy_intent(
        IntentDecision(
            intent="analysis",
            confidence=0.98,
            reason="account positioning diagnosis",
            suggested_expert_codes=[AgentCode.POSITIONING],
            requires_account_context=True,
        ),
        has_account=True,
    )

    assert route.mode is TurnExecutionMode.SKILL
    assert route.skill_code == "account_positioning_diagnosis"
    assert route.requires_account_context is True
    assert route.requires_operation_task is True


def test_legacy_plain_analysis_is_query_and_workflow_remains_task() -> None:
    query_route = route_decision_from_legacy_intent(
        IntentDecision(
            intent="analysis",
            confidence=0.9,
            reason="read account data",
            requires_account_context=True,
        ),
        has_account=True,
    )
    task_route = route_decision_from_legacy_intent(
        IntentDecision(
            intent="workflow",
            confidence=0.9,
            reason="build a full operating plan",
            requires_account_context=True,
        ),
        has_account=True,
    )

    assert query_route.mode is TurnExecutionMode.QUERY
    assert query_route.requires_operation_task is False
    assert task_route.mode is TurnExecutionMode.TASK
    assert task_route.requires_operation_task is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        TurnExecutionMode.TASK,
        TurnExecutionMode.ACTION,
    ],
)
async def test_task_and_action_routes_keep_the_full_strategy_graph(
    session,
    admin,
    monkeypatch,
    mode,
) -> None:
    _account, task = await _task_with_account(session, admin)
    runtime = BrainRuntimeGraph()
    captured_states: list[dict] = []

    async def capture_full_graph(state, *, config):
        captured_states.append(state)

    async def ignore_runtime_output(*_args, **_kwargs):
        return None

    monkeypatch.setattr(runtime._smart_graph, "ainvoke", capture_full_graph)
    monkeypatch.setattr(runtime, "_record_event", ignore_runtime_output)
    monkeypatch.setattr(runtime, "_stream_main_agent_turn", ignore_runtime_output)
    route = TurnRouteDecision(
        mode=mode,
        intent=f"{mode.value}_request",
        confidence=1,
        reason="full operating workflow required",
        requires_account_context=True,
        requires_operation_task=True,
    )

    await runtime.start_routed(
        session,
        task,
        route_decision=route,
    )

    assert len(captured_states) == 1
    graph_nodes = runtime._smart_graph.get_graph().nodes
    assert "strategy_planning" in graph_nodes
    if mode is TurnExecutionMode.ACTION:
        assert {
            "collect_permissions",
            "smart_permission_gate",
            "execute_tools",
        }.issubset(graph_nodes)


def test_observation_runtime_uses_real_learning_nodes() -> None:
    graph = BrainRuntimeGraph()._observation_graph.get_graph()
    nodes = set(graph.nodes)
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert {
        "performance_analysis",
        "reflection",
        "experience_verification",
        "next_strategy",
    }.issubset(nodes)
    assert ("__start__", "performance_analysis") in edges
    assert ("reflection", "experience_verification") in edges
    assert ("experience_verification", "next_strategy") in edges


def test_strategy_draft_cannot_reference_evidence_outside_runtime_context() -> None:
    draft = OperatingStrategyDraft.model_validate(
        {
            "account_stage": "growth",
            "main_problem": "内容转化承接不足",
            "data_sufficiency": "partial",
            "missing_data": ["有效咨询量"],
            "confidence": 0.7,
            "diagnosis": [
                {
                    "issue": "近期内容数量充足但缺少转化证据",
                    "evidence_ids": ["platform_content_record:12:content_record_count"],
                }
            ],
            "strategy": {
                "period_days": 30,
                "primary_action": "建立真实转化基线",
                "content_mix": {"真实案例": 100},
                "stage_goals": ["建立转化基线"],
                "content_direction": ["真实案例"],
                "user_strategy": ["高意向用户"],
                "conversion_path": ["内容", "主页", "咨询"],
            },
            "kpis": [
                {
                    "metric": "有效咨询量",
                    "target": "建立基线",
                    "evidence_ids": ["invented:999:qualified_leads"],
                }
            ],
            "risks": ["缺少转化数据"],
            "rationale_summary": "先建立真实转化基线。",
            "required_expert_codes": [AgentCode.POSITIONING.value],
        }
    )

    with pytest.raises(ValueError, match="unknown evidence ids"):
        validate_strategy_draft_evidence(
            draft,
            {
                "platform_content_record:12:content_record_count",
            },
        )
