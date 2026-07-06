"""成本看板 schema：模型调用、Agent 调用、任务维度聚合。"""

from pydantic import BaseModel

from app.models.enums import BrainTaskType


class CostModelRow(BaseModel):
    model: str
    calls: int
    tokens: int
    cost: float


class CostAgentRow(BaseModel):
    agent_code: str
    agent_name: str
    calls: int
    tokens: int
    cost: float


class CostTaskRow(BaseModel):
    task_id: int
    title: str
    type: BrainTaskType
    calls: int
    tokens: int
    cost: float


class CostBrainRow(BaseModel):
    type: BrainTaskType
    tasks: int
    calls: int
    tokens: int
    cost: float


class CostOverviewOut(BaseModel):
    total_cost: float
    total_calls: int
    total_tokens: int
    by_brain: list[CostBrainRow]
    by_model: list[CostModelRow]
    by_agent: list[CostAgentRow]
    by_task: list[CostTaskRow]
