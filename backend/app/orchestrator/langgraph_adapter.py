"""Minimal resumable graph adapter for complex orchestration paths.

This keeps simple pipelines untouched while giving B5 a checkpointable boundary
that can later delegate to LangGraph directly.
"""

import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event

GraphState = dict[str, Any]
GraphHandler = Callable[[GraphState], Awaitable[GraphState] | GraphState]


@dataclass(frozen=True)
class GraphStep:
    key: str
    handler: GraphHandler
    requires_confirmation: bool = False


@dataclass(frozen=True)
class GraphRunResult:
    thread_id: str
    status: str
    state: GraphState
    next_step: str | None = None


class LangGraphAdapter:
    """Checkpointed graph runner with explicit human-confirmation resumes."""

    async def run(
        self,
        session: AsyncSession,
        *,
        thread_id: str,
        initial_state: GraphState,
        steps: Sequence[GraphStep],
        approved_steps: set[str] | None = None,
    ) -> GraphRunResult:
        approved = approved_steps or set()
        checkpoint = await self._load_checkpoint(session, thread_id)
        index = int(checkpoint.get("next_index", 0)) if checkpoint else 0
        state = dict(checkpoint.get("state", initial_state) if checkpoint else initial_state)

        while index < len(steps):
            step = steps[index]
            if step.requires_confirmation and step.key not in approved:
                await self._save_checkpoint(session, thread_id, "waiting", state, index, step.key)
                return GraphRunResult(
                    thread_id=thread_id,
                    status="waiting",
                    state=state,
                    next_step=step.key,
                )

            state = await self._run_step(step, state)
            index += 1
            await self._save_checkpoint(session, thread_id, "running", state, index, None)

        await self._save_checkpoint(session, thread_id, "completed", state, index, None)
        return GraphRunResult(thread_id=thread_id, status="completed", state=state)

    async def _run_step(self, step: GraphStep, state: GraphState) -> GraphState:
        result = step.handler(dict(state))
        if inspect.isawaitable(result):
            return await result
        return result

    async def _load_checkpoint(self, session: AsyncSession, thread_id: str) -> dict | None:
        rows = (
            await session.scalars(
                select(Event).where(Event.type == "langgraph.checkpoint").order_by(Event.id.desc())
            )
        ).all()
        for row in rows:
            payload = row.payload or {}
            if payload.get("thread_id") == thread_id:
                return payload
        return None

    async def _save_checkpoint(
        self,
        session: AsyncSession,
        thread_id: str,
        status: str,
        state: GraphState,
        next_index: int,
        waiting_for: str | None,
    ) -> None:
        session.add(
            Event(
                type="langgraph.checkpoint",
                payload={
                    "thread_id": thread_id,
                    "status": status,
                    "state": state,
                    "next_index": next_index,
                    "waiting_for": waiting_for,
                },
            )
        )
        await session.commit()
