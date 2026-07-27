"""Runtime working memory for visible agent threads."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import extract_json
from app.config import settings
from app.llm.gateway import (
    LLMCallContext,
    bind_llm_call_context,
    gateway,
    reset_stream_observer,
    set_stream_observer,
)
from app.models import BrainTask, Event, RuntimeMemory
from app.models.enums import AgentCode
from app.prompts import prompt_registry


class RuntimeMemoryCompactionError(RuntimeError):
    """Raised when the memory projection cannot be safely updated."""


class RuntimeMemoryScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    org_id: int | None = None
    client_id: int | None = None
    project_id: int | None = None
    account_ids: list[int] = Field(default_factory=list)


class RuntimeMemorySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = ""
    scope: RuntimeMemoryScope = Field(default_factory=RuntimeMemoryScope)
    constraints: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    expert_findings: list[str] = Field(default_factory=list)
    tool_results: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    next_step: str = ""
    covered_event_ids: list[int] = Field(default_factory=list)


class RuntimeMemoryService:
    def __init__(
        self,
        *,
        llm=gateway,
        event_threshold: int | None = None,
        char_threshold: int | None = None,
    ) -> None:
        self._llm = llm
        self._event_threshold = (
            event_threshold
            if event_threshold is not None
            else settings.agent_runtime_memory_event_threshold
        )
        self._char_threshold = (
            char_threshold
            if char_threshold is not None
            else settings.agent_runtime_memory_char_threshold
        )

    async def load(
        self,
        session: AsyncSession,
        *,
        org_id: int,
        task_id: int,
    ) -> RuntimeMemory | None:
        return await session.scalar(
            select(RuntimeMemory).where(
                RuntimeMemory.org_id == org_id,
                RuntimeMemory.task_id == task_id,
            )
        )

    async def maybe_compact(
        self,
        session: AsyncSession,
        task: BrainTask,
    ) -> RuntimeMemory | None:
        return await self.compact(session, task, force=False)

    async def compact(
        self,
        session: AsyncSession,
        task: BrainTask,
        *,
        force: bool = False,
    ) -> RuntimeMemory | None:
        memory = await self.load(session, org_id=task.org_id, task_id=task.id)
        events = await self._source_events_after(
            session,
            task,
            after_event_id=memory.last_event_id if memory else None,
        )
        if not events:
            return memory

        normalized = [_normalize_event(event) for event in events]
        normalized = [item for item in normalized if item is not None]
        if not normalized:
            return memory

        rendered_events = json.dumps(normalized, ensure_ascii=False)
        if (
            not force
            and len(normalized) < self._event_threshold
            and len(rendered_events) < self._char_threshold
        ):
            return memory

        prompt = prompt_registry.load("memory.compactor")
        previous_snapshot = memory.snapshot if memory is not None else {}
        messages = [
            {"role": "system", "content": prompt.content},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "previous_memory": _redact_value(previous_snapshot),
                        "new_events": normalized,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        context = LLMCallContext(
            task_id=task.id,
            trace_id=task.thread_id,
            prompt_id=prompt.spec.id,
            prompt_version=prompt.spec.version,
            prompt_hash=prompt.content_hash,
            prompt_schema_version=prompt.spec.schema_version,
            scope=_authoritative_scope(task),
            budget={
                "purpose": "runtime_memory_compaction",
                "max_context_chars": settings.agent_runtime_context_char_budget,
            },
            response_format={"type": "json_object"},
        )
        observer_token = set_stream_observer(None)
        try:
            with bind_llm_call_context(context):
                result, _cost = await self._llm.chat(
                    session,
                    task.org_id,
                    AgentCode.DECISION.value,
                    messages,
                )
        finally:
            reset_stream_observer(observer_token)

        try:
            snapshot = RuntimeMemorySnapshot.model_validate(extract_json(result.content))
        except (ValueError, ValidationError) as exc:
            raise RuntimeMemoryCompactionError(
                "runtime memory compaction returned invalid JSON"
            ) from exc

        event_ids = [event.id for event in events]
        snapshot = _apply_authoritative_scope(snapshot, task, event_ids)
        if memory is None:
            memory = RuntimeMemory(
                org_id=task.org_id,
                task_id=task.id,
                thread_id=task.thread_id or f"brain-task-{task.id}",
                revision=0,
                source_event_count=0,
            )
            session.add(memory)

        memory.revision += 1
        memory.snapshot = snapshot.model_dump()
        memory.last_event_id = max(event_ids)
        memory.source_event_count += len(event_ids)
        memory.prompt_id = prompt.spec.id
        memory.prompt_version = prompt.spec.version
        memory.prompt_hash = prompt.content_hash
        memory.prompt_schema_version = prompt.spec.schema_version
        memory.compacted_at = datetime.now(UTC)
        await session.flush()
        await self._record_compaction_event(session, task, memory, event_ids)
        await session.commit()
        return memory

    async def build_runtime_context(
        self,
        session: AsyncSession,
        task: BrainTask,
        *,
        current_message: str,
        budget_chars: int | None = None,
    ) -> list[dict[str, str]]:
        budget = budget_chars or settings.agent_runtime_context_char_budget
        memory = await self.load(session, org_id=task.org_id, task_id=task.id)
        events = await self._source_events_after(
            session,
            task,
            after_event_id=memory.last_event_id if memory else None,
        )
        messages: list[dict[str, str]] = []
        used = 0
        if memory is not None and memory.snapshot:
            memory_text = "Runtime memory:\n" + json.dumps(
                memory.snapshot,
                ensure_ascii=False,
                indent=2,
            )
            messages.append({"role": "system", "content": _truncate(memory_text, budget // 2)})
            used += len(messages[-1]["content"])

        recent = self._events_to_messages(events, current_message=current_message)
        for item in reversed(recent):
            item_len = len(item["content"])
            if messages and used + item_len > budget:
                continue
            messages.insert(1 if messages and messages[0]["role"] == "system" else 0, item)
            used += item_len
        return messages

    async def _source_events_after(
        self,
        session: AsyncSession,
        task: BrainTask,
        *,
        after_event_id: int | None,
    ) -> list[Event]:
        rows = (
            await session.scalars(
                select(Event)
                .where(
                    Event.type.like("brain.runtime.%"),
                    Event.payload["task_id"].as_integer() == task.id,
                    *(
                        (Event.id > after_event_id,)
                        if after_event_id is not None
                        else ()
                    ),
                )
                .order_by(Event.id)
            )
        ).all()
        return [
            row
            for row in rows
            if row.type != "brain.runtime.memory_compacted"
        ]

    async def _record_compaction_event(
        self,
        session: AsyncSession,
        task: BrainTask,
        memory: RuntimeMemory,
        event_ids: list[int],
    ) -> None:
        session.add(
            Event(
                type="brain.runtime.memory_compacted",
                content_item_id=task.content_item_id,
                project_id=task.brief.project_id if task.brief else None,
                payload={
                    "task_id": task.id,
                    "thread_id": task.thread_id or f"brain-task-{task.id}",
                    "memory_id": memory.id,
                    "revision": memory.revision,
                    "source_event_count": len(event_ids),
                    "source_event_ids": event_ids,
                    "last_event_id": memory.last_event_id,
                },
            )
        )

    def _events_to_messages(
        self,
        events: list[Event],
        *,
        current_message: str,
    ) -> list[dict[str, str]]:
        latest_current_id = next(
            (
                event.id
                for event in reversed(events)
                if event.type == "brain.runtime.user_message"
                and str((event.payload or {}).get("message") or "").strip()
                == current_message.strip()
            ),
            None,
        )
        messages: list[dict[str, str]] = []
        for event in events:
            payload = event.payload or {}
            if event.type == "brain.runtime.user_message":
                if event.id == latest_current_id:
                    continue
                content = str(payload.get("message") or payload.get("content") or "").strip()
                if content:
                    messages.append({"role": "user", "content": content})
            elif event.type == "brain.runtime.message_done":
                agent_code = str(payload.get("agent_code") or AgentCode.DECISION.value)
                if agent_code != AgentCode.DECISION.value:
                    continue
                content = str(payload.get("content") or payload.get("message") or "").strip()
                if content:
                    messages.append({"role": "assistant", "content": content})
            elif event.type == "brain.runtime.subagent_completed":
                agent_name = str(payload.get("agent_name") or "expert")
                message = str(payload.get("message") or "").strip()
                if message:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": f"Expert update ({agent_name}): {message}",
                        }
                    )
            elif event.type == "brain.runtime.decision_selected":
                choice = str(payload.get("choice_title") or "").strip()
                if choice:
                    messages.append({"role": "user", "content": f"Selected option: {choice}"})
            elif event.type == "brain.runtime.resumed":
                result = "approved" if payload.get("approved") else "rejected"
                messages.append({"role": "user", "content": f"Tool permission decision: {result}"})
        return messages


def _normalize_event(event: Event) -> dict[str, Any] | None:
    payload = _redact_value(event.payload or {})
    base = {
        "event_id": event.id,
        "type": event.type,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }
    if event.type == "brain.runtime.user_message":
        return {**base, "message": _text(payload.get("message") or payload.get("content"))}
    if event.type in {
        "brain.runtime.message_done",
        "brain.runtime.message_error",
        "brain.runtime.subagent_completed",
        "brain.runtime.clarification_requested",
        "brain.runtime.decision_requested",
        "brain.runtime.completed",
        "brain.runtime.failed",
        "brain.runtime.generation_stopped",
    }:
        return {
            **base,
            "agent_code": _text(payload.get("agent_code")),
            "agent_name": _text(payload.get("agent_name")),
            "message": _text(
                payload.get("message") or payload.get("content") or payload.get("error")
            ),
        }
    if event.type == "brain.runtime.decision_selected":
        return {
            **base,
            "choice_title": _text(payload.get("choice_title")),
            "comment": _text(payload.get("comment")),
        }
    if event.type == "brain.runtime.resumed":
        return {
            **base,
            "approved": bool(payload.get("approved")),
            "comment": _text(payload.get("comment")),
        }
    return None


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(
                marker in lowered
                for marker in ("secret", "token", "password", "api_key", "apikey", "key")
            ):
                result[str(key)] = "[redacted]"
            else:
                result[str(key)] = _redact_value(item)
        return result
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        return _truncate(value, 2000)
    return value


def _text(value: Any, *, limit: int = 1200) -> str:
    return _truncate(str(value or "").strip(), limit)


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}..."


def _authoritative_scope(task: BrainTask) -> dict[str, Any]:
    scope: dict[str, Any] = {"org_id": task.org_id}
    if task.brief is not None and task.brief.project_id is not None:
        scope["project_id"] = task.brief.project_id
    if task.brief is not None:
        scope["account_ids"] = [int(item) for item in task.brief.account_ids]
    return scope


def _apply_authoritative_scope(
    snapshot: RuntimeMemorySnapshot,
    task: BrainTask,
    event_ids: list[int],
) -> RuntimeMemorySnapshot:
    previous_ids = [int(item) for item in snapshot.covered_event_ids]
    covered_ids = sorted(set(previous_ids + [int(item) for item in event_ids]))
    account_ids = [int(item) for item in task.brief.account_ids] if task.brief else []
    snapshot.scope.org_id = task.org_id
    snapshot.scope.project_id = task.brief.project_id if task.brief else None
    snapshot.scope.account_ids = account_ids
    snapshot.covered_event_ids = covered_ids
    return snapshot


runtime_memory_service = RuntimeMemoryService()
