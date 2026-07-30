"""Durable, idempotent execution boundary for main-Agent tool calls."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_object_session

from app.core.approval_audit import add_approval_requested
from app.models import (
    AgentInvocation,
    AgentToolCall,
    BrainTask,
    ToolExecutionAttempt,
    User,
)
from app.orchestrator.runtime_scope import RuntimeScope, RuntimeScopeConflict
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
        scope: RuntimeScope | None = None,
        approved: bool = False,
    ) -> ToolExecutionOutcome:
        if task.org_id != user.org_id:
            raise PermissionError("task and caller organization do not match")
        session = _session_for(user)
        if scope is not None:
            await scope.validate(session)
            if scope.org_id != task.org_id or scope.user_id != user.id or scope.task_id != task.id:
                raise RuntimeScopeConflict("tool execution scope does not match")
            if any(
                explicit is not None and explicit != expected
                for explicit, expected in (
                    (account_id, scope.account_id),
                    (skill_run_id, scope.skill_run_id),
                    (thread_id, scope.thread_id),
                    (turn_id, scope.turn_id),
                )
            ):
                raise RuntimeScopeConflict("tool execution provenance was overridden")
            account_id = scope.account_id
            skill_run_id = scope.skill_run_id
            thread_id = scope.thread_id
            turn_id = scope.turn_id
            if invocation_id is not None:
                invocation = await session.get(AgentInvocation, invocation_id)
                if (
                    invocation is None
                    or invocation.task_id != scope.task_id
                    or invocation.run_id != scope.run_id
                    or invocation.skill_run_id != scope.skill_run_id
                    or invocation.thread_id != scope.thread_id
                    or invocation.turn_id != scope.turn_id
                ):
                    raise RuntimeScopeConflict("tool invocation scope does not match")
        elif any(value is not None for value in (skill_run_id, thread_id, turn_id)):
            raise RuntimeScopeConflict("V3 tool writes require RuntimeScope")

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

        row = await session.scalar(
            select(AgentToolCall).where(
                AgentToolCall.org_id == task.org_id,
                AgentToolCall.task_id == task.id,
                AgentToolCall.tool_code == request.tool_code,
                AgentToolCall.idempotency_key == request.idempotency_key,
            )
        )
        if row is not None:
            replay = _existing_outcome(
                row,
                request=request,
                invocation_id=invocation_id,
                skill_run_id=skill_run_id,
                thread_id=thread_id,
                turn_id=turn_id,
                scope=scope,
                approved=approved,
            )
            if replay is not None:
                return replay
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
                provider_idempotency_key=_provider_idempotency_key(
                    org_id=task.org_id,
                    task_id=task.id,
                    tool_code=request.tool_code,
                    logical_key=request.idempotency_key,
                    side_effect_level=spec.side_effect_level,
                ),
                side_effect_level=spec.side_effect_level,
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
                    "runtime_scope": scope.as_dict() if scope is not None else None,
                },
            )
            try:
                async with session.begin_nested():
                    session.add(row)
                    await session.flush()
            except IntegrityError:
                row = await session.scalar(
                    select(AgentToolCall).where(
                        AgentToolCall.org_id == task.org_id,
                        AgentToolCall.task_id == task.id,
                        AgentToolCall.tool_code == request.tool_code,
                        AgentToolCall.idempotency_key == request.idempotency_key,
                    )
                )
                if row is None:
                    raise
                replay = _existing_outcome(
                    row,
                    request=request,
                    invocation_id=invocation_id,
                    skill_run_id=skill_run_id,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    scope=scope,
                    approved=approved,
                )
                if replay is not None:
                    return replay

        if spec.permission_mode in {"confirm", "manual"} and not approved:
            try:
                await self._invoke_adapter(
                    session=session,
                    task=task,
                    user=user,
                    request=request,
                    project_id=project_id,
                    account_id=account_id,
                    invocation_id=invocation_id,
                    provider_idempotency_key=row.provider_idempotency_key,
                    approved=approved,
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

        attempt_no = (
            int(
                await session.scalar(
                    select(func.max(ToolExecutionAttempt.attempt_no)).where(
                        ToolExecutionAttempt.tool_call_id == row.id
                    )
                )
                or 0
            )
            + 1
        )
        attempt = ToolExecutionAttempt(
            tool_call_id=row.id,
            attempt_no=attempt_no,
            status="dispatched",
            provider_idempotency_key=row.provider_idempotency_key,
            dispatched_at=datetime.now(UTC),
            meta={"logical_key": request.idempotency_key},
        )
        session.add(attempt)
        row.status = "running"
        row.started_at = row.started_at or datetime.now(UTC)
        await session.commit()
        try:
            result = await self._invoke_adapter(
                session=session,
                task=task,
                user=user,
                request=request,
                project_id=project_id,
                account_id=account_id,
                invocation_id=invocation_id,
                provider_idempotency_key=row.provider_idempotency_key,
                approved=approved,
            )
        except Exception as exc:
            ambiguous = spec.side_effect_level == "non_idempotent_write"
            row.status = "ambiguous" if ambiguous else "failed"
            row.error = type(exc).__name__
            row.finished_at = datetime.now(UTC)
            attempt.status = "ambiguous" if ambiguous else "failed"
            attempt.error = type(exc).__name__
            attempt.finished_at = datetime.now(UTC)
            await session.commit()
            if ambiguous:
                return ToolExecutionOutcome("ambiguous", row, None)
            raise

        safe_result = dict(result)
        row.status = "success"
        row.output_summary = _result_summary(safe_result)
        row.finished_at = datetime.now(UTC)
        row.meta = {**(row.meta or {}), "result": safe_result}
        attempt.status = "success"
        attempt.finished_at = datetime.now(UTC)
        row_id = row.id
        attempt_id = attempt.id
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            if spec.side_effect_level != "non_idempotent_write":
                raise
            row = await session.get(AgentToolCall, row_id)
            attempt = await session.get(ToolExecutionAttempt, attempt_id)
            if row is None or attempt is None:
                raise
            row.status = "ambiguous"
            row.error = "LocalCommitFailed"
            row.finished_at = datetime.now(UTC)
            attempt.status = "ambiguous"
            attempt.error = "LocalCommitFailed"
            attempt.finished_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(task)
            await session.refresh(user)
            return ToolExecutionOutcome("ambiguous", row, None)
        await session.refresh(row)
        return ToolExecutionOutcome("success", row, safe_result)

    async def _invoke_adapter(
        self,
        *,
        session: AsyncSession,
        task: BrainTask,
        user: User,
        request: RuntimeToolCall,
        project_id: int | None,
        account_id: int | None,
        invocation_id: int | None,
        provider_idempotency_key: str | None,
        approved: bool,
    ) -> dict[str, Any]:
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
                provider_idempotency_key=provider_idempotency_key,
                approved=approved,
            ),
        )
        return dict(result)


def _session_for(user: User) -> AsyncSession:
    session = async_object_session(user)
    if session is None:
        raise RuntimeError("tool execution session is not bound")
    return session


def _existing_outcome(
    row: AgentToolCall,
    *,
    request: RuntimeToolCall,
    invocation_id: int | None,
    skill_run_id: int | None,
    thread_id: int | None,
    turn_id: int | None,
    scope: RuntimeScope | None,
    approved: bool,
) -> ToolExecutionOutcome | None:
    original = dict((row.meta or {}).get("arguments") or {})
    if original != request.arguments:
        raise ToolIdempotencyConflict("idempotency key was already used with different arguments")
    if (
        row.invocation_id != invocation_id
        or (skill_run_id is not None and row.skill_run_id != skill_run_id)
        or (thread_id is not None and row.thread_id != thread_id)
        or (turn_id is not None and row.turn_id != turn_id)
        or (scope is not None and (row.meta or {}).get("runtime_scope") != scope.as_dict())
    ):
        raise ToolIdempotencyConflict("idempotent tool call provenance does not match")
    if row.status == "success":
        return ToolExecutionOutcome(
            status="success",
            tool_call=row,
            result=dict((row.meta or {}).get("result") or {}),
        )
    if row.status in {"ambiguous", "running"}:
        return ToolExecutionOutcome(row.status, row, None)
    if row.status == "waiting_approval" and not approved:
        return ToolExecutionOutcome("waiting_approval", row, None)
    if row.side_effect_level == "non_idempotent_write" and row.status == "failed":
        return ToolExecutionOutcome("failed", row, None)
    return None


def _result_summary(result: dict[str, Any]) -> str:
    if not result:
        return "工具已完成，未返回数据。"
    keys = "、".join(sorted(result)[:8])
    return f"工具已完成，返回字段：{keys}"


def _provider_idempotency_key(
    *,
    org_id: int,
    task_id: int,
    tool_code: str,
    logical_key: str,
    side_effect_level: str,
) -> str | None:
    if side_effect_level == "read":
        return None
    digest = hashlib.sha256(f"{org_id}:{task_id}:{tool_code}:{logical_key}".encode()).hexdigest()
    return f"geozx:{digest}"
