import pytest
from sqlalchemy import select

from app.models import Event
from app.orchestrator.langgraph_adapter import GraphStep, LangGraphAdapter


def prepare(state: dict) -> dict:
    return {**state, "prepared": True, "history": [*state.get("history", []), "prepare"]}


async def review(state: dict) -> dict:
    return {**state, "reviewed": True, "history": [*state.get("history", []), "review"]}


def publish(state: dict) -> dict:
    return {**state, "published": True, "history": [*state.get("history", []), "publish"]}


@pytest.mark.asyncio
async def test_graph_pauses_for_confirmation_and_resumes_from_checkpoint(session) -> None:
    adapter = LangGraphAdapter()
    steps = [
        GraphStep("prepare", prepare),
        GraphStep("review", review, requires_confirmation=True),
        GraphStep("publish", publish),
    ]

    first = await adapter.run(
        session,
        thread_id="task-1",
        initial_state={"goal": "launch", "history": []},
        steps=steps,
    )

    assert first.status == "waiting"
    assert first.next_step == "review"
    assert first.state["history"] == ["prepare"]

    second = await adapter.run(
        session,
        thread_id="task-1",
        initial_state={"goal": "ignored"},
        steps=steps,
        approved_steps={"review"},
    )

    assert second.status == "completed"
    assert second.state["history"] == ["prepare", "review", "publish"]

    events = (
        await session.scalars(
            select(Event).where(Event.type == "langgraph.checkpoint").order_by(Event.id)
        )
    ).all()
    assert [event.payload["status"] for event in events] == [
        "running",
        "waiting",
        "running",
        "running",
        "completed",
    ]
