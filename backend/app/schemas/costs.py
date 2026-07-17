"""Role-aware cost workspace contracts."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from app.models.enums import BrainTaskType

BudgetStatus = Literal["no_budget", "healthy", "warning", "exceeded"]


class CostScopeOut(BaseModel):
    client_id: int
    client_name: str
    project_id: int | None
    project_name: str | None
    period_days: int
    period_start: datetime
    period_end: datetime


class BusinessCostSummaryOut(BaseModel):
    actual_cost: float
    budget: float | None
    budget_usage: float | None
    remaining_budget: float | None
    task_count: int
    agent_calls: int
    tool_calls: int
    failed_operations: int
    budget_status: BudgetStatus


class CostProjectRow(BaseModel):
    project_id: int
    project_name: str
    budget: float | None
    actual_cost: float
    budget_usage: float | None
    budget_status: BudgetStatus
    task_count: int


class CostAgentRow(BaseModel):
    agent_code: str
    agent_name: str
    calls: int
    cost: float
    failed_calls: int


class CostTaskRow(BaseModel):
    task_id: int
    title: str
    type: BrainTaskType
    status: str
    agent_calls: int
    tool_calls: int
    cost: float


class CostToolRow(BaseModel):
    tool_code: str
    tool_name: str
    calls: int
    cost: float
    failed_calls: int


class CostDailyRow(BaseModel):
    date: date
    cost: float


class CostOverviewOut(BaseModel):
    scope: CostScopeOut
    summary: BusinessCostSummaryOut
    by_project: list[CostProjectRow]
    by_agent: list[CostAgentRow]
    by_task: list[CostTaskRow]
    by_tool: list[CostToolRow]
    daily: list[CostDailyRow]


class TechnicalCostSummaryOut(BaseModel):
    total_cost: float
    total_calls: int
    total_tokens: int
    failed_calls: int
    fallback_attempts: int
    average_latency_ms: int


class TechnicalProviderRow(BaseModel):
    provider: str
    calls: int
    tokens: int
    cost: float
    failed_calls: int
    average_latency_ms: int


class TechnicalModelRow(BaseModel):
    provider: str
    model: str
    calls: int
    tokens: int
    cost: float
    failed_calls: int
    average_latency_ms: int


class TechnicalAgentRow(BaseModel):
    agent_code: str
    calls: int
    tokens: int
    cost: float
    failed_calls: int


class TechnicalDailyRow(BaseModel):
    date: date
    calls: int
    failed_calls: int
    cost: float


class TechnicalCostOverviewOut(BaseModel):
    period_days: int
    period_start: datetime
    period_end: datetime
    summary: TechnicalCostSummaryOut
    by_provider: list[TechnicalProviderRow]
    by_model: list[TechnicalModelRow]
    by_agent: list[TechnicalAgentRow]
    daily: list[TechnicalDailyRow]
