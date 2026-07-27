"""Persisted quality policy for specialist output in the AI COO runtime."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentInvocation, AgentQualityScore, BrainTask
from app.schemas.ai_coo import CriticEvaluation

_PASS_THRESHOLD = 80
_FACTUAL_ACCURACY_FLOOR = 75
_MAX_IMPROVEMENT_ITERATIONS = 2


class CriticDisposition(StrEnum):
    PASS = "pass"
    IMPROVE = "improve"
    HUMAN = "human"


@dataclass(frozen=True)
class CriticRecordResult:
    score: AgentQualityScore
    disposition: CriticDisposition


class AICOOCriticService:
    """Apply server-owned thresholds and write one idempotent quality ledger."""

    async def record(
        self,
        session: AsyncSession,
        *,
        task: BrainTask,
        invocation: AgentInvocation,
        deliverable_id: int | None,
        evaluation: CriticEvaluation,
        iteration: int,
        evidence_refs: list[dict],
        prompt_id: str,
        prompt_version: str,
        prompt_hash: str,
        critic_model: str,
    ) -> CriticRecordResult:
        if invocation.task_id != task.id:
            raise ValueError("critic invocation does not belong to task")
        if not 0 <= iteration <= _MAX_IMPROVEMENT_ITERATIONS:
            raise ValueError("critic iteration is outside the bounded retry policy")

        existing = await session.scalar(
            select(AgentQualityScore).where(
                AgentQualityScore.task_id == task.id,
                AgentQualityScore.invocation_id == invocation.id,
                AgentQualityScore.iteration == iteration,
            )
        )
        if existing is not None:
            return CriticRecordResult(
                score=existing,
                disposition=self._disposition(existing.passed, iteration),
            )

        dimensions = evaluation.dimensions.model_dump(mode="json")
        score_value = round(sum(dimensions.values()) / len(dimensions))
        passed = (
            score_value >= _PASS_THRESHOLD
            and dimensions["factual_accuracy"] >= _FACTUAL_ACCURACY_FLOOR
        )
        score = AgentQualityScore(
            org_id=task.org_id,
            task_id=task.id,
            run_id=invocation.run_id,
            invocation_id=invocation.id,
            deliverable_id=deliverable_id,
            score=score_value,
            dimensions=dimensions,
            issues=evaluation.issues,
            suggestions=evaluation.suggestions,
            passed=passed,
            iteration=iteration,
            evidence_refs=evidence_refs,
            critic_prompt_id=prompt_id,
            critic_prompt_version=prompt_version,
            critic_prompt_hash=prompt_hash,
            critic_model=critic_model,
        )
        session.add(score)
        await session.commit()
        await session.refresh(score)
        return CriticRecordResult(
            score=score,
            disposition=self._disposition(passed, iteration),
        )

    @staticmethod
    def _disposition(passed: bool, iteration: int) -> CriticDisposition:
        if passed:
            return CriticDisposition.PASS
        if iteration < _MAX_IMPROVEMENT_ITERATIONS:
            return CriticDisposition.IMPROVE
        return CriticDisposition.HUMAN


ai_coo_critic_service = AICOOCriticService()
