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
from app.orchestrator.ai_coo_runtime import ai_coo_operating_service
from app.orchestrator.brain_runtime import BrainRuntimeGraph


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


def test_smart_runtime_enters_ai_coo_operating_nodes_before_dynamic_dispatch() -> None:
    graph = BrainRuntimeGraph()._smart_graph.get_graph()
    nodes = set(graph.nodes)
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert {
        "goal_understanding",
        "situation_awareness",
        "strategy_planning",
        "task_planning",
    }.issubset(nodes)
    assert ("__start__", "goal_understanding") in edges
    assert ("goal_understanding", "situation_awareness") in edges
    assert ("situation_awareness", "strategy_planning") in edges
    assert ("strategy_planning", "task_planning") in edges
    assert ("task_planning", "decide_next") in edges
