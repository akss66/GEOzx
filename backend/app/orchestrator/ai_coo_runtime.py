"""Evidence-first operating context for the AI COO runtime."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Account,
    AccountClient,
    BrainTask,
    DecisionTrace,
    Project,
    ProjectAccount,
    StrategyPlan,
)
from app.orchestrator.brain_intelligence import (
    IntelligenceUnavailable,
    brain_intelligence,
)
from app.schemas.ai_coo import COOMemoryContext, OperatingStrategyDraft
from app.services.ai_coo_evidence import build_account_situation
from app.services.ai_coo_memory import build_coo_memory_context

_EXPERT_PURPOSES = {
    "01-positioning": "账号定位诊断",
    "02-content-director": "内容策略与脚本规划",
    "03-art-director": "视觉方向规划",
    "04-video-creator": "视频素材规划",
    "05-editor": "成片剪辑规划",
    "06-operator": "账号运营与发布规划",
    "07-advertiser": "投放策略规划",
    "08-customer-service": "用户反馈与客服策略",
}


@dataclass(frozen=True)
class OperatingContextResult:
    account_id: int | None
    active_client_id: int | None
    active_project_id: int | None
    available_client_ids: list[int]
    available_project_ids: list[int]
    normalized_goal: dict[str, Any]
    situation_summary: dict[str, Any]
    evidence_refs: list[dict[str, Any]]
    memory_context: COOMemoryContext
    strategy_plan_id: int
    strategy_status: str
    decision_trace_id: int
    task_plan: list[dict[str, Any]]


class AICOOOperatingService:
    """Create the auditable operating baseline before expert execution."""

    async def resolve_context(
        self,
        session: AsyncSession,
        *,
        task: BrainTask,
        account_id: int | None = None,
    ) -> dict[str, Any]:
        """Resolve the real account/client/project scope without inventing defaults."""

        return await _resolve_scope(
            session,
            task,
            _first_account_id(task) if account_id is None else account_id,
        )

    async def prepare(
        self,
        session: AsyncSession,
        *,
        task: BrainTask,
        run_id: int | None,
        required_expert_codes: list[str],
    ) -> OperatingContextResult:
        brief = task.brief
        goal = (brief.goal if brief else task.title).strip()
        account_id = _first_account_id(task)
        scope = await self.resolve_context(
            session,
            task=task,
            account_id=account_id,
        )
        situation = await self._situation(
            session,
            task=task,
            account_id=account_id,
        )
        situation_payload = situation.model_dump(mode="json")
        evidence_refs = [
            item.model_dump(mode="json") for item in situation.evidence_refs
        ]
        normalized_goal = {
            "objective": goal,
            "content_goal": brief.content_goal if brief else "",
            "expected_outputs": list(brief.expected_outputs) if brief else [],
            "risk_constraints": list(brief.risk_constraints) if brief else [],
            "platforms": list(brief.platforms) if brief else [],
            "account_id": account_id,
        }
        memory_context = await build_coo_memory_context(
            session,
            org_id=task.org_id,
            account_id=account_id,
            client_ids=scope["available_client_ids"],
            project_ids=scope["available_project_ids"],
            situation_summary=situation_payload,
        )
        existing_strategy = await session.scalar(
            select(StrategyPlan)
            .where(StrategyPlan.task_id == task.id)
            .order_by(StrategyPlan.version.desc(), StrategyPlan.id.desc())
            .limit(1)
        )
        existing_trace = await session.scalar(
            select(DecisionTrace)
            .where(
                DecisionTrace.task_id == task.id,
                DecisionTrace.trace_key == "initial-operating-strategy-v1",
            )
            .limit(1)
        )
        if existing_strategy is not None and existing_trace is not None:
            task_plan = _build_task_plan(
                existing_strategy.strategy.get(
                    "expert_sequence",
                    required_expert_codes,
                )
            )
            return OperatingContextResult(
                account_id=account_id,
                active_client_id=scope["active_client_id"],
                active_project_id=scope["active_project_id"],
                available_client_ids=scope["available_client_ids"],
                available_project_ids=scope["available_project_ids"],
                normalized_goal=normalized_goal,
                situation_summary=situation_payload,
                evidence_refs=evidence_refs,
                memory_context=memory_context,
                strategy_plan_id=existing_strategy.id,
                strategy_status=existing_strategy.status,
                decision_trace_id=existing_trace.id,
                task_plan=task_plan,
            )

        data_insufficient = situation.data_sufficiency == "insufficient"
        strategy_status = "data_collection_required" if data_insufficient else "draft"
        strategy_draft: OperatingStrategyDraft | None = None
        strategy_prompt_id: str | None = None
        strategy_prompt_version: str | None = None
        strategy_prompt_hash: str | None = None
        strategy_schema_version = "1.0"
        if evidence_refs:
            try:
                strategy_model_plan = await brain_intelligence.plan_operating_strategy(
                    session,
                    task.org_id,
                    goal=goal,
                    situation=situation_payload,
                    evidence_refs=evidence_refs,
                    suggested_expert_codes=required_expert_codes,
                    memory_context=memory_context.model_dump(mode="json"),
                )
                strategy_draft = strategy_model_plan.draft
                validate_strategy_draft_evidence(
                    strategy_draft,
                    {_evidence_id(item) for item in evidence_refs},
                )
                strategy_prompt_id = strategy_model_plan.prompt.spec.id
                strategy_prompt_version = strategy_model_plan.prompt.spec.version
                strategy_prompt_hash = strategy_model_plan.prompt.content_hash
                strategy_schema_version = (
                    strategy_model_plan.prompt.spec.schema_version or "1.0"
                )
            except (IntelligenceUnavailable, ValueError):
                strategy_draft = None

        selected_expert_codes = (
            list(strategy_draft.required_expert_codes)
            if strategy_draft is not None
            else required_expert_codes
        )
        task_plan = _build_task_plan(selected_expert_codes)
        if strategy_draft is not None:
            situation_payload.update(
                {
                    "account_stage": strategy_draft.account_stage,
                    "main_problem": strategy_draft.main_problem,
                    "data_sufficiency": strategy_draft.data_sufficiency,
                    "missing_data": list(strategy_draft.missing_data),
                    "confidence": float(strategy_draft.confidence),
                    "diagnosis": [
                        item.model_dump(mode="json")
                        for item in strategy_draft.diagnosis
                    ],
                }
            )
            strategy_payload = {
                "mode": "evidence_grounded",
                **strategy_draft.strategy.model_dump(mode="json"),
                "next_action": "execute_task_plan",
                "expert_sequence": selected_expert_codes,
            }
            strategy_kpis = [
                item.model_dump(mode="json") for item in strategy_draft.kpis
            ]
            strategy_risks = list(strategy_draft.risks)
            rationale = strategy_draft.rationale_summary
        else:
            strategy_payload = {
                "mode": (
                    "evidence_first"
                    if data_insufficient
                    else "evidence_grounded_fallback"
                ),
                "period_days": 30,
                "next_action": (
                    "collect_baseline"
                    if data_insufficient
                    else "execute_task_plan"
                ),
                "expert_sequence": selected_expert_codes,
            }
            strategy_kpis = [
                {
                    "metric": "evidence_coverage",
                    "target": "required_sources_present",
                    "evidence_ids": [],
                }
            ]
            strategy_risks = list(situation.missing_data)
            rationale = (
                "当前缺少可追溯的账号数据，先补齐运营基线，再形成业务判断。"
                if data_insufficient
                else "当前存在可追溯账号数据，策略模型暂不可用，先按必要专家链路继续分析。"
            )
        strategy = StrategyPlan(
            org_id=task.org_id,
            task_id=task.id,
            run_id=run_id,
            client_id=scope["active_client_id"],
            project_id=scope["active_project_id"],
            account_id=account_id,
            created_by_id=task.created_by_id,
            status=strategy_status,
            version=1,
            goal=goal,
            situation_snapshot=situation_payload,
            strategy=strategy_payload,
            kpis=strategy_kpis,
            risks=strategy_risks,
            evidence_refs=evidence_refs,
            rationale_summary=rationale,
            prompt_id=strategy_prompt_id,
            prompt_version=strategy_prompt_version,
            prompt_hash=strategy_prompt_hash,
            schema_version=strategy_schema_version,
        )
        session.add(strategy)
        await session.flush()

        selected_key = "collect_baseline" if data_insufficient else "execute_task_plan"
        trace = DecisionTrace(
            org_id=task.org_id,
            task_id=task.id,
            run_id=run_id,
            client_id=scope["active_client_id"],
            project_id=scope["active_project_id"],
            account_id=account_id,
            trace_key="initial-operating-strategy-v1",
            goal=goal,
            evidence_refs=evidence_refs,
            alternatives=[
                {
                    "key": "collect_baseline",
                    "label": "补齐真实运营基线",
                },
                {
                    "key": "execute_task_plan",
                    "label": "按现有证据执行专家计划",
                },
            ],
            selected_option={"key": selected_key},
            decision_reason=rationale,
            action_summary=" → ".join(
                step["purpose"] for step in task_plan
            )
            or "暂不调度专家，等待明确目标。",
            status="decided",
            decided_at=datetime.now(UTC),
        )
        session.add(trace)
        await session.commit()
        await session.refresh(strategy)
        await session.refresh(trace)
        return OperatingContextResult(
            account_id=account_id,
            active_client_id=scope["active_client_id"],
            active_project_id=scope["active_project_id"],
            available_client_ids=scope["available_client_ids"],
            available_project_ids=scope["available_project_ids"],
            normalized_goal=normalized_goal,
            situation_summary=situation_payload,
            evidence_refs=evidence_refs,
            memory_context=memory_context,
            strategy_plan_id=strategy.id,
            strategy_status=strategy.status,
            decision_trace_id=trace.id,
            task_plan=task_plan,
        )

    async def _situation(
        self,
        session: AsyncSession,
        *,
        task: BrainTask,
        account_id: int | None,
    ):
        if account_id is not None:
            return await build_account_situation(
                session,
                org_id=task.org_id,
                account_id=account_id,
            )
        from app.schemas.ai_coo import AccountSituationOut

        return AccountSituationOut(
            account_id=0,
            generated_at=datetime.now(UTC),
            data_sufficiency="insufficient",
            conclusion="数据不足",
            diagnosis=[],
            evidence_refs=[],
            missing_data=["账号上下文"],
            confidence=0,
        )


def _first_account_id(task: BrainTask) -> int | None:
    if task.brief and task.brief.account_ids:
        return int(task.brief.account_ids[0])
    return None


async def _resolve_scope(
    session: AsyncSession,
    task: BrainTask,
    account_id: int | None,
) -> dict[str, Any]:
    if account_id is None:
        return {
            "active_client_id": None,
            "active_project_id": task.brief.project_id if task.brief else None,
            "available_client_ids": [],
            "available_project_ids": [],
        }
    account = await session.scalar(
        select(Account).where(
            Account.id == account_id,
            Account.org_id == task.org_id,
        )
    )
    if account is None:
        raise ValueError("brain task account is unavailable")

    client_ids = set(
        (
            await session.scalars(
                select(AccountClient.client_id).where(
                    AccountClient.account_id == account_id
                )
            )
        ).all()
    )
    project_ids = set(
        (
            await session.scalars(
                select(ProjectAccount.project_id).where(
                    ProjectAccount.account_id == account_id
                )
            )
        ).all()
    )
    if account.client_id is not None:
        client_ids.add(account.client_id)
    if account.project_id is not None:
        project_ids.add(account.project_id)

    requested_project_id = task.brief.project_id if task.brief else None
    active_project_id = (
        requested_project_id
        if requested_project_id in project_ids
        else account.project_id
    )
    active_client_id = account.client_id
    if active_project_id is not None:
        project_client_id = await session.scalar(
            select(Project.client_id).where(
                Project.id == active_project_id,
                Project.org_id == task.org_id,
            )
        )
        if project_client_id is not None:
            active_client_id = project_client_id
            client_ids.add(project_client_id)

    return {
        "active_client_id": active_client_id,
        "active_project_id": active_project_id,
        "available_client_ids": sorted(client_ids),
        "available_project_ids": sorted(project_ids),
    }


def _build_task_plan(required_expert_codes: Sequence[str]) -> list[dict[str, Any]]:
    return [
        {
            "order": index,
            "agent_code": code,
            "purpose": _EXPERT_PURPOSES.get(code, "完成本轮专业任务"),
            "status": "planned",
        }
        for index, code in enumerate(dict.fromkeys(required_expert_codes), start=1)
        if code in _EXPERT_PURPOSES
    ]


def validate_strategy_draft_evidence(
    draft: OperatingStrategyDraft,
    available_evidence_ids: set[str],
) -> None:
    referenced = {
        evidence_id
        for diagnosis in draft.diagnosis
        for evidence_id in diagnosis.evidence_ids
    }
    referenced.update(
        evidence_id
        for kpi in draft.kpis
        for evidence_id in kpi.evidence_ids
    )
    unknown = sorted(referenced - available_evidence_ids)
    if unknown:
        raise ValueError(f"unknown evidence ids: {', '.join(unknown)}")


def _evidence_id(item: dict[str, Any]) -> str:
    return f"{item.get('source_type')}:{item.get('source_id')}:{item.get('metric')}"


ai_coo_operating_service = AICOOOperatingService()
