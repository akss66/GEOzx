"""Evidence-gated observation, reflection, learning and intelligence scoring."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentQualityScore,
    BrainTask,
    ExperienceMemory,
    ReflectionRecord,
    StrategyPlan,
)
from app.schemas.ai_coo import OperationIntelligenceOut
from app.services.ai_coo_evidence import build_account_situation

OI_WEIGHTS = {
    "strategy_quality": 0.30,
    "evidence_quality": 0.25,
    "execution_effect": 0.25,
    "learning_quality": 0.20,
}


class AICOOLearningService:
    """Maintain the learning loop without treating model prose as verified experience."""

    async def ensure_pending_observation(
        self,
        session: AsyncSession,
        *,
        task: BrainTask,
        strategy: StrategyPlan,
        run_id: int | None,
    ) -> ReflectionRecord:
        existing = await self._latest_reflection(session, task)
        if existing is not None:
            return existing
        reflection = ReflectionRecord(
            org_id=task.org_id,
            task_id=task.id,
            run_id=run_id,
            client_id=strategy.client_id,
            project_id=strategy.project_id,
            account_id=strategy.account_id,
            status="pending_observation",
            goal_snapshot={"goal": strategy.goal},
            expected_outcome={
                "kpis": list(strategy.kpis),
                "strategy_plan_id": strategy.id,
            },
            observed_outcome={},
            evidence_refs=[],
            diagnosis=[],
            conclusion="等待新的真实效果数据后进行复盘。",
            next_strategy={},
            experience_candidates=[],
        )
        session.add(reflection)
        await session.commit()
        await session.refresh(reflection)
        return reflection

    async def refresh_observation(
        self,
        session: AsyncSession,
        *,
        task: BrainTask,
    ) -> ReflectionRecord:
        reflection = await self._latest_reflection(session, task)
        strategy = await self._latest_strategy(session, task)
        if strategy is None:
            raise ValueError("task has no strategy plan")
        if reflection is None:
            reflection = await self.ensure_pending_observation(
                session,
                task=task,
                strategy=strategy,
                run_id=None,
            )
        if reflection.account_id is None:
            return reflection

        situation = await build_account_situation(
            session,
            org_id=task.org_id,
            account_id=reflection.account_id,
        )
        current_refs = [
            item.model_dump(mode="json") for item in situation.evidence_refs
        ]
        baseline_refs = list(strategy.evidence_refs)
        new_refs = _new_evidence_refs(current_refs, baseline_refs)
        if not new_refs:
            reflection.status = "pending_observation"
            reflection.conclusion = "尚未发现晚于策略基线的新数据，继续等待观测。"
            await session.commit()
            await session.refresh(reflection)
            return reflection

        metrics = {
            str(item["metric"]): item["value"]
            for item in new_refs
            if item.get("metric")
        }
        diagnoses = _diagnose_kpis(list(strategy.kpis), metrics)
        candidates = _experience_candidates(strategy, diagnoses, new_refs)
        reflection.status = "observed"
        reflection.observed_outcome = {
            "metrics": metrics,
            "data_sufficiency": situation.data_sufficiency,
        }
        reflection.evidence_refs = new_refs
        reflection.diagnosis = diagnoses
        reflection.conclusion = _reflection_conclusion(diagnoses)
        reflection.next_strategy = _next_strategy(strategy, diagnoses)
        reflection.experience_candidates = candidates
        reflection.measured_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(reflection)
        return reflection

    async def verify_experience_candidate(
        self,
        session: AsyncSession,
        *,
        reflection: ReflectionRecord,
        candidate_key: str,
        verified_by_id: int,
        verification_note: str,
    ) -> ExperienceMemory:
        candidate = next(
            (
                item
                for item in reflection.experience_candidates
                if item.get("key") == candidate_key
            ),
            None,
        )
        if candidate is None:
            raise ValueError("experience candidate does not exist")
        if reflection.status != "observed" or not candidate.get("source_refs"):
            raise ValueError("experience candidate has not passed evidence verification")
        existing = await session.scalar(
            select(ExperienceMemory).where(
                ExperienceMemory.reflection_id == reflection.id,
                ExperienceMemory.action == str(candidate["action"]),
                ExperienceMemory.condition == str(candidate["condition"]),
            )
        )
        if existing is not None:
            return existing
        memory = ExperienceMemory(
            org_id=reflection.org_id,
            task_id=reflection.task_id,
            reflection_id=reflection.id,
            client_id=reflection.client_id,
            project_id=reflection.project_id,
            account_id=reflection.account_id,
            verified_by_id=verified_by_id,
            status="verified",
            industry=str(candidate.get("industry") or ""),
            action=str(candidate["action"]),
            condition=str(candidate["condition"]),
            result=str(candidate["result"]),
            confidence=Decimal(str(candidate["confidence"])),
            source_refs=list(candidate["source_refs"]),
            verification_method="manual_confirmation",
            verification_note=verification_note,
            verified_at=datetime.now(UTC),
        )
        session.add(memory)
        await session.commit()
        await session.refresh(memory)
        return memory

    async def operation_intelligence(
        self,
        session: AsyncSession,
        *,
        task: BrainTask,
    ) -> OperationIntelligenceOut:
        strategy = await self._latest_strategy(session, task)
        reflection = await self._latest_reflection(session, task)
        quality_average = await session.scalar(
            select(func.avg(AgentQualityScore.score)).where(
                AgentQualityScore.org_id == task.org_id,
                AgentQualityScore.task_id == task.id,
            )
        )
        verified_count = int(
            await session.scalar(
                select(func.count(ExperienceMemory.id)).where(
                    ExperienceMemory.org_id == task.org_id,
                    ExperienceMemory.task_id == task.id,
                    ExperienceMemory.status == "verified",
                )
            )
            or 0
        )
        execution_effect, execution_basis = _execution_effect(
            reflection,
            quality_average,
        )

        components = {
            "strategy_quality": _strategy_quality(strategy),
            "evidence_quality": _evidence_quality(strategy, reflection),
            "execution_effect": execution_effect,
            "learning_quality": _learning_quality(reflection, verified_count),
        }
        score = round(
            sum(
                components[name] * weight
                for name, weight in OI_WEIGHTS.items()
            )
        )
        basis = _score_basis(
            strategy,
            reflection,
            quality_average,
            verified_count,
            execution_basis,
        )
        non_zero = sum(value > 0 for value in components.values())
        data_sufficiency = (
            "sufficient" if non_zero == 4 else "partial" if non_zero >= 2 else "insufficient"
        )
        return OperationIntelligenceOut(
            task_id=task.id,
            score=score,
            components=components,
            weights=OI_WEIGHTS,
            basis=basis,
            data_sufficiency=data_sufficiency,
            calculated_at=datetime.now(UTC),
        )

    @staticmethod
    async def _latest_strategy(
        session: AsyncSession,
        task: BrainTask,
    ) -> StrategyPlan | None:
        return await session.scalar(
            select(StrategyPlan)
            .where(
                StrategyPlan.org_id == task.org_id,
                StrategyPlan.task_id == task.id,
            )
            .order_by(StrategyPlan.version.desc(), StrategyPlan.id.desc())
            .limit(1)
        )

    @staticmethod
    async def _latest_reflection(
        session: AsyncSession,
        task: BrainTask,
    ) -> ReflectionRecord | None:
        return await session.scalar(
            select(ReflectionRecord)
            .where(
                ReflectionRecord.org_id == task.org_id,
                ReflectionRecord.task_id == task.id,
            )
            .order_by(ReflectionRecord.id.desc())
            .limit(1)
        )


def _evidence_identity(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("source_type") or ""),
        str(item.get("source_id") or ""),
        str(item.get("metric") or ""),
    )


def _new_evidence_refs(
    current: list[dict[str, Any]],
    baseline: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    baseline_ids = {_evidence_identity(item) for item in baseline}
    return [item for item in current if _evidence_identity(item) not in baseline_ids]


def _diagnose_kpis(
    kpis: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for kpi in kpis:
        metric = str(kpi.get("metric") or "")
        if not metric or metric not in metrics:
            continue
        actual = metrics[metric]
        target = kpi.get("target")
        baseline = kpi.get("baseline")
        direction = str(kpi.get("direction") or "increase")
        result = "observed"
        if (
            direction != "observe"
            and isinstance(actual, (int, float))
            and isinstance(target, (int, float))
        ):
            if direction == "decrease":
                passed = actual <= target
            elif direction == "maintain":
                passed = actual == target
            else:
                passed = actual >= target
            result = "target_met" if passed else "target_not_met"
        results.append(
            {
                "metric": metric,
                "baseline": baseline,
                "target": target,
                "direction": direction,
                "actual": actual,
                "result": result,
            }
        )
    return results


def _experience_candidates(
    strategy: StrategyPlan,
    diagnoses: list[dict[str, Any]],
    source_refs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    action = str(strategy.strategy.get("primary_action") or "").strip()
    candidates: list[dict[str, Any]] = []
    for diagnosis in diagnoses:
        if diagnosis["result"] != "target_met" or not action:
            continue
        metric = diagnosis["metric"]
        candidates.append(
            {
                "key": f"strategy-{strategy.id}-{metric}",
                "industry": str(strategy.strategy.get("industry") or ""),
                "action": action,
                "condition": strategy.goal,
                "result": (
                    f"{metric} 从基线 {diagnosis['baseline']} 达到 "
                    f"{diagnosis['actual']}，目标为 {diagnosis['target']}"
                ),
                "confidence": 0.7,
                "source_refs": source_refs,
                "status": "candidate",
            }
        )
    return candidates


def _reflection_conclusion(diagnoses: list[dict[str, Any]]) -> str:
    if not diagnoses:
        return "已收到新的真实数据，但当前 KPI 无可比较指标。"
    met = sum(item["result"] == "target_met" for item in diagnoses)
    return f"已完成真实数据复盘，{met}/{len(diagnoses)} 项可比较 KPI 达到目标。"


def _next_strategy(
    strategy: StrategyPlan,
    diagnoses: list[dict[str, Any]],
) -> dict[str, Any]:
    if diagnoses and all(item["result"] == "target_met" for item in diagnoses):
        return {
            "action": "continue_and_expand_observation",
            "source_strategy_plan_id": strategy.id,
        }
    return {
        "action": "review_strategy_before_next_cycle",
        "source_strategy_plan_id": strategy.id,
    }


def _strategy_quality(strategy: StrategyPlan | None) -> int:
    if strategy is None:
        return 0
    return min(
        100,
        (25 if strategy.goal else 0)
        + (25 if strategy.strategy else 0)
        + (20 if strategy.kpis else 0)
        + (15 if strategy.risks else 0)
        + (15 if strategy.rationale_summary else 0),
    )


def _evidence_quality(
    strategy: StrategyPlan | None,
    reflection: ReflectionRecord | None,
) -> int:
    refs = list(strategy.evidence_refs if strategy else [])
    refs.extend(reflection.evidence_refs if reflection else [])
    if not refs:
        return 0
    fresh = sum(item.get("freshness") == "fresh" for item in refs)
    traceable = sum(bool(item.get("source_id") and item.get("metric")) for item in refs)
    return min(100, 20 + traceable * 15 + fresh * 10)


def _learning_quality(
    reflection: ReflectionRecord | None,
    verified_count: int,
) -> int:
    if reflection is None:
        return 0
    if verified_count:
        return 100
    if reflection.status == "observed":
        return 70
    return 20


def _execution_effect(
    reflection: ReflectionRecord | None,
    quality_average: Any,
) -> tuple[int, str | None]:
    comparable = [
        item
        for item in (reflection.diagnosis if reflection else [])
        if item.get("result") in {"target_met", "target_not_met"}
    ]
    if comparable:
        met = sum(item.get("result") == "target_met" for item in comparable)
        return round(met / len(comparable) * 100), f"真实 KPI 达成 {met}/{len(comparable)}"
    if quality_average is not None:
        return round(float(quality_average)), "尚无可比较 KPI，暂以专家质量均分作为执行代理"
    return 0, None


def _score_basis(
    strategy: StrategyPlan | None,
    reflection: ReflectionRecord | None,
    quality_average: Any,
    verified_count: int,
    execution_basis: str | None,
) -> list[str]:
    basis: list[str] = []
    if strategy is not None:
        basis.append(f"策略版本 {strategy.version}")
    if quality_average is not None:
        basis.append(f"专家质量均分 {round(float(quality_average))}")
    if execution_basis:
        basis.append(execution_basis)
    if reflection is not None:
        basis.append(f"复盘状态 {reflection.status}")
    if verified_count:
        basis.append(f"已验证经验 {verified_count} 条")
    return basis


ai_coo_learning_service = AICOOLearningService()
