"""成本看板 API：聚合模型调用、子 Agent 调用和运营大脑任务成本。"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.db import get_session
from app.models import AgentInvocation, BrainTask, LLMCall
from app.schemas.costs import CostAgentRow, CostBrainRow, CostModelRow, CostOverviewOut, CostTaskRow

router = APIRouter(prefix="/costs", tags=["costs"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _round_cost(value: float | int | None) -> float:
    return round(float(value or 0), 4)


@router.get("/overview", response_model=CostOverviewOut)
async def get_cost_overview(user: CurrentUser, session: SessionDep) -> CostOverviewOut:
    model_rows = (
        await session.execute(
            select(
                LLMCall.model,
                func.count(LLMCall.id),
                func.coalesce(func.sum(LLMCall.total_tokens), 0),
                func.coalesce(func.sum(LLMCall.cost_usd), 0),
            )
            .where(LLMCall.org_id == user.org_id)
            .group_by(LLMCall.model)
            .order_by(func.sum(LLMCall.cost_usd).desc())
        )
    ).all()
    agent_rows = (
        await session.execute(
            select(
                AgentInvocation.agent_code,
                AgentInvocation.agent_name,
                func.count(AgentInvocation.id),
                func.coalesce(func.sum(AgentInvocation.token_count), 0),
                func.coalesce(func.sum(AgentInvocation.cost), 0),
            )
            .join(BrainTask, AgentInvocation.task_id == BrainTask.id)
            .where(BrainTask.org_id == user.org_id)
            .group_by(AgentInvocation.agent_code, AgentInvocation.agent_name)
            .order_by(func.sum(AgentInvocation.cost).desc())
        )
    ).all()
    task_rows = (
        await session.execute(
            select(
                BrainTask.id,
                BrainTask.title,
                BrainTask.type,
                func.count(AgentInvocation.id),
                func.coalesce(func.sum(AgentInvocation.token_count), 0),
                func.coalesce(func.sum(AgentInvocation.cost), 0),
            )
            .join(AgentInvocation, AgentInvocation.task_id == BrainTask.id)
            .where(BrainTask.org_id == user.org_id)
            .group_by(BrainTask.id, BrainTask.title, BrainTask.type)
            .order_by(func.sum(AgentInvocation.cost).desc())
        )
    ).all()
    brain_rows = (
        await session.execute(
            select(
                BrainTask.type,
                func.count(func.distinct(BrainTask.id)),
                func.count(AgentInvocation.id),
                func.coalesce(func.sum(AgentInvocation.token_count), 0),
                func.coalesce(func.sum(AgentInvocation.cost), 0),
            )
            .join(AgentInvocation, AgentInvocation.task_id == BrainTask.id)
            .where(BrainTask.org_id == user.org_id)
            .group_by(BrainTask.type)
            .order_by(func.sum(AgentInvocation.cost).desc())
        )
    ).all()

    return CostOverviewOut(
        total_cost=_round_cost(sum(row[3] for row in model_rows)),
        total_calls=sum(int(row[1]) for row in model_rows),
        total_tokens=sum(int(row[2]) for row in model_rows),
        by_brain=[
            CostBrainRow(
                type=task_type,
                tasks=int(tasks),
                calls=int(calls),
                tokens=int(tokens),
                cost=_round_cost(cost),
            )
            for task_type, tasks, calls, tokens, cost in brain_rows
        ],
        by_model=[
            CostModelRow(model=model, calls=int(calls), tokens=int(tokens), cost=_round_cost(cost))
            for model, calls, tokens, cost in model_rows
        ],
        by_agent=[
            CostAgentRow(
                agent_code=str(agent_code),
                agent_name=agent_name,
                calls=int(calls),
                tokens=int(tokens),
                cost=_round_cost(cost),
            )
            for agent_code, agent_name, calls, tokens, cost in agent_rows
        ],
        by_task=[
            CostTaskRow(
                task_id=task_id,
                title=title,
                type=task_type,
                calls=int(calls),
                tokens=int(tokens),
                cost=_round_cost(cost),
            )
            for task_id, title, task_type, calls, tokens, cost in task_rows
        ],
    )
