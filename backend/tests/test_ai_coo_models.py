from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
    Account,
    AgentQualityScore,
    BrainTask,
    DecisionTrace,
    ExperienceMemory,
    Org,
    ReflectionRecord,
    StrategyPlan,
)
from app.models.enums import Platform
from app.schemas.ai_coo import COORuntimeState, EvidenceRef


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        yield database
    Base.metadata.drop_all(engine)


def _task_scope(session: Session) -> tuple[Org, Account, BrainTask]:
    org = Org(name="同舟行")
    account = Account(
        org=org,
        platform=Platform.DOUYIN,
        nickname="建筑膜账号",
    )
    session.add(account)
    session.flush()
    task = BrainTask(
        org_id=org.id,
        title="提升账号获客能力",
        runtime_mode="coo_v1",
    )
    session.add(task)
    session.commit()
    return org, account, task


def test_ai_coo_ledgers_persist_business_state(session: Session) -> None:
    org, account, task = _task_scope(session)
    evidence = [
        {
            "source_type": "account_metric_snapshot",
            "source_id": "42",
            "metric": "plays",
            "value": 12800,
            "time_range": {"start": "2026-07-01", "end": "2026-07-27"},
            "collected_at": "2026-07-27T08:00:00Z",
            "freshness": "fresh",
        }
    ]
    strategy = StrategyPlan(
        org_id=org.id,
        task_id=task.id,
        account_id=account.id,
        status="draft",
        version=1,
        goal="提升有效咨询量",
        situation_snapshot={"account_stage": "growth"},
        strategy={"content_ratio": {"case": 0.4, "knowledge": 0.3}},
        kpis=[{"metric": "qualified_leads", "target": 30}],
        risks=["历史咨询数据不足"],
        evidence_refs=evidence,
        rationale_summary="案例内容在当前样本中转化更稳定。",
    )
    decision = DecisionTrace(
        org_id=org.id,
        task_id=task.id,
        account_id=account.id,
        trace_key="strategy-content-ratio-v1",
        goal="提升有效咨询量",
        evidence_refs=evidence,
        alternatives=[{"key": "knowledge_first"}, {"key": "case_first"}],
        selected_option={"key": "case_first"},
        decision_reason="真实案例内容具有更高的咨询贡献。",
        action_summary="提高案例内容比例。",
    )
    reflection = ReflectionRecord(
        org_id=org.id,
        task_id=task.id,
        account_id=account.id,
        status="pending_observation",
        goal_snapshot={"metric": "qualified_leads", "target": 30},
        expected_outcome={"qualified_leads": 30},
        observed_outcome={},
        evidence_refs=[],
        diagnosis=[],
        conclusion="等待观测数据。",
        next_strategy={},
        experience_candidates=[],
    )
    quality = AgentQualityScore(
        org_id=org.id,
        task_id=task.id,
        score=85,
        dimensions={
            "brand_consistency": 88,
            "user_value": 86,
            "communication": 82,
            "conversion": 84,
            "factual_accuracy": 90,
        },
        issues=["前三秒吸引力仍可加强"],
        suggestions=["以真实施工前后对比开场"],
        passed=True,
        iteration=0,
        evidence_refs=evidence,
        critic_prompt_id="critic",
        critic_prompt_version="1.0.0",
    )
    memory = ExperienceMemory(
        org_id=org.id,
        account_id=account.id,
        task_id=task.id,
        status="candidate",
        industry="建筑服务",
        action="案例内容",
        condition="高客单价本地服务",
        result="等待真实结果验证",
        confidence=Decimal("0.50"),
        source_refs=evidence,
        verification_method="pending",
    )
    session.add_all([strategy, decision, reflection, quality, memory])
    session.commit()

    loaded = session.scalar(select(StrategyPlan).where(StrategyPlan.task_id == task.id))
    assert loaded is not None
    assert loaded.evidence_refs[0]["metric"] == "plays"
    assert task.strategy_plans[0].goal == "提升有效咨询量"
    assert task.decision_traces[0].selected_option["key"] == "case_first"
    assert task.reflection_records[0].status == "pending_observation"
    assert task.quality_scores[0].score == 85
    assert task.experience_memories[0].status == "candidate"


def test_evidence_ref_requires_traceable_source() -> None:
    evidence = EvidenceRef(
        source_type="platform_content_record",
        source_id="content:87",
        metric="completion_rate",
        value=0.31,
        time_range={"start": "2026-07-01", "end": "2026-07-27"},
        collected_at=datetime(2026, 7, 27, 8, tzinfo=UTC),
        freshness="fresh",
    )
    assert evidence.source_id == "content:87"

    with pytest.raises(ValidationError):
        EvidenceRef(
            source_type="model_guess",
            source_id="unknown",
            metric="completion_rate",
            value=0.31,
            time_range={},
            collected_at=datetime.now(UTC),
            freshness="unknown",
        )


def test_coo_runtime_state_keeps_scope_and_budget_explicit() -> None:
    state = COORuntimeState(
        task_id=7,
        run_id=9,
        thread_id="brain-task-7",
        org_id=1,
        available_client_ids=[2],
        available_project_ids=[3],
        active_client_id=2,
        active_project_id=3,
        account_id=4,
        user_goal="提升获客",
        normalized_goal={"objective": "increase_qualified_leads"},
        phase="situation_awareness",
        iteration=0,
        retry_budget=2,
        token_count=120,
        cost_usd=Decimal("0.0123"),
        status="running",
    )
    assert state.retry_budget == 2
    assert state.evidence_refs == []
    assert state.errors == []

    with pytest.raises(ValidationError):
        COORuntimeState(
            task_id=7,
            thread_id="brain-task-7",
            org_id=1,
            user_goal="提升获客",
            phase="critic_review",
            iteration=3,
            retry_budget=-1,
            status="running",
        )
