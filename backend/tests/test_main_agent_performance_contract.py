"""Local, network-free performance contracts for the main-agent hot paths."""

from __future__ import annotations

from math import ceil
from time import perf_counter_ns

import httpx
import pytest
from pydantic import BaseModel

from app.models import Account
from app.models.enums import AccountStatus, Platform
from app.orchestrator.capability_router import route_deterministic_request
from app.orchestrator.runtime_tools import build_runtime_tool_adapter
from app.orchestrator.skills.registry import SkillRegistry
from app.schemas.conversation import TurnExecutionMode
from app.schemas.skills import SkillDefinition
from app.tools import ToolExecutionContext


class _AccountInspectionInput(BaseModel):
    pass


class _AccountInspectionOutput(BaseModel):
    summary: str


def _registry() -> SkillRegistry:
    return SkillRegistry(
        [
            SkillDefinition(
                code="account_inspection",
                version=1,
                name="账号体检",
                description="诊断当前账号",
                supported_platforms=frozenset({"douyin"}),
                input_model=_AccountInspectionInput,
                output_model=_AccountInspectionOutput,
                expert_codes=("06-operator",),
                tool_codes=("account.data_context",),
                risk_level="low",
                approval_policy="none",
                artifact_type="account_inspection_report",
            )
        ]
    )


def _p95_ms(samples_ns: list[int]) -> float:
    ordered = sorted(samples_ns)
    index = max(0, ceil(len(ordered) * 0.95) - 1)
    return ordered[index] / 1_000_000


@pytest.mark.parametrize(
    ("message", "expected_mode", "budget_ms"),
    [
        ("给当前账号做一次体检", TurnExecutionMode.SKILL, 50),
        ("我现在的账号有数据吗？", TurnExecutionMode.QUERY, 100),
        ("最近30天播放量是多少？", TurnExecutionMode.QUERY, 100),
        ("你能做什么？", TurnExecutionMode.ANSWER, 100),
    ],
)
def test_deterministic_routes_meet_local_p95_budget(
    message: str,
    expected_mode: TurnExecutionMode,
    budget_ms: int,
) -> None:
    """Clear requests must never pay model/network latency before routing."""

    registry = _registry()
    samples: list[int] = []
    for _ in range(200):
        started = perf_counter_ns()
        decision = route_deterministic_request(
            message,
            platform="douyin",
            registry=registry,
            has_account=True,
        )
        samples.append(perf_counter_ns() - started)
        assert decision is not None
        assert decision.mode is expected_mode

    assert _p95_ms(samples) < budget_ms


@pytest.mark.asyncio
async def test_account_query_tool_meets_local_p95_budget_without_network(
    session,
    admin,
    monkeypatch,
) -> None:
    """The common account query is a local DB read and must stay below two seconds."""

    async def reject_network(*_args, **_kwargs):
        raise AssertionError("account.data_context must not call an external network")

    monkeypatch.setattr(httpx.AsyncClient, "request", reject_network)
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="性能门禁账号",
        status=AccountStatus.ACTIVE,
        auth={},
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)

    adapter = build_runtime_tool_adapter()
    context = ToolExecutionContext(session=session, user=admin, account_id=account.id)
    samples: list[int] = []
    for _ in range(10):
        started = perf_counter_ns()
        result = await adapter.invoke("account.data_context", {"days": 30}, context)
        samples.append(perf_counter_ns() - started)
        assert result["account_id"] == account.id
        assert result["data_status"] == "empty"

    assert _p95_ms(samples) < 2_000
