from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import AgentInvocation, AgentQualityScore, BrainTask
from app.models.enums import AgentCode, AgentInvocationStatus
from app.orchestrator.ai_coo_critic import (
    CriticDisposition,
    ai_coo_critic_service,
)
from app.schemas.ai_coo import CriticEvaluation


def _evaluation(score: int) -> CriticEvaluation:
    return CriticEvaluation(
        dimensions={
            "brand_consistency": score,
            "user_value": score,
            "propagation_ability": score,
            "commercial_conversion": score,
            "factual_accuracy": score,
        },
        issues=[] if score >= 80 else ["前三秒价值表达不足"],
        suggestions=[] if score >= 80 else ["以真实用户问题开场"],
    )


async def _invocation(session, admin) -> tuple[BrainTask, AgentInvocation]:
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="提升账号获客能力",
        runtime_mode="coo_v1",
    )
    session.add(task)
    await session.flush()
    invocation = AgentInvocation(
        task_id=task.id,
        step_key="round-1:01-positioning",
        attempt=0,
        agent_code=AgentCode.POSITIONING,
        agent_name="账号定位专家",
        status=AgentInvocationStatus.DONE,
        input_summary="诊断账号定位",
        output_summary="聚焦本地高客单服务",
        model="test-model",
        token_count=0,
        cost=Decimal("0"),
        upstream=[],
    )
    session.add(invocation)
    await session.commit()
    return task, invocation


@pytest.mark.asyncio
async def test_critic_persists_passed_score_idempotently(session, admin) -> None:
    task, invocation = await _invocation(session, admin)

    first = await ai_coo_critic_service.record(
        session,
        task=task,
        invocation=invocation,
        deliverable_id=None,
        evaluation=_evaluation(86),
        iteration=0,
        evidence_refs=[],
        prompt_id="main-agent.critic",
        prompt_version="1.0.0",
        prompt_hash="a" * 64,
        critic_model="test-model",
    )
    second = await ai_coo_critic_service.record(
        session,
        task=task,
        invocation=invocation,
        deliverable_id=None,
        evaluation=_evaluation(86),
        iteration=0,
        evidence_refs=[],
        prompt_id="main-agent.critic",
        prompt_version="1.0.0",
        prompt_hash="a" * 64,
        critic_model="test-model",
    )

    assert first.disposition == CriticDisposition.PASS
    assert first.score.score == 86
    assert second.score.id == first.score.id
    rows = (
        await session.scalars(
            select(AgentQualityScore).where(AgentQualityScore.task_id == task.id)
        )
    ).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_critic_bounds_improvement_before_human_takeover(session, admin) -> None:
    task, invocation = await _invocation(session, admin)

    improve = await ai_coo_critic_service.record(
        session,
        task=task,
        invocation=invocation,
        deliverable_id=None,
        evaluation=_evaluation(62),
        iteration=1,
        evidence_refs=[],
        prompt_id="main-agent.critic",
        prompt_version="1.0.0",
        prompt_hash="b" * 64,
        critic_model="test-model",
    )
    human = await ai_coo_critic_service.record(
        session,
        task=task,
        invocation=invocation,
        deliverable_id=None,
        evaluation=_evaluation(62),
        iteration=2,
        evidence_refs=[],
        prompt_id="main-agent.critic",
        prompt_version="1.0.0",
        prompt_hash="b" * 64,
        critic_model="test-model",
    )

    assert improve.disposition == CriticDisposition.IMPROVE
    assert human.disposition == CriticDisposition.HUMAN
    assert human.score.passed is False
