"""Cross-intent regression coverage for one Main Agent V2 Thread."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.core.security import create_access_token
from app.models import (
    Account,
    AgentRun,
    BrainTask,
    ContentItem,
    ConversationTurn,
    Deliverable,
    OrchestrationPlan,
    SkillRun,
    StrategyPlan,
    TaskBrief,
)
from app.models.enums import (
    AccountStatus,
    BrainTaskStatus,
    BrainTaskType,
    DeliverableStatus,
    DeliverableType,
    Platform,
)
from app.orchestrator.skill_runtime import SkillExecutionResult
from app.schemas.conversation import (
    CreateConversationTurnRequest,
    TurnExecutionMode,
    TurnRouteDecision,
)
from app.services.turn_execution import execute_conversation_turn


def _auth(user) -> dict[str, str]:
    token = create_access_token(str(user.id), user.role.value)
    return {"Authorization": f"Bearer {token}"}


def _decision(mode: TurnExecutionMode, **overrides: Any) -> TurnRouteDecision:
    values: dict[str, Any] = {
        "mode": mode,
        "intent": f"flow_{mode.value}",
        "confidence": 0.99,
        "reason": "deterministic cross-intent regression route",
        "requires_account_context": mode
        in {
            TurnExecutionMode.QUERY,
            TurnExecutionMode.SKILL,
            TurnExecutionMode.TASK,
            TurnExecutionMode.ACTION,
        },
        "requires_operation_task": mode
        in {
            TurnExecutionMode.SKILL,
            TurnExecutionMode.TASK,
            TurnExecutionMode.ACTION,
        },
    }
    values.update(overrides)
    return TurnRouteDecision(**values)


def _turn_payload(
    client_message_id: str,
    message: str,
    *,
    requested_skill_code: str | None = None,
) -> dict[str, Any]:
    return {
        "client_message_id": client_message_id,
        "message": message,
        "requested_skill_code": requested_skill_code,
        "execution_preference": "AUTO",
        "attachment_ids": [],
    }


@pytest.mark.asyncio
async def test_main_agent_v2_cross_intent_flow_preserves_turn_ownership(
    client,
    session,
    admin,
    monkeypatch,
) -> None:
    """A greeting after an Artifact must not steal or duplicate its provenance."""

    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    monkeypatch.setattr(settings, "agent_runtime_async_enabled", False)
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="Main Agent V2 regression account",
        status=AccountStatus.ACTIVE,
        auth={"auth_status": "manual", "data_sync_status": "manual"},
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)

    create_thread = await client.post(
        "/brain/conversations",
        headers=_auth(admin),
        json={"account_id": account.id, "title": "Main Agent V2 cross-intent flow"},
    )
    assert create_thread.status_code == 201, create_thread.text
    thread_id = create_thread.json()["id"]

    routes = {
        "你好": _decision(
            TurnExecutionMode.ANSWER,
            requires_account_context=False,
            requires_operation_task=False,
        ),
        "查看最近七天数据": _decision(
            TurnExecutionMode.QUERY,
            skill_code="account_data_query",
            requires_operation_task=False,
        ),
        "解释体检报告": _decision(
            TurnExecutionMode.ANSWER,
            requires_account_context=False,
            requires_operation_task=False,
        ),
        "制定 30 天策略": _decision(TurnExecutionMode.TASK),
        "继续普通对话": _decision(
            TurnExecutionMode.ANSWER,
            requires_account_context=False,
            requires_operation_task=False,
        ),
    }

    async def classify(_session, _org_id, message, **_kwargs):
        return routes[message]

    class QueryAdapter:
        async def invoke(self, name, params, context):
            assert name == "account.data_context"
            assert params == {"days": 30}
            assert context.account_id == account.id
            return {
                "account_id": account.id,
                "period": {"days": 7},
                "metrics": {"play": {"value": 1200}},
                "sources": [{"kind": "fixture", "id": 7}],
            }

    async def execute_inspection(
        runtime_session,
        *,
        user,
        thread,
        turn,
        run,
        skill_code,
        days,
    ):
        assert skill_code == "account_inspection"
        assert days == 30
        content = ContentItem(
            account_id=thread.account_id,
            created_by_id=user.id,
            title="账号体检报告",
        )
        runtime_session.add(content)
        await runtime_session.flush()
        task = BrainTask(
            org_id=user.org_id,
            created_by_id=user.id,
            content_item_id=content.id,
            title="一键账号体检",
            type=BrainTaskType.ACCOUNT_DIAGNOSIS,
            status=BrainTaskStatus.COMPLETED,
            progress=100,
            current_focus="账号体检已完成",
            runtime_mode="skill",
        )
        task.brief = TaskBrief(
            goal=turn.user_input,
            project_id=thread.project_id,
            project_name=None,
            account_group_id=None,
            account_group_name=None,
            platforms=["douyin"],
            account_ids=[thread.account_id],
            cycle="current_turn",
            budget=None,
            content_goal=turn.user_input,
            risk_constraints=[],
            expected_outputs=["account_inspection_report"],
            confirmation_actions=[],
        )
        task.plan = OrchestrationPlan(
            summary="Execute account inspection",
            steps=[],
            quality_gates=[],
            estimated_cost=Decimal("0"),
            requires_human_confirmation=False,
        )
        runtime_session.add(task)
        await runtime_session.flush()
        run.task_id = task.id
        skill_run = SkillRun(
            org_id=user.org_id,
            thread_id=thread.id,
            turn_id=turn.id,
            run_id=run.id,
            task_id=task.id,
            idempotency_key="skill:account_inspection:v1",
            skill_code="account_inspection",
            skill_version=1,
            status="completed",
            input_snapshot={"account_id": thread.account_id, "days": days},
            output_snapshot={"status": "completed"},
        )
        runtime_session.add(skill_run)
        await runtime_session.flush()
        artifact = Deliverable(
            content_item_id=content.id,
            thread_id=thread.id,
            turn_id=turn.id,
            run_id=run.id,
            skill_run_id=skill_run.id,
            agent_code="02-content-director",
            type=DeliverableType.REVIEW_REPORT,
            version=1,
            status=DeliverableStatus.APPROVED,
            payload={
                "artifact_type": "account_inspection_report",
                "summary": "账号具备增长基础。",
                "period": "最近 30 天",
                "findings": ["完播率仍有提升空间"],
                "recommendations": ["收敛内容主题"],
            },
        )
        runtime_session.add(artifact)
        await runtime_session.commit()
        await runtime_session.refresh(artifact)
        return SkillExecutionResult(
            status="completed",
            skill_run_id=skill_run.id,
            task_id=task.id,
            artifact_id=artifact.id,
            artifact_type="account_inspection_report",
            report={"summary": "账号具备增长基础。"},
            response="账号体检已完成。",
        )

    async def start_strategy(runtime_session, task, **kwargs):
        run = await runtime_session.get(AgentRun, kwargs["agent_run_id"])
        assert run is not None
        assert run.task_id == task.id
        strategy = StrategyPlan(
            org_id=task.org_id,
            task_id=task.id,
            run_id=run.id,
            thread_id=run.thread_id,
            turn_id=run.turn_id,
            account_id=account.id,
            created_by_id=admin.id,
            status="draft",
            version=1,
            goal="制定 30 天策略",
            strategy={"period_days": 30},
        )
        runtime_session.add(strategy)
        task.status = BrainTaskStatus.COMPLETED
        task.progress = 100
        task.current_focus = "30 天策略已生成"
        await runtime_session.commit()
        return task

    async def completed_status(*_args, **_kwargs):
        return "completed"

    monkeypatch.setattr(
        "app.services.turn_execution.brain_intelligence.classify_turn",
        classify,
    )
    monkeypatch.setattr(
        "app.services.turn_execution.build_runtime_tool_adapter",
        QueryAdapter,
    )
    monkeypatch.setattr(
        "app.services.turn_execution.skill_runtime.execute",
        execute_inspection,
    )
    monkeypatch.setattr(
        "app.services.turn_execution.runtime_graph.start_routed",
        start_strategy,
    )
    monkeypatch.setattr(
        "app.services.turn_execution.runtime_status",
        completed_status,
    )

    async def enqueue_agent_runtime(*, run_id: int) -> None:
        del run_id

    monkeypatch.setattr(
        "app.api.conversations.enqueue_agent_runtime",
        enqueue_agent_runtime,
    )

    requests = [
        ("flow-1", "你好", None),
        ("flow-2", "查看最近七天数据", None),
        ("flow-3", "一键账号体检", "account_inspection"),
        ("flow-4", "解释体检报告", None),
        ("flow-5", "制定 30 天策略", None),
        ("flow-6", "继续普通对话", None),
    ]
    for client_message_id, message, requested_skill_code in requests:
        response = await client.post(
            f"/brain/conversations/{thread_id}/turns",
            headers=_auth(admin),
            json=_turn_payload(
                client_message_id,
                message,
                requested_skill_code=requested_skill_code,
            ),
        )
        assert response.status_code == 202, response.text
        payload = response.json()
        queued_turn = await session.get(ConversationTurn, payload["turn"]["id"])
        queued_run = await session.get(AgentRun, payload["run"]["id"])
        assert queued_turn is not None
        assert queued_run is not None
        await execute_conversation_turn(
            session,
            admin,
            queued_turn,
            queued_run,
            CreateConversationTurnRequest(
                client_message_id=client_message_id,
                message=message,
                requested_skill_code=requested_skill_code,
            ),
        )

    turns = list(
        await session.scalars(
            select(ConversationTurn)
            .where(ConversationTurn.thread_id == thread_id)
            .options(selectinload(ConversationTurn.agent_runs))
            .order_by(ConversationTurn.id)
        )
    )
    inspection_artifact = await session.scalar(select(Deliverable))
    inspection_skill_run = await session.scalar(
        select(SkillRun).where(SkillRun.turn_id == turns[2].id)
    )
    explanation_artifact_count = await session.scalar(
        select(func.count(Deliverable.id)).where(
            Deliverable.turn_id == turns[3].id,
        )
    )
    strategy = await session.scalar(select(StrategyPlan).where(StrategyPlan.turn_id == turns[4].id))

    assert len(turns) == 6
    assert [len(turn.agent_runs) for turn in turns] == [1, 1, 1, 1, 1, 1]
    assert turns[0].agent_runs[0].task_id is None
    assert turns[1].agent_runs[0].task_id is None
    assert inspection_artifact is not None
    assert inspection_artifact.turn_id == turns[2].id
    assert inspection_skill_run is not None
    assert inspection_artifact.skill_run_id == inspection_skill_run.id
    assert inspection_skill_run.task_id == turns[2].agent_runs[0].task_id
    assert turns[2].agent_runs[0].task_id is not None
    assert explanation_artifact_count == 0
    assert turns[3].agent_runs[0].task_id is None
    assert strategy is not None
    assert strategy.task_id == turns[4].agent_runs[0].task_id
    assert turns[5].agent_runs[0].task_id is None
    assert await session.scalar(select(func.count(Deliverable.id))) == 1
