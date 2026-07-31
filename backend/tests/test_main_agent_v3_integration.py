"""Real API-to-worker integration matrix for Operations Brain V3."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.core.security import create_access_token
from app.llm.adapters import CompletionResult
from app.llm.gateway import LLMGateway
from app.models import (
    AgentInvocation,
    AgentRun,
    AgentToolCall,
    BrainTask,
    ContentItem,
    ConversationTurn,
    Deliverable,
    LLMCall,
    OrchestrationPlan,
    SkillRun,
)
from app.models.enums import (
    BrainTaskStatus,
    BrainTaskType,
    ContentStage,
    ContentStatus,
    DeliverableStatus,
    DeliverableType,
)
from app.orchestrator.skill_runtime import SkillExecutionResult, skill_input_hash
from app.schemas.conversation import TurnExecutionMode, TurnRouteDecision
from app.worker import _execute_v2_conversation_run

TERMINAL_OR_PAUSED = {
    "blocked",
    "completed",
    "failed",
    "waiting_decision",
    "waiting_permission",
    "waiting_user",
}


@pytest.fixture(autouse=True)
def _integration_runtime(monkeypatch):
    async def no_queue(*, run_id: int) -> None:
        del run_id

    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    monkeypatch.setattr("app.api.conversations.enqueue_agent_runtime", no_queue)


def _auth(user) -> dict[str, str]:
    token = create_access_token(str(user.id), user.role.value)
    return {"Authorization": f"Bearer {token}"}


class DeterministicConversationAdapter:
    provider = "v3-integration-test"

    async def complete(self, model, messages, options=None):
        del messages, options
        return CompletionResult(
            content="Operations Brain deterministic integration answer.",
            model=model,
            prompt_tokens=4,
            completion_tokens=6,
            total_tokens=10,
        )

    async def stream(self, model, messages, options=None):
        del model, messages, options
        yield "Operations Brain "
        yield "deterministic integration answer."


@dataclass
class DeterministicExecutionFixture:
    failed_skills: set[str] = field(default_factory=set)
    query_accounts: list[int] = field(default_factory=list)

    async def skill_execute(self, session, **kwargs) -> SkillExecutionResult:
        user = kwargs["user"]
        thread = kwargs["thread"]
        turn = kwargs["turn"]
        run = kwargs["run"]
        skill_code = kwargs["skill_code"]
        content = ContentItem(
            created_by_id=user.id,
            account_id=thread.account_id,
            title=f"{skill_code} artifact",
            current_stage=ContentStage.OPERATION,
            status=ContentStatus.DRAFT,
        )
        session.add(content)
        await session.flush()
        task = BrainTask(
            org_id=user.org_id,
            created_by_id=user.id,
            content_item_id=content.id,
            title=turn.user_input,
            type=BrainTaskType.REVIEW_OPTIMIZATION,
            status=(
                BrainTaskStatus.FAILED
                if skill_code in self.failed_skills
                else BrainTaskStatus.COMPLETED
            ),
            current_focus=f"{skill_code} deterministic executor",
            runtime_mode="v3-integration-test",
        )
        session.add(task)
        await session.flush()
        run.task_id = task.id
        frozen_input = {"account_id": thread.account_id, "days": 30}
        skill_run = SkillRun(
            org_id=user.org_id,
            thread_id=thread.id,
            turn_id=turn.id,
            run_id=run.id,
            task_id=task.id,
            idempotency_key=f"v3-integration:{skill_code}",
            skill_code=skill_code,
            skill_version=1,
            status="failed" if skill_code in self.failed_skills else "completed",
            input_snapshot=frozen_input,
            input_hash=skill_input_hash(frozen_input),
            output_snapshot={"artifact_type": f"{skill_code}_report"},
            quality_score=Decimal("0.9100"),
            error_code=("EXPERT_EXECUTION_FAILED" if skill_code in self.failed_skills else None),
        )
        session.add(skill_run)
        await session.flush()
        if skill_code in self.failed_skills:
            await session.commit()
            return SkillExecutionResult(
                status="failed",
                skill_run_id=skill_run.id,
                task_id=task.id,
                artifact_id=None,
                artifact_type=f"{skill_code}_report",
                report={},
                response="The deterministic expert failed safely.",
                error_code="EXPERT_EXECUTION_FAILED",
            )
        deliverable = Deliverable(
            content_item_id=content.id,
            thread_id=thread.id,
            turn_id=turn.id,
            run_id=run.id,
            skill_run_id=skill_run.id,
            agent_code="06-operation",
            type=DeliverableType.REVIEW_REPORT,
            version=1,
            status=DeliverableStatus.PENDING_REVIEW,
            payload={"title": f"{skill_code} report"},
        )
        session.add(deliverable)
        await session.flush()
        skill_run.output_snapshot = {
            "artifact_id": deliverable.id,
            "artifact_type": f"{skill_code}_report",
        }
        await session.commit()
        return SkillExecutionResult(
            status="completed",
            skill_run_id=skill_run.id,
            task_id=task.id,
            artifact_id=deliverable.id,
            artifact_type=f"{skill_code}_report",
            report={"title": f"{skill_code} report"},
            response=f"{skill_code} completed.",
        )

    def query_adapter(self):
        fixture = self

        class Adapter:
            async def invoke(self, name, params, context):
                assert name == "account.data_context"
                assert params == {"days": 30}
                fixture.query_accounts.append(context.account_id)
                return {
                    "account_id": context.account_id,
                    "period": {"days": 30},
                    "metrics": {},
                    "sources": [],
                    "coverage": {},
                }

        return Adapter()


async def _create_account_and_thread(client, admin, *, nickname: str):
    account = await client.post(
        "/accounts",
        headers=_auth(admin),
        json={"nickname": nickname, "platform": "douyin"},
    )
    assert account.status_code == 201
    thread = await client.post(
        "/brain/conversations",
        headers=_auth(admin),
        json={"account_id": account.json()["id"], "title": nickname},
    )
    assert thread.status_code == 201
    return account.json(), thread.json()


async def _submit_execute_and_poll(
    client,
    session,
    admin,
    *,
    thread_id: int,
    key: str,
    message: str,
    requested_skill_code: str | None = None,
):
    submitted = await client.post(
        f"/brain/conversations/{thread_id}/turns",
        headers=_auth(admin),
        json={
            "client_message_id": key,
            "message": message,
            "requested_skill_code": requested_skill_code,
        },
    )
    assert submitted.status_code == 202
    submitted_body = submitted.json()
    run = await session.get(AgentRun, submitted_body["run"]["id"])
    assert run is not None
    await asyncio.wait_for(
        _execute_v2_conversation_run(
            session,
            run=run,
            worker_id=f"integration-worker:{key}",
        ),
        timeout=5,
    )
    for _ in range(25):
        response = await client.get(
            f"/brain/turns/{submitted_body['turn']['id']}",
            headers=_auth(admin),
        )
        assert response.status_code == 200
        if response.json()["status"] in TERMINAL_OR_PAUSED:
            return response.json(), run
        await asyncio.sleep(0.01)
    raise AssertionError("Turn did not reach a terminal or paused state")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "message"),
    [
        ("greeting", "你好"),
        ("capability", "你能做什么"),
    ],
)
async def test_real_worker_matrix_answers_without_dispatch(
    client, session, admin, monkeypatch, key: str, message: str
) -> None:
    _, thread = await _create_account_and_thread(client, admin, nickname=f"answer-{key}")
    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.gateway",
        LLMGateway(adapters={"deepseek": DeterministicConversationAdapter()}),
    )

    turn, _run = await _submit_execute_and_poll(
        client,
        session,
        admin,
        thread_id=thread["id"],
        key=f"matrix-{key}",
        message=message,
    )

    assert turn["intent"]["mode"] == "answer"
    assert turn["status"] == "completed"
    assert turn["model_call_count"] == 1
    assert await session.scalar(select(func.count(BrainTask.id))) == 0
    assert await session.scalar(select(func.count(AgentInvocation.id))) == 0


@pytest.mark.asyncio
async def test_real_worker_matrix_queries_only_the_thread_account(
    client, session, admin, monkeypatch
) -> None:
    account, thread = await _create_account_and_thread(client, admin, nickname="query")
    fixture = DeterministicExecutionFixture()
    monkeypatch.setattr(
        "app.services.turn_execution.build_runtime_tool_adapter",
        fixture.query_adapter,
    )

    turn, _run = await _submit_execute_and_poll(
        client,
        session,
        admin,
        thread_id=thread["id"],
        key="matrix-query",
        message="只查询账号数据，不生成策略",
    )

    assert turn["intent"]["mode"] == "query"
    assert turn["status"] == "completed"
    assert turn["model_call_count"] == 0
    assert fixture.query_accounts == [account["id"]]
    assert turn["projections"][0]["type"] == "account_data"
    assert turn["projections"][0]["account_id"] == account["id"]
    assert await session.scalar(select(func.count(BrainTask.id))) == 0
    assert await session.scalar(select(func.count(AgentInvocation.id))) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "skill_code",
    [
        "account_inspection",
        "performance_review",
        "topic_planning",
        "publishing_preparation",
    ],
)
async def test_real_worker_matrix_persists_skill_artifact(
    client, session, admin, monkeypatch, skill_code: str
) -> None:
    account, thread = await _create_account_and_thread(
        client,
        admin,
        nickname=f"skill-{skill_code}",
    )
    fixture = DeterministicExecutionFixture()
    monkeypatch.setattr(
        "app.services.turn_execution.skill_runtime.execute",
        fixture.skill_execute,
    )

    turn, run = await _submit_execute_and_poll(
        client,
        session,
        admin,
        thread_id=thread["id"],
        key=f"matrix-{skill_code}",
        message=f"execute {skill_code}",
        requested_skill_code=skill_code,
    )

    assert turn["intent"]["mode"] == "skill"
    assert turn["intent"]["skill_code"] == skill_code
    assert turn["status"] == "completed"
    assert turn["model_call_count"] == 0
    artifact = next(item for item in turn["projections"] if item["type"] == "artifact")
    assert artifact["account_id"] == account["id"]
    assert await session.get(Deliverable, artifact["artifact_id"]) is not None
    assert (
        await session.scalar(select(func.count(SkillRun.id)).where(SkillRun.run_id == run.id)) == 1
    )


@pytest.mark.asyncio
async def test_real_worker_matrix_pauses_action_for_permission(
    client, session, admin, monkeypatch
) -> None:
    _, thread = await _create_account_and_thread(client, admin, nickname="approval")

    async def classify(*_args, **_kwargs):
        return TurnRouteDecision(
            mode=TurnExecutionMode.ACTION,
            intent="publish_action",
            confidence=1,
            reason="deterministic integration action",
            requires_account_context=True,
            requires_operation_task=True,
        )

    async def start_routed(*_args, **_kwargs):
        return None

    async def waiting_permission(*_args, **_kwargs):
        return "waiting_permission"

    monkeypatch.setattr(
        "app.services.turn_execution.brain_intelligence.classify_turn",
        classify,
    )
    monkeypatch.setattr(
        "app.services.turn_execution.runtime_graph.start_routed",
        start_routed,
    )
    monkeypatch.setattr("app.services.turn_execution.runtime_status", waiting_permission)

    turn, run = await _submit_execute_and_poll(
        client,
        session,
        admin,
        thread_id=thread["id"],
        key="matrix-action-approval",
        message="publish the prepared content now",
    )

    assert turn["intent"]["mode"] == "action"
    assert turn["status"] == "waiting_permission"
    assert turn["model_call_count"] == 0
    task = await session.get(BrainTask, run.task_id)
    assert task is not None
    assert task.content_item_id is not None
    plan = await session.scalar(
        select(OrchestrationPlan).where(OrchestrationPlan.task_id == task.id)
    )
    assert plan is not None
    assert plan.requires_human_confirmation is True


@pytest.mark.asyncio
async def test_real_worker_matrix_closes_expert_failure_without_artifact(
    client, session, admin, monkeypatch
) -> None:
    _, thread = await _create_account_and_thread(client, admin, nickname="failure")
    fixture = DeterministicExecutionFixture(failed_skills={"account_inspection"})
    monkeypatch.setattr(
        "app.services.turn_execution.skill_runtime.execute",
        fixture.skill_execute,
    )

    turn, run = await _submit_execute_and_poll(
        client,
        session,
        admin,
        thread_id=thread["id"],
        key="matrix-expert-failure",
        message="run account inspection with deterministic failure",
        requested_skill_code="account_inspection",
    )

    assert turn["status"] == "failed"
    assert not any(item["type"] == "artifact" for item in turn["projections"])
    skill_run = await session.scalar(select(SkillRun).where(SkillRun.run_id == run.id))
    assert skill_run is not None
    assert skill_run.status == "failed"
    assert skill_run.error_code == "EXPERT_EXECUTION_FAILED"


@pytest.mark.asyncio
async def test_real_worker_matrix_same_thread_never_switches_to_another_account(
    client, session, admin, monkeypatch
) -> None:
    account_a, thread = await _create_account_and_thread(client, admin, nickname="scope-a")
    account_b, _ = await _create_account_and_thread(client, admin, nickname="scope-b")
    fixture = DeterministicExecutionFixture()
    monkeypatch.setattr(
        "app.services.turn_execution.build_runtime_tool_adapter",
        fixture.query_adapter,
    )

    first, _ = await _submit_execute_and_poll(
        client,
        session,
        admin,
        thread_id=thread["id"],
        key="matrix-scope-first",
        message="只查询账号数据，不生成策略",
    )
    second, _ = await _submit_execute_and_poll(
        client,
        session,
        admin,
        thread_id=thread["id"],
        key="matrix-scope-follow-up",
        message="查询当前账号数据",
    )

    assert account_a["id"] != account_b["id"]
    assert fixture.query_accounts == [account_a["id"], account_a["id"]]
    assert all(
        projection.get("account_id", account_a["id"]) == account_a["id"]
        for turn in (first, second)
        for projection in turn["projections"]
    )
    assert await session.scalar(select(func.count(AgentToolCall.id))) == 0
    assert await session.scalar(select(func.count(LLMCall.id))) == 0
    assert await session.scalar(select(func.count(ConversationTurn.id))) == 2
