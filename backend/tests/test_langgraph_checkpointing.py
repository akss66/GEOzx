"""Production LangGraph checkpoint lifecycle and graph wiring."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.models import AgentToolCall, BrainTask
from app.models.enums import BrainTaskStatus, BrainTaskType
from app.orchestrator.brain_runtime import (
    BrainRuntimeGraph,
    BrainRuntimeState,
    bind_runtime_session,
)
from app.orchestrator.checkpointing import postgres_checkpoint_dsn


def test_postgres_checkpoint_dsn_uses_psycopg_scheme() -> None:
    assert (
        postgres_checkpoint_dsn(
            "postgresql+asyncpg://dyflow:secret@postgres:5432/dyflow"
        )
        == "postgresql://dyflow:secret@postgres:5432/dyflow"
    )
    assert (
        postgres_checkpoint_dsn("postgres://dyflow:secret@postgres/dyflow")
        == "postgres://dyflow:secret@postgres/dyflow"
    )


def test_runtime_graph_wires_one_checkpointer_to_every_graph() -> None:
    checkpointer = InMemorySaver()

    runtime = BrainRuntimeGraph(checkpointer=checkpointer)

    assert runtime._graph.checkpointer is checkpointer
    assert runtime._resume_graph.checkpointer is checkpointer
    assert runtime._smart_graph.checkpointer is checkpointer
    assert runtime._smart_resume_graph.checkpointer is checkpointer
    assert runtime.graph_config("brain-task-42") == {
        "configurable": {"thread_id": "brain-task-42"}
    }


@pytest.mark.asyncio
async def test_worker_owns_postgres_checkpointer_lifecycle(monkeypatch) -> None:
    from app import worker

    checkpointer = object()
    lifecycle = {"opened": False, "closed": False}

    @asynccontextmanager
    async def fake_open(_database_url: str):
        lifecycle["opened"] = True
        yield checkpointer
        lifecycle["closed"] = True

    redis_client = SimpleNamespace(aclose=AsyncMock())
    configure = AsyncMock()
    monkeypatch.setattr(worker.settings, "langgraph_checkpoint_enabled", True)
    monkeypatch.setattr(worker, "open_postgres_checkpointer", fake_open)
    monkeypatch.setattr(worker.aioredis, "from_url", lambda *_args, **_kwargs: redis_client)
    monkeypatch.setattr(worker.runtime_graph, "configure_checkpointer", configure)
    ctx: dict = {}

    await worker.on_startup(ctx)

    assert lifecycle["opened"] is True
    assert ctx["langgraph_checkpointer"] is checkpointer
    configure.assert_awaited_once_with(checkpointer)

    await worker.on_shutdown(ctx)

    assert lifecycle["closed"] is True
    redis_client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_permission_interrupt_resumes_from_the_same_checkpoint(
    session, admin, monkeypatch
) -> None:
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="Permission checkpoint",
        type=BrainTaskType.CONTENT_CREATION,
        status=BrainTaskStatus.RUNNING,
        runtime_mode="langgraph",
        thread_id="checkpoint-permission-1",
    )
    session.add(task)
    await session.commit()
    tool_call = AgentToolCall(
        org_id=admin.org_id,
        task_id=task.id,
        module="brain",
        agent_code="02-content-director",
        tool_code="publish_package_prepare",
        tool_name="Prepare publish package",
        status="waiting_approval",
        permission_mode="confirm",
        requires_human_confirmation=True,
        output_summary="Prepared",
    )
    session.add(tool_call)
    await session.commit()

    checkpointer = InMemorySaver()
    runtime = BrainRuntimeGraph(checkpointer=checkpointer)

    async def no_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr(runtime, "_record_event", no_event)
    graph = StateGraph(BrainRuntimeState)
    graph.add_node("collect", runtime._collect_permissions)
    graph.add_node("gate", runtime._smart_permission_gate)
    graph.add_edge(START, "collect")
    graph.add_edge("collect", "gate")
    graph.add_edge("gate", END)
    compiled = graph.compile(checkpointer=checkpointer)
    config = runtime.graph_config(task.thread_id)

    with bind_runtime_session(session):
        paused = await compiled.ainvoke(
            {"task_id": task.id, "thread_id": task.thread_id, "observations": []},
            config=config,
        )

    assert paused["__interrupt__"][0].value == {
        "kind": "permission",
        "tool_call_ids": [tool_call.id],
    }
    tool_call.status = "success"
    await session.commit()

    with bind_runtime_session(session):
        resumed = await compiled.ainvoke(
            Command(resume={"kind": "permission", "approved": True}),
            config=config,
        )

    assert resumed["status"] == "ready_to_decide"
    assert resumed["pending_permissions"] == []
    assert resumed["observations"][-1]["tool_call_id"] == tool_call.id
    assert resumed["observations"][-1]["approved"] is True


@pytest.mark.asyncio
async def test_decision_interrupt_validates_and_restores_the_user_choice() -> None:
    checkpointer = InMemorySaver()
    runtime = BrainRuntimeGraph(checkpointer=checkpointer)
    graph = StateGraph(BrainRuntimeState)
    graph.add_node("decision", runtime._decision_gate)
    graph.add_edge(START, "decision")
    graph.add_edge("decision", END)
    compiled = graph.compile(checkpointer=checkpointer)
    config = runtime.graph_config("checkpoint-decision-1")

    paused = await compiled.ainvoke(
        {
            "task_id": 1,
            "thread_id": "checkpoint-decision-1",
            "status": "waiting_decision",
            "pending_decision_id": "direction-1",
            "observations": [],
        },
        config=config,
    )
    assert paused["__interrupt__"][0].value == {
        "kind": "decision",
        "decision_id": "direction-1",
    }

    resumed = await compiled.ainvoke(
        Command(
            resume={
                "kind": "decision",
                "decision_id": "direction-1",
                "choice_id": "authority",
                "choice_title": "Authority",
            }
        ),
        config=config,
    )

    assert resumed["status"] == "ready_to_decide"
    assert resumed["pending_decision_id"] == ""
    assert resumed["observations"] == [
        {
            "kind": "user_decision",
            "decision_id": "direction-1",
            "choice_id": "authority",
            "summary": "Authority",
        }
    ]
