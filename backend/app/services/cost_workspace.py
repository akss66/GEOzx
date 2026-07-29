"""Scoped business cost rollups and admin technical telemetry."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.workspace_access import (
    accessible_account_ids,
    accessible_project_ids,
    require_client_access,
    require_project_access,
)
from app.models import (
    AgentInvocation,
    AgentToolCall,
    BrainTask,
    LLMCall,
    Project,
    TaskBrief,
    User,
)
from app.models.enums import AgentInvocationStatus
from app.schemas.costs import (
    BusinessCostSummaryOut,
    CostAgentRow,
    CostDailyRow,
    CostOverviewOut,
    CostProjectRow,
    CostScopeOut,
    CostTaskRow,
    CostToolRow,
    TechnicalAgentRow,
    TechnicalCostOverviewOut,
    TechnicalCostSummaryOut,
    TechnicalDailyRow,
    TechnicalModelRow,
    TechnicalProviderRow,
)
from app.services.model_infrastructure import AGENT_NAMES

FAILED_AGENT_STATUSES = {
    AgentInvocationStatus.FAILED,
    AgentInvocationStatus.BLOCKED,
}
FAILED_TOOL_STATUSES = {"failed", "error", "blocked", "rejected"}


def _cost(value: Decimal | float | int | None) -> float:
    return round(float(value or 0), 4)


def _raw_cost(value: Decimal | float | int | None) -> float:
    return float(value or 0)


def _budget_metrics(budget: Decimal | float | None, actual: float):
    if budget is None:
        return None, None, "no_budget"
    budget_value = _cost(budget)
    if budget_value <= 0:
        usage = 100.0 if actual > 0 else 0.0
    else:
        usage = round(actual / budget_value * 100, 2)
    status_value = "exceeded" if usage >= 100 else "warning" if usage >= 80 else "healthy"
    return usage, _cost(budget_value - actual), status_value


async def business_cost_overview(
    session: AsyncSession,
    *,
    user: User,
    client_id: int,
    project_id: int | None,
    days: int,
) -> CostOverviewOut:
    client = await require_client_access(session, user, client_id)
    selected_project: Project | None = None
    if project_id is not None:
        selected_project = await require_project_access(session, user, project_id)
        if selected_project.client_id != client.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
        projects = [selected_project]
    else:
        allowed_ids = await accessible_project_ids(session, user)
        projects = list(
            await session.scalars(
                select(Project)
                .where(Project.client_id == client.id, Project.id.in_(allowed_ids))
                .order_by(Project.id)
            )
        ) if allowed_ids else []

    project_ids = [project.id for project in projects]
    period_end = datetime.now(UTC)
    period_start = period_end - timedelta(days=days)

    task_rows: list[tuple[BrainTask, TaskBrief]] = []
    if project_ids:
        has_period_activity = or_(
            BrainTask.invocations.any(AgentInvocation.created_at >= period_start),
            BrainTask.tool_calls.any(AgentToolCall.created_at >= period_start),
            exists(
                select(LLMCall.id).where(
                    LLMCall.org_id == user.org_id,
                    LLMCall.task_id == BrainTask.id,
                    LLMCall.created_at >= period_start,
                )
            ),
        )
        task_rows = list(
            (
                await session.execute(
                    select(BrainTask, TaskBrief)
                    .join(TaskBrief, TaskBrief.task_id == BrainTask.id)
                    .where(
                        BrainTask.org_id == user.org_id,
                        TaskBrief.project_id.in_(project_ids),
                        has_period_activity,
                    )
                )
            ).tuples()
        )

    visible_account_ids = await accessible_account_ids(session, user)
    if visible_account_ids is not None:
        task_rows = [
            row
            for row in task_rows
            if not row[1].account_ids
            or bool(set(row[1].account_ids) & visible_account_ids)
        ]

    invocation_rows: list[tuple[AgentInvocation, BrainTask, TaskBrief]] = []
    tool_rows: list[tuple[AgentToolCall, BrainTask, TaskBrief]] = []
    llm_calls: list[LLMCall] = []
    task_ids = {task.id for task, _brief in task_rows}
    if task_ids:
        invocation_rows = list(
            (
                await session.execute(
                    select(AgentInvocation, BrainTask, TaskBrief)
                    .join(BrainTask, BrainTask.id == AgentInvocation.task_id)
                    .join(TaskBrief, TaskBrief.task_id == BrainTask.id)
                    .where(
                        AgentInvocation.task_id.in_(task_ids),
                        AgentInvocation.created_at >= period_start,
                    )
                )
            ).tuples()
        )
        tool_rows = list(
            (
                await session.execute(
                    select(AgentToolCall, BrainTask, TaskBrief)
                    .join(BrainTask, BrainTask.id == AgentToolCall.task_id)
                    .join(TaskBrief, TaskBrief.task_id == BrainTask.id)
                    .where(
                        AgentToolCall.task_id.in_(task_ids),
                        AgentToolCall.created_at >= period_start,
                    )
                )
            ).tuples()
        )
        llm_calls = list(
            await session.scalars(
                select(LLMCall)
                .where(
                    LLMCall.org_id == user.org_id,
                    LLMCall.task_id.in_(task_ids),
                    LLMCall.created_at >= period_start,
                )
                .order_by(LLMCall.created_at)
            )
        )

    task_rollups: dict[int, dict[str, Any]] = {}
    agent_rollups: dict[tuple[str, str], dict[str, Any]] = {}
    unlinked_agent_tasks: dict[tuple[str, str], set[int]] = defaultdict(set)
    failed_unlinked_agent_tasks: dict[tuple[str, str], set[int]] = defaultdict(set)
    tool_rollups: dict[tuple[str, str], dict[str, Any]] = {}
    project_costs: dict[int, float] = defaultdict(float)
    project_tasks: dict[int, set[int]] = defaultdict(set)
    daily_costs: dict[Any, float] = defaultdict(float)
    invocation_context = {
        invocation.id: (invocation, task, brief)
        for invocation, task, brief in invocation_rows
    }
    task_context = {task.id: (task, brief) for task, brief in task_rows}
    metered_invocation_ids = {
        call.invocation_id for call in llm_calls if call.invocation_id is not None
    }

    for invocation, task, brief in invocation_rows:
        project_id = brief.project_id
        if project_id is None:
            continue
        project_key = project_id
        value = (
            0.0
            if invocation.id in metered_invocation_ids
            else _raw_cost(invocation.cost)
        )
        failed = invocation.status in FAILED_AGENT_STATUSES
        rollup = task_rollups.setdefault(
            task.id,
            {
                "task": task,
                "agent_calls": 0,
                "tool_calls": 0,
                "cost": 0.0,
            },
        )
        rollup["agent_calls"] += 1
        rollup["cost"] += value
        agent_key = (invocation.agent_code.value, invocation.agent_name)
        agent = agent_rollups.setdefault(agent_key, {"calls": 0, "cost": 0.0, "failed": 0})
        agent["calls"] += 1
        agent["cost"] += value
        agent["failed"] += int(failed)
        project_costs[project_key] += value
        project_tasks[project_key].add(task.id)
        daily_costs[invocation.created_at.date()] += value

    for call in llm_calls:
        context = (
            invocation_context.get(call.invocation_id)
            if call.invocation_id is not None
            else None
        )
        if context is not None:
            invocation, task, brief = context
            agent_key = (invocation.agent_code.value, invocation.agent_name)
        else:
            task_id = call.task_id
            if task_id is None:
                continue
            task_brief = task_context.get(task_id)
            if task_brief is None:
                continue
            task, brief = task_brief
            agent_code = call.agent_code or "unassigned"
            agent_key = (agent_code, AGENT_NAMES.get(agent_code, "系统调用"))

        project_id = brief.project_id
        if project_id is None:
            continue
        project_key = project_id
        value = _raw_cost(call.cost_usd)
        rollup = task_rollups.setdefault(
            task.id,
            {
                "task": task,
                "agent_calls": 0,
                "tool_calls": 0,
                "cost": 0.0,
            },
        )
        rollup["cost"] += value
        agent = agent_rollups.setdefault(
            agent_key,
            {"calls": 0, "cost": 0.0, "failed": 0},
        )
        if context is None:
            if task.id not in unlinked_agent_tasks[agent_key]:
                unlinked_agent_tasks[agent_key].add(task.id)
                agent["calls"] += 1
            if (
                call.status != "ok"
                and task.id not in failed_unlinked_agent_tasks[agent_key]
            ):
                failed_unlinked_agent_tasks[agent_key].add(task.id)
                agent["failed"] += 1
        agent["cost"] += value
        project_costs[project_key] += value
        project_tasks[project_key].add(task.id)
        daily_costs[call.created_at.date()] += value

    for tool, task, brief in tool_rows:
        project_id = brief.project_id
        if project_id is None:
            continue
        project_key = project_id
        value = _raw_cost(tool.cost)
        failed = tool.status in FAILED_TOOL_STATUSES
        rollup = task_rollups.setdefault(
            task.id,
            {
                "task": task,
                "agent_calls": 0,
                "tool_calls": 0,
                "cost": 0.0,
            },
        )
        rollup["tool_calls"] += 1
        rollup["cost"] += value
        tool_key = (tool.tool_code, tool.tool_name)
        tool_row = tool_rollups.setdefault(tool_key, {"calls": 0, "cost": 0.0, "failed": 0})
        tool_row["calls"] += 1
        tool_row["cost"] += value
        tool_row["failed"] += int(failed)
        project_costs[project_key] += value
        project_tasks[project_key].add(task.id)
        daily_costs[tool.created_at.date()] += value

    actual_cost = _cost(sum(project_costs.values()))
    configured_budgets = [project.monthly_cost_budget_usd for project in projects]
    budget: Decimal | None = (
        sum((value for value in configured_budgets if value is not None), Decimal("0"))
        if any(value is not None for value in configured_budgets)
        else None
    )
    usage, remaining, budget_status = _budget_metrics(budget, actual_cost)
    failed_operations = sum(row["failed"] for row in agent_rollups.values()) + sum(
        row["failed"] for row in tool_rollups.values()
    )

    return CostOverviewOut(
        scope=CostScopeOut(
            client_id=client.id,
            client_name=client.name,
            project_id=selected_project.id if selected_project else None,
            project_name=selected_project.name if selected_project else None,
            period_days=days,
            period_start=period_start,
            period_end=period_end,
        ),
        summary=BusinessCostSummaryOut(
            actual_cost=actual_cost,
            budget=_cost(budget) if budget is not None else None,
            budget_usage=usage,
            remaining_budget=remaining,
            task_count=len(task_rollups),
            agent_calls=len(invocation_rows),
            tool_calls=len(tool_rows),
            failed_operations=failed_operations,
            budget_status=budget_status,
        ),
        by_project=[
            _project_row(project, project_costs[project.id], len(project_tasks[project.id]))
            for project in sorted(projects, key=lambda item: project_costs[item.id], reverse=True)
        ],
        by_agent=[
            CostAgentRow(
                agent_code=code,
                agent_name=name,
                calls=row["calls"],
                cost=_cost(row["cost"]),
                failed_calls=row["failed"],
            )
            for (code, name), row in sorted(
                agent_rollups.items(), key=lambda item: item[1]["cost"], reverse=True
            )
        ],
        by_task=[
            CostTaskRow(
                task_id=task_id,
                title=row["task"].title,
                type=row["task"].type,
                status=row["task"].status.value,
                agent_calls=row["agent_calls"],
                tool_calls=row["tool_calls"],
                cost=_cost(row["cost"]),
            )
            for task_id, row in sorted(
                task_rollups.items(), key=lambda item: item[1]["cost"], reverse=True
            )
        ],
        by_tool=[
            CostToolRow(
                tool_code=code,
                tool_name=name,
                calls=row["calls"],
                cost=_cost(row["cost"]),
                failed_calls=row["failed"],
            )
            for (code, name), row in sorted(
                tool_rollups.items(), key=lambda item: item[1]["cost"], reverse=True
            )
        ],
        daily=[
            CostDailyRow(date=day, cost=_cost(value))
            for day, value in sorted(daily_costs.items())
        ],
    )


def _project_row(project: Project, actual: float, task_count: int) -> CostProjectRow:
    usage, _remaining, budget_status = _budget_metrics(project.monthly_cost_budget_usd, actual)
    return CostProjectRow(
        project_id=project.id,
        project_name=project.name,
        budget=_cost(project.monthly_cost_budget_usd)
        if project.monthly_cost_budget_usd is not None
        else None,
        actual_cost=_cost(actual),
        budget_usage=usage,
        budget_status=budget_status,
        task_count=task_count,
    )


async def technical_cost_overview(
    session: AsyncSession,
    *,
    user: User,
    days: int,
) -> TechnicalCostOverviewOut:
    period_end = datetime.now(UTC)
    period_start = period_end - timedelta(days=days)
    calls = list(
        await session.scalars(
            select(LLMCall)
            .where(LLMCall.org_id == user.org_id, LLMCall.created_at >= period_start)
            .order_by(LLMCall.created_at)
        )
    )
    provider_rollups: dict[str, dict[str, Any]] = {}
    model_rollups: dict[tuple[str, str], dict[str, Any]] = {}
    agent_rollups: dict[str, dict[str, Any]] = {}
    daily_rollups: dict[Any, dict[str, Any]] = {}

    for call in calls:
        failed = call.status != "ok"
        provider = provider_rollups.setdefault(call.provider, _technical_bucket())
        model = model_rollups.setdefault((call.provider, call.model), _technical_bucket())
        agent = agent_rollups.setdefault(call.agent_code or "unassigned", _technical_bucket())
        daily = daily_rollups.setdefault(call.created_at.date(), _technical_bucket())
        for bucket in (provider, model, agent, daily):
            bucket["calls"] += 1
            bucket["tokens"] += call.total_tokens
            bucket["cost"] += call.cost_usd
            bucket["failed"] += int(failed)
            bucket["latency"] += call.latency_ms

    total_cost = _cost(sum(call.cost_usd for call in calls))
    failed_calls = sum(call.status != "ok" for call in calls)
    return TechnicalCostOverviewOut(
        period_days=days,
        period_start=period_start,
        period_end=period_end,
        summary=TechnicalCostSummaryOut(
            total_cost=total_cost,
            total_calls=len(calls),
            total_tokens=sum(call.total_tokens for call in calls),
            failed_calls=failed_calls,
            fallback_attempts=failed_calls,
            average_latency_ms=_average_latency(calls),
        ),
        by_provider=[
            TechnicalProviderRow(
                provider=provider,
                calls=row["calls"],
                tokens=row["tokens"],
                cost=_cost(row["cost"]),
                failed_calls=row["failed"],
                average_latency_ms=_bucket_latency(row),
            )
            for provider, row in sorted(
                provider_rollups.items(), key=lambda item: item[1]["cost"], reverse=True
            )
        ],
        by_model=[
            TechnicalModelRow(
                provider=provider,
                model=model,
                calls=row["calls"],
                tokens=row["tokens"],
                cost=_cost(row["cost"]),
                failed_calls=row["failed"],
                average_latency_ms=_bucket_latency(row),
            )
            for (provider, model), row in sorted(
                model_rollups.items(), key=lambda item: item[1]["cost"], reverse=True
            )
        ],
        by_agent=[
            TechnicalAgentRow(
                agent_code=code,
                calls=row["calls"],
                tokens=row["tokens"],
                cost=_cost(row["cost"]),
                failed_calls=row["failed"],
            )
            for code, row in sorted(
                agent_rollups.items(), key=lambda item: item[1]["cost"], reverse=True
            )
        ],
        daily=[
            TechnicalDailyRow(
                date=day,
                calls=row["calls"],
                failed_calls=row["failed"],
                cost=_cost(row["cost"]),
            )
            for day, row in sorted(daily_rollups.items())
        ],
    )


def _technical_bucket() -> dict[str, Any]:
    return {"calls": 0, "tokens": 0, "cost": 0.0, "failed": 0, "latency": 0}


def _bucket_latency(bucket: dict[str, Any]) -> int:
    return round(bucket["latency"] / bucket["calls"]) if bucket["calls"] else 0


def _average_latency(calls: list[LLMCall]) -> int:
    return round(sum(call.latency_ms for call in calls) / len(calls)) if calls else 0
