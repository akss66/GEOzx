from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.models import (
    Account,
    AccountMetricSnapshot,
    AgentQualityScore,
    BrainTask,
    DataImportBatch,
    ExperienceMemory,
    StrategyPlan,
)
from app.models.enums import DataSourceKind, ImportBatchStatus, Platform
from app.schemas.ai_coo import OperatingKPI
from app.services.ai_coo_learning import _diagnose_kpis, ai_coo_learning_service


async def _task_with_strategy(session, admin) -> tuple[Account, BrainTask, StrategyPlan]:
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="真实学习闭环账号",
    )
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="提升账号播放量",
        runtime_mode="coo_v1",
        thread_id="brain-task-learning-loop",
    )
    session.add_all([account, task])
    await session.flush()
    strategy = StrategyPlan(
        org_id=admin.org_id,
        task_id=task.id,
        account_id=account.id,
        created_by_id=admin.id,
        status="approved",
        version=1,
        goal="提升账号播放量",
        situation_snapshot={"data_sufficiency": "partial"},
        strategy={"primary_action": "提高真实案例内容占比", "period_days": 30},
        kpis=[{"metric": "total_play", "baseline": 100, "target": 150}],
        risks=["样本量不足"],
        evidence_refs=[],
        rationale_summary="根据已导入播放数据建立观察周期。",
    )
    session.add(strategy)
    await session.commit()
    return account, task, strategy


@pytest.mark.asyncio
async def test_learning_loop_waits_for_new_real_observation(session, admin) -> None:
    account, task, strategy = await _task_with_strategy(session, admin)

    reflection = await ai_coo_learning_service.ensure_pending_observation(
        session,
        task=task,
        strategy=strategy,
        run_id=None,
    )
    refreshed = await ai_coo_learning_service.refresh_observation(
        session,
        task=task,
    )

    assert reflection.account_id == account.id
    assert refreshed.id == reflection.id
    assert refreshed.status == "pending_observation"
    assert refreshed.observed_outcome == {}
    assert refreshed.experience_candidates == []
    assert await session.scalar(select(ExperienceMemory.id)) is None


@pytest.mark.asyncio
async def test_learning_loop_uses_new_metric_and_creates_only_a_candidate(
    session,
    admin,
) -> None:
    account, task, strategy = await _task_with_strategy(session, admin)
    await ai_coo_learning_service.ensure_pending_observation(
        session,
        task=task,
        strategy=strategy,
        run_id=None,
    )
    batch = DataImportBatch(
        org_id=admin.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=ImportBatchStatus.COMMITTED,
        template_code="douyin_daily_play_v1",
        content_sha256="a" * 64,
        period_start=date(2026, 7, 27),
        period_end=date(2026, 7, 27),
        committed_at=datetime(2026, 7, 27, 12, 0, tzinfo=UTC),
    )
    session.add(batch)
    await session.flush()
    session.add(
        AccountMetricSnapshot(
            org_id=admin.org_id,
            account_id=account.id,
            import_batch_id=batch.id,
            source_kind=DataSourceKind.PLATFORM_EXPORT,
            stat_date=date(2026, 7, 27),
            total_play=180,
        )
    )
    await session.commit()

    reflection = await ai_coo_learning_service.refresh_observation(
        session,
        task=task,
    )

    assert reflection.status == "observed"
    assert reflection.observed_outcome["metrics"]["total_play"] == 180
    assert reflection.diagnosis[0]["result"] == "target_met"
    assert reflection.experience_candidates[0]["action"] == "提高真实案例内容占比"
    assert reflection.experience_candidates[0]["source_refs"]
    assert await session.scalar(select(ExperienceMemory.id)) is None


@pytest.mark.asyncio
async def test_operation_intelligence_uses_persisted_ledgers(session, admin) -> None:
    _account, task, strategy = await _task_with_strategy(session, admin)
    reflection = await ai_coo_learning_service.ensure_pending_observation(
        session,
        task=task,
        strategy=strategy,
        run_id=None,
    )
    reflection.status = "observed"
    session.add(
        AgentQualityScore(
            org_id=admin.org_id,
            task_id=task.id,
            score=88,
            dimensions={
                "brand_consistency": 85,
                "user_value": 90,
                "propagation_ability": 86,
                "commercial_conversion": 84,
                "factual_accuracy": 95,
            },
            passed=True,
            iteration=0,
        )
    )
    await session.commit()

    score = await ai_coo_learning_service.operation_intelligence(
        session,
        task=task,
    )

    assert score.score > 0
    assert score.components["strategy_quality"] == 100
    assert score.components["execution_effect"] == 88
    assert score.components["learning_quality"] == 70
    assert score.weights == {
        "strategy_quality": 0.30,
        "evidence_quality": 0.25,
        "execution_effect": 0.25,
        "learning_quality": 0.20,
    }


@pytest.mark.asyncio
async def test_operation_intelligence_prefers_observed_kpi_results(
    session,
    admin,
) -> None:
    _account, task, strategy = await _task_with_strategy(session, admin)
    reflection = await ai_coo_learning_service.ensure_pending_observation(
        session,
        task=task,
        strategy=strategy,
        run_id=None,
    )
    reflection.status = "observed"
    reflection.diagnosis = [
        {"metric": "播放量", "result": "target_met"},
        {"metric": "完播率", "result": "target_met"},
        {"metric": "有效咨询量", "result": "target_not_met"},
        {"metric": "互动率", "result": "observed"},
    ]
    session.add(
        AgentQualityScore(
            org_id=admin.org_id,
            task_id=task.id,
            score=88,
            dimensions={},
            passed=True,
            iteration=0,
        )
    )
    await session.commit()

    score = await ai_coo_learning_service.operation_intelligence(
        session,
        task=task,
    )

    assert score.components["execution_effect"] == 67
    assert "真实 KPI 达成 2/3" in score.basis


def test_operating_kpi_supports_measurable_targets_and_direction() -> None:
    kpi = OperatingKPI.model_validate(
        {
            "metric": "2s_skip_rate",
            "baseline": 0.42,
            "target": 0.30,
            "direction": "decrease",
            "evidence_ids": ["account-metric:1:2s_skip_rate"],
        }
    )

    assert kpi.target == 0.30
    assert kpi.baseline == 0.42
    assert kpi.direction == "decrease"


def test_kpi_diagnosis_respects_direction_and_non_numeric_targets() -> None:
    diagnoses = _diagnose_kpis(
        [
            {
                "metric": "2s_skip_rate",
                "baseline": 0.42,
                "target": 0.30,
                "direction": "decrease",
            },
            {
                "metric": "total_play",
                "baseline": 100,
                "target": 150,
                "direction": "increase",
            },
            {
                "metric": "consultation_baseline",
                "target": "建立有效咨询基线",
                "direction": "observe",
            },
        ],
        {
            "2s_skip_rate": 0.28,
            "total_play": 140,
            "consultation_baseline": 12,
        },
    )

    assert diagnoses[0]["result"] == "target_met"
    assert diagnoses[1]["result"] == "target_not_met"
    assert diagnoses[2]["result"] == "observed"
