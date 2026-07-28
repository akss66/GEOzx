"""Durable, idempotent execution boundary for main-Agent tool calls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_object_session

from app.core.approval_audit import add_approval_requested
from app.models import AgentToolCall, BrainTask, User
from app.schemas.brain import RuntimeToolCall
from app.tools import (
    ToolAdapter,
    ToolExecutionContext,
    ToolPermissionRequired,
)


class ToolIdempotencyConflict(RuntimeError):
    """Raised when one idempotency key is reused with different arguments."""


@dataclass(frozen=True)
class ToolExecutionOutcome:
    status: str
    tool_call: AgentToolCall
    result: dict[str, Any] | None


class DurableToolExecutor:
    """Persist intent before execution and reuse completed results on retry."""

    def __init__(self, adapter: ToolAdapter) -> None:
        self._adapter = adapter

    async def execute(
        self,
        *,
        task: BrainTask,
        user: User,
        request: RuntimeToolCall,
        project_id: int | None = None,
        account_id: int | None = None,
        agent_code: str = "00-decision",
        invocation_id: int | None = None,
        skill_run_id: int | None = None,
        thread_id: int | None = None,
        turn_id: int | None = None,
        approved: bool = False,
    ) -> ToolExecutionOutcome:
        if task.org_id != user.org_id:
            raise PermissionError("task and caller organization do not match")

        spec = self._adapter.get_spec(request.tool_code)
        if spec is None:
            # Preserve the low-level adapter's audited denial behavior.
            await self._adapter.invoke(
                request.tool_code,
                request.arguments,
                ToolExecutionContext(
                    session=_session_for(user),
                    user=user,
                    project_id=project_id,
                    account_id=account_id,
                    task_id=task.id,
                    invocation_id=invocation_id,
                    approved=approved,
                ),
            )
            raise AssertionError("unreachable")

        session = _session_for(user)
        row = await session.scalar(
            select(AgentToolCall).where(
                AgentToolCall.org_id == task.org_id,
                AgentToolCall.task_id == task.id,
                AgentToolCall.tool_code == request.tool_code,
                AgentToolCall.idempotency_key == request.idempotency_key,
            )
        )
        if row is not None:
            original = dict((row.meta or {}).get("arguments") or {})
            if original != request.arguments:
                raise ToolIdempotencyConflict(
                    "idempotency key was already used with different arguments"
                )
            if (
                (skill_run_id is not None and row.skill_run_id != skill_run_id)
                or (thread_id is not None and row.thread_id != thread_id)
                or (turn_id is not None and row.turn_id != turn_id)
            ):
                raise ToolIdempotencyConflict(
                    "idempotent tool call provenance does not match"
                )
            if row.status == "success":
                return ToolExecutionOutcome(
                    status="success",
                    tool_call=row,
                    result=dict((row.meta or {}).get("result") or {}),
                )
            if row.status == "waiting_approval" and not approved:
                return ToolExecutionOutcome("waiting_approval", row, None)
        else:
            row = AgentToolCall(
                org_id=task.org_id,
                task_id=task.id,
                invocation_id=invocation_id,
                skill_run_id=skill_run_id,
                thread_id=thread_id,
                turn_id=turn_id,
                module="brain",
                agent_code=agent_code,
                tool_code=request.tool_code,
                tool_name=request.tool_code.replace("_", " ").title(),
                idempotency_key=request.idempotency_key,
                status="planned",
                permission_mode=spec.permission_mode,
                requires_human_confirmation=spec.permission_mode in {"confirm", "manual"},
                input_summary=request.purpose,
                meta={
                    "arguments": request.arguments,
                    "purpose": request.purpose,
                    "scope": {
                        "project_id": project_id,
                        "account_id": account_id,
                    },
                },
            )
            session.add(row)
            await session.flush()

        row.status = "running"
        row.started_at = row.started_at or datetime.now(UTC)
        await session.commit()
        try:
            result = await self._adapter.invoke(
                request.tool_code,
                request.arguments,
                ToolExecutionContext(
                    session=session,
                    user=user,
                    project_id=project_id,
                    account_id=account_id,
                    task_id=task.id,
                    invocation_id=invocation_id,
                    approved=approved,
                ),
            )
        except ToolPermissionRequired:
            row.status = "waiting_approval"
            row.requires_human_confirmation = True
            await add_approval_requested(
                session,
                org_id=task.org_id,
                project_id=project_id,
                content_item_id=task.content_item_id,
                approval_kind="tool_call",
                source_id=row.id,
                title=row.tool_name,
                body=request.purpose,
            )
            await session.commit()
            return ToolExecutionOutcome("waiting_approval", row, None)
        except Exception as exc:
            row.status = "failed"
            row.error = type(exc).__name__
            row.finished_at = datetime.now(UTC)
            await session.commit()
            raise

        safe_result = dict(result)
        row.status = "success"
        row.output_summary = _result_summary(safe_result)
        row.finished_at = datetime.now(UTC)
        row.meta = {**(row.meta or {}), "result": safe_result}
        await session.commit()
        await session.refresh(row)
        return ToolExecutionOutcome("success", row, safe_result)


def _session_for(user: User) -> AsyncSession:
    session = async_object_session(user)
    if session is None:
        raise RuntimeError("tool execution session is not bound")
    return session


def _result_summary(result: dict[str, Any]) -> str:
    if not result:
        return "工具已完成，未返回数据。"
    keys = "、".join(sorted(result)[:8])
    return f"工具已完成，返回字段：{keys}"
