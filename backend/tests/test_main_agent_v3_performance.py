"""Operations Brain V3 latency semantics and model-budget contracts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.llm.adapters import CompletionResult
from app.llm.gateway import LLMGateway
from app.models import (
    Account,
    AgentRun,
    ConversationThread,
    ConversationTurn,
    LLMCall,
)
from app.models.enums import Platform
from app.schemas.conversation import TurnExecutionMode
from app.services.turn_observability import (
    TurnObservabilityScope,
    apply_turn_closure_metrics,
    bind_turn_observability,
    mark_execution_started,
    record_first_user_token,
    record_route_completed,
)
from app.tools import ToolAdapter, ToolExecutionContext, ToolNotAllowedError
from app.worker import _execute_v2_conversation_run


@dataclass
class FakeClock:
    wall: datetime
    monotonic_value: float = 0.0

    def now(self) -> datetime:
        return self.wall

    def monotonic(self) -> float:
        return self.monotonic_value

    def advance(self, milliseconds: int) -> None:
        self.wall += timedelta(milliseconds=milliseconds)
        self.monotonic_value += milliseconds / 1000


@pytest.mark.asyncio
async def test_turn_timing_preserves_first_route_and_first_user_token(
    session,
    admin,
) -> None:
    created_at = datetime(2026, 7, 30, 1, 0, tzinfo=UTC)
    turn = ConversationTurn(
        id=9001,
        thread_id=8001,
        org_id=admin.org_id,
        created_by_id=admin.id,
        user_input="你好",
        created_at=created_at,
        updated_at=created_at,
    )
    session.add(turn)
    # This unit test exercises UPDATE semantics without requiring the full
    # thread lineage; disable FK enforcement for this isolated timing row.
    await session.commit()

    clock = FakeClock(created_at)
    scope = TurnObservabilityScope(
        org_id=admin.org_id,
        thread_id=turn.thread_id,
        turn_id=turn.id,
        run_id=7001,
        turn_created_at=created_at,
    )
    with bind_turn_observability(scope, clock=clock):
        mark_execution_started()
        clock.advance(125)
        await record_route_completed(session)
        clock.advance(875)
        await record_first_user_token(
            session,
            agent_code="01-positioning",
            delta="专家内部 token",
        )
        await record_first_user_token(
            session,
            agent_code="00-decision",
            delta="",
        )
        await record_first_user_token(
            session,
            agent_code="00-decision",
            delta="你",
        )
        clock.advance(500)
        await record_route_completed(session)
        await record_first_user_token(
            session,
            agent_code="00-decision",
            delta="好",
        )

    await session.refresh(turn)
    assert turn.route_ms == 125
    assert turn.first_token_ms == 1000


def test_turn_closure_timing_updates_completion_and_total_on_resume() -> None:
    created_at = datetime(2026, 7, 30, 1, 0, tzinfo=UTC)
    turn = ConversationTurn(
        id=91,
        thread_id=81,
        org_id=71,
        user_input="继续",
        route_ms=20,
        first_token_ms=200,
        completion_ms=600,
        total_ms=700,
        created_at=created_at,
        updated_at=created_at,
    )

    apply_turn_closure_metrics(
        turn,
        now=created_at + timedelta(milliseconds=1600),
        writes_user_message=True,
    )

    assert turn.route_ms == 20
    assert turn.first_token_ms == 200
    assert turn.completion_ms == 1600
    assert turn.total_ms == 1600


def test_turn_closure_without_user_message_only_updates_paused_total() -> None:
    created_at = datetime(2026, 7, 30, 1, 0, tzinfo=UTC)
    turn = ConversationTurn(
        id=92,
        thread_id=82,
        org_id=72,
        user_input="执行",
        created_at=created_at,
        updated_at=created_at,
    )

    apply_turn_closure_metrics(
        turn,
        now=created_at + timedelta(milliseconds=950),
        writes_user_message=False,
    )

    assert turn.completion_ms is None
    assert turn.total_ms == 950


@pytest.mark.asyncio
async def test_tool_attempts_are_counted_for_the_active_turn(session, admin) -> None:
    turn, run = await _worker_turn(
        session,
        admin,
        key="tool-attempt-count",
        message="调用一个不可用工具",
    )
    scope = TurnObservabilityScope(
        org_id=admin.org_id,
        thread_id=turn.thread_id,
        turn_id=turn.id,
        run_id=run.id,
        turn_created_at=turn.created_at,
    )

    with bind_turn_observability(scope):
        with pytest.raises(ToolNotAllowedError):
            await ToolAdapter().invoke(
                "missing.tool",
                {},
                ToolExecutionContext(session=session, user=admin),
            )
        await session.commit()

    await session.refresh(turn)
    assert turn.tool_call_count == 1


class BudgetAdapter:
    provider = "deepseek"

    def __init__(self, *, classification: dict | None = None) -> None:
        self.classification = classification

    async def complete(self, model, messages, options=None):
        del messages, options
        content = (
            json.dumps(self.classification, ensure_ascii=False)
            if self.classification is not None
            else "模型答复"
        )
        return CompletionResult(content, model, 3, 4, 7)

    async def stream(self, model, messages, options=None):
        del messages, options
        yield "模型"
        yield "答复"


async def _worker_turn(session, admin, *, key: str, message: str):
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname=f"预算账号-{key}",
    )
    session.add(account)
    await session.flush()
    thread = ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title=message,
    )
    turn = ConversationTurn(
        thread=thread,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id=key,
        user_input=message,
        status="running",
    )
    session.add_all([thread, turn])
    await session.flush()
    run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        thread_id=thread.id,
        turn_id=turn.id,
        client_message_id=key,
        status="running",
        phase="running",
        request_payload={
            "client_message_id": key,
            "message": message,
            "thread_id": thread.id,
            "turn_id": turn.id,
        },
    )
    session.add(run)
    await session.commit()
    return turn, run


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["你好", "你能做什么"])
async def test_deterministic_answer_budget_is_zero_router_one_answer(
    session,
    admin,
    monkeypatch,
    message: str,
) -> None:
    turn, run = await _worker_turn(
        session,
        admin,
        key=f"answer-budget-{len(message)}",
        message=message,
    )
    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.gateway",
        LLMGateway(adapters={"deepseek": BudgetAdapter()}),
    )

    result = await __import__("asyncio").wait_for(
        _execute_v2_conversation_run(
            session,
            run=run,
            worker_id="budget-worker",
        ),
        timeout=5,
    )

    assert result.mode is TurnExecutionMode.ANSWER
    calls = list(
        await session.scalars(
            select(LLMCall).where(LLMCall.org_id == admin.org_id).order_by(LLMCall.id)
        )
    )
    assert [call.agent_code for call in calls] == ["00-decision"]
    await session.refresh(turn)
    assert turn.model_call_count == 1


@pytest.mark.asyncio
async def test_fuzzy_answer_budget_is_one_router_plus_one_answer(
    session,
    admin,
    monkeypatch,
) -> None:
    turn, run = await _worker_turn(
        session,
        admin,
        key="fuzzy-budget",
        message="我最近有点迷茫，帮我看看下一步",
    )
    classification = {
        "mode": "answer",
        "intent": "general_question",
        "confidence": 0.82,
        "reason": "需要模型理解开放请求。",
        "requires_account_context": False,
        "requires_operation_task": False,
    }
    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.gateway",
        LLMGateway(
            adapters={
                "deepseek": BudgetAdapter(classification=classification),
            }
        ),
    )

    result = await __import__("asyncio").wait_for(
        _execute_v2_conversation_run(
            session,
            run=run,
            worker_id="fuzzy-budget-worker",
        ),
        timeout=5,
    )

    assert result.mode is TurnExecutionMode.ANSWER
    calls = list(
        await session.scalars(
            select(LLMCall).where(LLMCall.org_id == admin.org_id).order_by(LLMCall.id)
        )
    )
    assert [call.agent_code for call in calls] == ["00-router", "00-decision"]
    await session.refresh(turn)
    assert turn.model_call_count == 2
