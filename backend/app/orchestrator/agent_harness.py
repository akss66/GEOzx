"""One audited execution boundary for every specialist Agent."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.base import AgentContext
from app.agents.registry import AGENT_SPECS, AgentSpec, get_agent_spec
from app.core.approval_audit import add_approval_requested
from app.core.events import publish_realtime_event, record_runtime_event_once
from app.core.workspace_access import require_account_access, require_project_access
from app.db import async_session
from app.models import (
    Account,
    AgentInvocation,
    AgentRun,
    AgentToolCall,
    BrainTask,
    ContentItem,
    Deliverable,
    DeliverableAcceptance,
    Event,
    KnowledgeEntry,
    ModelConfig,
    Project,
    ProjectAccount,
    User,
)
from app.models.enums import (
    AccountStatus,
    AgentCode,
    AgentInvocationStatus,
    ContentStatus,
    DeliverableAcceptanceStatus,
    DeliverableStatus,
    WorkspaceRole,
)
from app.orchestrator.agent_kernel import KernelEventType, expert_kernel_policy
from app.orchestrator.runtime_scope import RuntimeScope, RuntimeScopeConflict
from app.orchestrator.runtime_tools import (
    build_runtime_tool_adapter,
    runtime_tool_capabilities,
)
from app.orchestrator.specialist_kernel import (
    SpecialistKernelBlocked,
    specialist_kernel,
)
from app.orchestrator.tool_executor import DurableToolExecutor
from app.schemas.brain import RuntimeToolCall
from app.services.agent_management import get_business_config, require_agent_enabled
from app.services.knowledge_workspace import (
    knowledge_context,
    list_agent_knowledge,
    record_knowledge_citations,
)
from app.services.runtime_deliverables import write_runtime_deliverable


class AgentHarnessError(RuntimeError):
    pass


class AgentStepInProgress(AgentHarnessError):
    pass


@dataclass(frozen=True)
class AgentHarnessResult:
    task: BrainTask
    invocation: AgentInvocation
    deliverable: Deliverable | None
    acceptance: DeliverableAcceptance | None
    knowledge_sources: list[KnowledgeEntry]
    output: dict


@dataclass(frozen=True)
class AgentTraceResult:
    invocation_id: int
    agent_code: str
    output_summary: str
    output: dict[str, Any]


@dataclass(frozen=True)
class _RuntimeLifecycleBroadcast:
    event_id: int
    event_type: str
    payload: dict
    content_item_id: int | None
    project_id: int | None


_OPERATING_ROLES = {WorkspaceRole.LEAD, WorkspaceRole.OPERATOR, WorkspaceRole.EDITOR}
_MANAGEMENT_TO_RUNTIME_TOOL = {
    "account_context": "account.profile",
    "profile_snapshot": "account.data_context",
    "review_metrics": "account.metrics_summary",
}


class AgentHarness:
    """Execute a specialist with scope, prompt, trace, and idempotency controls."""

    async def execute_trace_isolated(
        self,
        *,
        scope: RuntimeScope,
        code: AgentCode,
        purpose: str,
        evidence_refs: list[str],
        step_key: str,
        attempt: int,
        upstream: dict,
        session_factory: Any = async_session,
    ) -> AgentTraceResult:
        """Reload the complete scope and execute one trace in its own session."""

        async with session_factory() as session:
            user = await session.scalar(
                select(User).where(
                    User.id == scope.user_id,
                    User.org_id == scope.org_id,
                )
            )
            task = await session.scalar(
                select(BrainTask)
                .options(selectinload(BrainTask.brief))
                .where(
                    BrainTask.id == scope.task_id,
                    BrainTask.org_id == scope.org_id,
                )
            )
            if user is None or task is None:
                raise RuntimeScopeConflict("isolated expert scope is unavailable")
            result = await self.execute(
                session,
                user=user,
                task=task,
                code=code,
                purpose=purpose,
                evidence_refs=list(evidence_refs),
                run_id=scope.run_id,
                skill_run_id=scope.skill_run_id,
                thread_id=scope.thread_id,
                turn_id=scope.turn_id,
                step_key=step_key,
                attempt=attempt,
                upstream=dict(upstream),
                scope=scope,
                trace_only=True,
            )
            return AgentTraceResult(
                invocation_id=result.invocation.id,
                agent_code=code.value,
                output_summary=result.invocation.output_summary,
                output=dict(result.output or {}),
            )

    async def execute(
        self,
        session: AsyncSession,
        *,
        user: User,
        task: BrainTask,
        code: AgentCode,
        purpose: str,
        evidence_refs: list[str],
        run_id: int | None = None,
        step_key: str | None = None,
        attempt: int = 0,
        upstream: dict | None = None,
        skill_run_id: int | None = None,
        thread_id: int | None = None,
        turn_id: int | None = None,
        scope: RuntimeScope | None = None,
        trace_only: bool = False,
    ) -> AgentHarnessResult:
        if task.org_id != user.org_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
        if scope is not None:
            await scope.validate(session)
            if scope.org_id != task.org_id or scope.user_id != user.id or scope.task_id != task.id:
                raise RuntimeScopeConflict("Agent Harness scope does not match")
            if any(
                explicit is not None and explicit != expected
                for explicit, expected in (
                    (run_id, scope.run_id),
                    (skill_run_id, scope.skill_run_id),
                    (thread_id, scope.thread_id),
                    (turn_id, scope.turn_id),
                )
            ):
                raise RuntimeScopeConflict("Agent Harness provenance was overridden")
            run_id = scope.run_id
            skill_run_id = scope.skill_run_id
            thread_id = scope.thread_id
            turn_id = scope.turn_id
        elif any(value is not None for value in (skill_run_id, thread_id, turn_id)):
            raise RuntimeScopeConflict("V3 Agent Harness writes require RuntimeScope")
        # Async ORM relationships must never be loaded by plain attribute access.
        # Skill retries and worker hand-offs can supply a persisted task whose
        # one-to-one Brief relationship is unloaded or expired.
        await session.refresh(task, attribute_names=["brief"])
        spec = get_agent_spec(code)
        project_id, account_id = self._task_scope(task)
        if scope is not None and account_id != scope.account_id:
            raise RuntimeScopeConflict("Agent Harness account scope does not match")
        project, account = await self._require_scope(
            session,
            user=user,
            project_id=project_id,
            account_id=account_id,
        )
        await require_agent_enabled(session, user.org_id, code)
        management = await get_business_config(
            session,
            user.org_id,
            code,
            responsibility=spec.name,
        )
        tool_allowlist = self._autonomous_runtime_tool_codes(management)
        kernel_policy = expert_kernel_policy(tool_allowlist=tool_allowlist)
        content_item = await self._ensure_content_item(
            session,
            task=task,
            user=user,
            project_id=project_id,
            account_id=account.id,
            spec=spec,
        )

        if run_id is not None and step_key:
            existing = await self._find_invocation(
                session,
                run_id=run_id,
                step_key=step_key,
                attempt=attempt,
            )
            if existing is not None:
                if trace_only:
                    return await self._existing_trace_result(session, task, existing)
                await self._repair_successful_tool_projections(
                    session,
                    task=task,
                    account_id=account.id,
                    invocation=existing,
                )
                return await self._existing_result(session, task, existing)

        invocation = AgentInvocation(
            task_id=task.id,
            run_id=run_id,
            skill_run_id=skill_run_id,
            thread_id=thread_id,
            turn_id=turn_id,
            step_key=step_key,
            attempt=attempt,
            agent_code=code,
            agent_name=spec.name,
            status=AgentInvocationStatus.RUNNING,
            input_summary=purpose,
            output_summary="",
            model=await self._model_name(session, user.org_id, code),
            token_count=0,
            cost=Decimal("0"),
            upstream=[],
            started_at=datetime.now(UTC),
        )
        session.add(invocation)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            await session.refresh(task)
            await session.refresh(task, attribute_names=["brief"])
            if run_id is None or not step_key:
                raise
            existing = await self._find_invocation(
                session,
                run_id=run_id,
                step_key=step_key,
                attempt=attempt,
            )
            if existing is None:
                raise
            await self._repair_successful_tool_projections(
                session,
                task=task,
                account_id=account_id,
                invocation=existing,
            )
            return await self._existing_result(session, task, existing)
        invocation_id = invocation.id
        started_lifecycle = await self._record_runtime_lifecycle(
            session,
            task=task,
            account_id=account.id,
            run_id=run_id,
            invocation=invocation,
            event_type="brain.runtime.subagent_started",
            payload={
                "message": f"{spec.name}开始处理。",
                "agent_code": code.value,
                "agent_name": spec.name,
                "invocation_id": invocation.id,
            },
            semantic_suffix="started",
        )

        session.add(
            Event(
                type="agent.harness.started",
                content_item_id=content_item.id,
                project_id=project_id,
                payload={
                    "task_id": task.id,
                    "run_id": run_id,
                    "invocation_id": invocation.id,
                    "step_key": step_key,
                    "attempt": attempt,
                    "agent_code": code.value,
                    "account_id": account.id,
                    "purpose": purpose,
                    "evidence_refs": evidence_refs,
                    "kernel_policy": kernel_policy.as_context(),
                },
            )
        )
        await session.commit()
        await self._publish_runtime_lifecycle(started_lifecycle)

        knowledge_rows = (
            await list_agent_knowledge(
                session,
                org_id=user.org_id,
                client_id=project.client_id,
                project_id=project.id,
            )
            if project is not None and project.client_id is not None
            else []
        )
        operating_context = {
            **self._account_scoped_upstream(upstream, account_id=account.id),
            "account_context": {
                "account_id": account.id,
                "nickname": account.nickname,
                "platform": account.platform.value,
                "project_id": project_id,
                "project_name": project.name if project is not None else None,
            },
            "evidence_refs": {"items": evidence_refs},
            "agent_policy": {
                "tool_permissions": management["tool_permissions"],
                "quality_gates": management["quality_gates"],
            },
        }
        operating_context["agent_policy"]["kernel"] = kernel_policy.as_context()
        runner = spec.runner()
        runner.code = code.value
        runtime_capabilities = {str(item["code"]): item for item in runtime_tool_capabilities(user)}
        available_tools = (
            []
            if trace_only
            else [
                runtime_capabilities[tool_code]
                for tool_code in sorted(tool_allowlist)
                if tool_code in runtime_capabilities
            ]
        )
        tool_executor = DurableToolExecutor(build_runtime_tool_adapter())

        async def execute_tool(request: RuntimeToolCall) -> dict:
            outcome = await tool_executor.execute(
                task=task,
                user=user,
                request=request,
                project_id=project_id,
                account_id=account.id,
                agent_code=code.value,
                invocation_id=invocation.id,
                scope=scope,
            )
            if outcome.status != "success" or outcome.result is None:
                raise SpecialistKernelBlocked(
                    f"tool {request.tool_code} requires main-Agent intervention: {outcome.status}"
                )
            await self._project_successful_tool_call(
                session,
                task=task,
                account_id=account.id,
                invocation=invocation,
                tool_call=outcome.tool_call,
            )
            return outcome.result

        async def emit_kernel_event(
            event_type: KernelEventType,
            payload: dict,
        ) -> None:
            session.add(
                Event(
                    type=f"agent.kernel.{event_type.value}",
                    content_item_id=content_item.id,
                    project_id=project_id,
                    payload={
                        "task_id": task.id,
                        "run_id": run_id,
                        "invocation_id": invocation.id,
                        "agent_code": code.value,
                        "account_id": account.id,
                        **payload,
                    },
                )
            )
            await session.commit()

        try:
            kernel_result = await specialist_kernel.run(
                session,
                org_id=user.org_id,
                runner=runner,
                context=AgentContext(
                    content_item_id=content_item.id,
                    task_id=task.id,
                    invocation_id=invocation.id,
                    trace_id=f"agent-run:{run_id}" if run_id is not None else task.thread_id,
                    project_id=project_id,
                    account_id=account.id,
                    request=purpose,
                    upstream=operating_context,
                    knowledge=knowledge_context(knowledge_rows),
                    budget=self._budget(task),
                ),
                policy=kernel_policy,
                available_tools=available_tools,
                execute_tool=execute_tool if available_tools else None,
                emit_event=emit_kernel_event,
            )
            payload = kernel_result.payload
        except Exception as exc:  # noqa: BLE001 - persist the durable failure ledger
            await session.rollback()
            await session.refresh(task)
            await session.refresh(task, attribute_names=["brief"])
            failed = await session.get(AgentInvocation, invocation_id)
            if failed is not None:
                failed.status = AgentInvocationStatus.FAILED
                failed.failure_reason = type(exc).__name__
                failed.finished_at = datetime.now(UTC)
                failed_lifecycle = await self._record_runtime_lifecycle(
                    session,
                    task=task,
                    account_id=account_id,
                    run_id=run_id,
                    invocation=failed,
                    event_type="brain.runtime.subagent_failed",
                    payload={
                        "message": f"{spec.name}处理失败。",
                        "agent_code": code.value,
                        "agent_name": spec.name,
                        "invocation_id": failed.id,
                    },
                    semantic_suffix="failed",
                )
                await session.commit()
                await self._publish_runtime_lifecycle(failed_lifecycle)
            raise AgentHarnessError(f"{spec.name} execution failed") from exc

        payload_dict = payload.model_dump(mode="json")
        if trace_only:
            summary = self._payload_summary(payload_dict)
            invocation.status = AgentInvocationStatus.DONE
            invocation.output_summary = summary
            await self._persist_trace_output(
                session,
                task=task,
                invocation=invocation,
                payload=payload_dict,
            )
            invocation.finished_at = datetime.now(UTC)
            completed_lifecycle = await self._record_runtime_lifecycle(
                session,
                task=task,
                account_id=account.id,
                run_id=run_id,
                invocation=invocation,
                event_type="brain.runtime.subagent_completed",
                payload={
                    "message": f"{spec.name}已完成本轮处理。",
                    "agent_code": code.value,
                    "agent_name": spec.name,
                    "invocation_id": invocation.id,
                    "summary": summary,
                    "trace_only": True,
                },
                semantic_suffix="completed",
            )
            session.add(
                Event(
                    type="agent.harness.completed",
                    content_item_id=content_item.id,
                    project_id=project_id,
                    payload={
                        "task_id": task.id,
                        "run_id": run_id,
                        "invocation_id": invocation.id,
                        "agent_code": code.value,
                        "trace_only": True,
                        "kernel_rounds": kernel_result.rounds,
                        "kernel_tool_calls": kernel_result.tool_calls,
                    },
                )
            )
            await session.commit()
            await self._publish_runtime_lifecycle(completed_lifecycle)
            return AgentHarnessResult(
                task=task,
                invocation=invocation,
                deliverable=None,
                acceptance=None,
                knowledge_sources=knowledge_rows,
                output=payload_dict,
            )

        deliverable = await write_runtime_deliverable(
            session,
            scope=scope,
            content=content_item,
            agent_code=code.value,
            deliverable_type=spec.deliverable_type,
            status=DeliverableStatus.PENDING_REVIEW,
            payload=payload_dict,
            note="Generated by the audited Agent Harness; awaiting human acceptance.",
        )
        summary = self._payload_summary(payload_dict)
        acceptance = DeliverableAcceptance(
            task_id=task.id,
            deliverable_id=deliverable.id,
            agent_code=code,
            agent_name=spec.name,
            deliverable_type=spec.deliverable_type,
            title=spec.deliverable_title,
            version=deliverable.version,
            summary=summary,
            acceptance_items=self._acceptance_items(payload_dict),
            history_versions=[
                {
                    "version": deliverable.version,
                    "status": DeliverableStatus.PENDING_REVIEW.value,
                    "note": "Awaiting human acceptance",
                    "created_at": datetime.now(UTC).isoformat(),
                }
            ],
            status=DeliverableAcceptanceStatus.PENDING,
            brain_rejudge_basis=[
                (
                    "The result is scoped to the selected project and account."
                    if project is not None
                    else "The result is scoped to the selected account."
                ),
                "It will not overwrite an approved result before human acceptance.",
            ],
        )
        session.add(acceptance)
        await session.flush()
        if project is not None and project.client_id is not None:
            await record_knowledge_citations(
                session,
                rows=knowledge_rows,
                org_id=user.org_id,
                client_id=project.client_id,
                project_id=project.id,
                task_id=task.id,
                invocation_id=invocation.id,
                agent_code=code.value,
                context=purpose[:500],
            )

        invocation.status = AgentInvocationStatus.DONE
        invocation.output_summary = summary
        invocation.finished_at = datetime.now(UTC)
        completed_lifecycle = await self._record_runtime_lifecycle(
            session,
            task=task,
            account_id=account.id,
            run_id=run_id,
            invocation=invocation,
            event_type="brain.runtime.subagent_completed",
            payload={
                "message": f"{spec.name}已完成本轮处理。",
                "agent_code": code.value,
                "agent_name": spec.name,
                "invocation_id": invocation.id,
                "summary": summary,
            },
            semantic_suffix="completed",
        )
        session.add(
            Event(
                type="agent.harness.completed",
                content_item_id=content_item.id,
                project_id=project_id,
                payload={
                    "task_id": task.id,
                    "run_id": run_id,
                    "invocation_id": invocation.id,
                    "agent_code": code.value,
                    "deliverable_id": deliverable.id,
                    "acceptance_id": acceptance.id,
                    "kernel_rounds": kernel_result.rounds,
                    "kernel_tool_calls": kernel_result.tool_calls,
                },
            )
        )
        await add_approval_requested(
            session,
            org_id=user.org_id,
            project_id=project_id,
            content_item_id=content_item.id,
            approval_kind="deliverable",
            source_id=acceptance.id,
            title=acceptance.title,
            body=f"{spec.name} has completed its work. Confirm whether to accept it.",
        )
        await session.commit()
        await self._publish_runtime_lifecycle(completed_lifecycle)
        return AgentHarnessResult(
            task=task,
            invocation=invocation,
            deliverable=deliverable,
            acceptance=acceptance,
            knowledge_sources=knowledge_rows,
            output=payload_dict,
        )

    @staticmethod
    async def _record_runtime_lifecycle(
        session: AsyncSession,
        *,
        task: BrainTask,
        account_id: int,
        run_id: int | None,
        invocation: AgentInvocation,
        event_type: str,
        payload: dict,
        semantic_suffix: str,
    ) -> _RuntimeLifecycleBroadcast | None:
        if run_id is None:
            return None
        run = await session.get(AgentRun, run_id)
        if run is None or run.org_id != task.org_id:
            return None
        event, created = await record_runtime_event_once(
            session,
            org_id=task.org_id,
            account_id=account_id,
            run_id=run.id,
            client_message_id=run.client_message_id,
            event_type=event_type,
            semantic_key=f"invocation:{invocation.id}:{semantic_suffix}",
            payload={"task_id": task.id, **payload},
            content_item_id=task.content_item_id,
            project_id=task.brief.project_id if task.brief else None,
        )
        if not created:
            return None
        return _RuntimeLifecycleBroadcast(
            event_id=event.id,
            event_type=event.type,
            payload=dict(event.payload or {}),
            content_item_id=event.content_item_id,
            project_id=event.project_id,
        )

    @staticmethod
    async def _publish_runtime_lifecycle(
        lifecycle: _RuntimeLifecycleBroadcast | None,
    ) -> None:
        if lifecycle is None:
            return
        await publish_realtime_event(
            lifecycle.event_type,
            lifecycle.payload,
            content_item_id=lifecycle.content_item_id,
            project_id=lifecycle.project_id,
            event_id=lifecycle.event_id,
        )

    async def _project_successful_tool_call(
        self,
        session: AsyncSession,
        *,
        task: BrainTask,
        account_id: int,
        invocation: AgentInvocation,
        tool_call: AgentToolCall,
    ) -> None:
        if tool_call.status != "success":
            return
        result = dict((tool_call.meta or {}).get("result") or {})
        broadcast = await self._record_runtime_lifecycle(
            session,
            task=task,
            account_id=account_id,
            run_id=invocation.run_id,
            invocation=invocation,
            event_type="brain.runtime.tool_completed",
            payload={
                "message": f"{tool_call.tool_name} completed.",
                "invocation_id": invocation.id,
                "tool_call_id": tool_call.id,
                "tool_code": tool_call.tool_code,
                "summary": tool_call.output_summary,
                "result": result,
            },
            semantic_suffix=f"tool_call:{tool_call.id}:completed",
        )
        if broadcast is None:
            return
        await session.commit()
        await self._publish_runtime_lifecycle(broadcast)

    async def _repair_successful_tool_projections(
        self,
        session: AsyncSession,
        *,
        task: BrainTask,
        account_id: int,
        invocation: AgentInvocation,
    ) -> None:
        tool_calls = (
            await session.scalars(
                select(AgentToolCall)
                .where(
                    AgentToolCall.invocation_id == invocation.id,
                    AgentToolCall.status == "success",
                )
                .order_by(AgentToolCall.id)
            )
        ).all()
        for tool_call in tool_calls:
            await self._project_successful_tool_call(
                session,
                task=task,
                account_id=account_id,
                invocation=invocation,
                tool_call=tool_call,
            )

    @staticmethod
    def _autonomous_runtime_tool_codes(management: dict) -> set[str]:
        """Translate configured expert capabilities into executable kernel tools.

        Only auto tools enter the specialist loop. Confirm/manual capabilities
        remain owned by the main Agent and the existing approval ledger.
        """
        return {
            runtime_code
            for management_code, permission_mode in management["tool_permissions"].items()
            if permission_mode == "auto"
            and (runtime_code := _MANAGEMENT_TO_RUNTIME_TOOL.get(management_code)) is not None
        }

    @staticmethod
    def _task_scope(task: BrainTask) -> tuple[int | None, int]:
        brief = task.brief
        if brief is None or len(brief.account_ids) != 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Agent execution requires exactly one account",
            )
        return brief.project_id, brief.account_ids[0]

    @staticmethod
    async def _require_scope(
        session: AsyncSession,
        *,
        user: User,
        project_id: int | None,
        account_id: int,
    ) -> tuple[Project | None, Account]:
        account = await require_account_access(session, user, account_id, roles=_OPERATING_ROLES)
        if account.status != AccountStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The selected account is inactive",
            )
        if account.auth_status != "authorized":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The selected account is not authorized",
            )
        if project_id is None:
            return None, account

        project = await require_project_access(session, user, project_id, roles=_OPERATING_ROLES)
        linked_id = await session.scalar(
            select(ProjectAccount.id).where(
                ProjectAccount.project_id == project.id,
                ProjectAccount.account_id == account.id,
            )
        )
        if account.project_id != project.id and linked_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Account is not assigned to the selected project",
            )
        return project, account

    @staticmethod
    async def _ensure_content_item(
        session: AsyncSession,
        *,
        task: BrainTask,
        user: User,
        project_id: int | None,
        account_id: int,
        spec: AgentSpec,
    ) -> ContentItem:
        if task.content_item_id is not None:
            current = await session.get(ContentItem, task.content_item_id)
            if current is not None:
                if current.project_id != project_id or current.account_id != account_id:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Task content scope does not match the selected account",
                    )
                return current
        current = ContentItem(
            project_id=project_id,
            created_by_id=user.id,
            account_id=account_id,
            title=task.title,
            current_stage=spec.stage,
            status=ContentStatus.IN_PROGRESS,
        )
        session.add(current)
        await session.flush()
        task.content_item_id = current.id
        return current

    @staticmethod
    def _account_scoped_upstream(upstream: dict | None, *, account_id: int) -> dict:
        if not upstream:
            return {}
        packet = dict(upstream)
        tool_results = packet.get("tool_results")
        if not isinstance(tool_results, dict):
            return packet
        items = tool_results.get("items")
        if not isinstance(items, list):
            return packet
        for item in items:
            if not isinstance(item, dict):
                continue
            result = item.get("result")
            if (
                item.get("tool_code") in _MANAGEMENT_TO_RUNTIME_TOOL.values()
                and isinstance(result, dict)
                and result.get("account_id") != account_id
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Tool result does not match the selected account",
                )
        return packet

    @staticmethod
    async def _find_invocation(
        session: AsyncSession, *, run_id: int, step_key: str, attempt: int
    ) -> AgentInvocation | None:
        return await session.scalar(
            select(AgentInvocation).where(
                AgentInvocation.run_id == run_id,
                AgentInvocation.step_key == step_key,
                AgentInvocation.attempt == attempt,
            )
        )

    @staticmethod
    async def _existing_result(
        session: AsyncSession, task: BrainTask, invocation: AgentInvocation
    ) -> AgentHarnessResult:
        if invocation.task_id != task.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Invocation does not belong to the selected task",
            )
        if invocation.status != AgentInvocationStatus.DONE:
            raise AgentStepInProgress(
                f"Agent step {invocation.step_key or invocation.id} is {invocation.status.value}"
            )
        acceptance = await session.scalar(
            select(DeliverableAcceptance)
            .where(
                DeliverableAcceptance.task_id == task.id,
                DeliverableAcceptance.agent_code == invocation.agent_code,
            )
            .order_by(DeliverableAcceptance.id.desc())
        )
        if acceptance is None or acceptance.deliverable_id is None:
            raise AgentHarnessError("Completed invocation has no acceptance ledger")
        deliverable = await session.get(Deliverable, acceptance.deliverable_id)
        if deliverable is None:
            raise AgentHarnessError("Completed invocation has no deliverable")
        return AgentHarnessResult(
            task,
            invocation,
            deliverable,
            acceptance,
            [],
            dict(deliverable.payload or {}),
        )

    @staticmethod
    async def _existing_trace_result(
        session: AsyncSession,
        task: BrainTask,
        invocation: AgentInvocation,
    ) -> AgentHarnessResult:
        for item in reversed(invocation.upstream or []):
            if isinstance(item, dict) and isinstance(item.get("trace_only_output"), dict):
                return AgentHarnessResult(
                    task=task,
                    invocation=invocation,
                    deliverable=None,
                    acceptance=None,
                    knowledge_sources=[],
                    output=dict(item["trace_only_output"]),
                )
        if invocation.run_id is not None:
            run = await session.get(AgentRun, invocation.run_id)
            if run is not None:
                result_payload = dict(run.result_payload or {})
                trace_outputs = result_payload.get("trace_only_outputs")
                if isinstance(trace_outputs, dict):
                    payload = trace_outputs.get(AgentHarness._trace_result_key(invocation))
                    if isinstance(payload, dict):
                        return AgentHarnessResult(
                            task=task,
                            invocation=invocation,
                            deliverable=None,
                            acceptance=None,
                            knowledge_sources=[],
                            output=dict(payload),
                        )
        raise AgentHarnessError("trace-only specialist result is incomplete")

    @staticmethod
    async def _persist_trace_output(
        session: AsyncSession,
        *,
        task: BrainTask,
        invocation: AgentInvocation,
        payload: dict[str, Any],
    ) -> None:
        upstream = list(invocation.upstream or [])
        upstream.append({"trace_only_output": dict(payload)})
        invocation.upstream = upstream
        if invocation.run_id is None:
            return
        run = await session.get(AgentRun, invocation.run_id)
        if run is None or run.org_id != task.org_id:
            return
        result_payload = dict(run.result_payload or {})
        trace_outputs = result_payload.get("trace_only_outputs")
        safe_outputs = dict(trace_outputs) if isinstance(trace_outputs, dict) else {}
        safe_outputs[AgentHarness._trace_result_key(invocation)] = payload
        result_payload["trace_only_outputs"] = safe_outputs
        run.result_payload = result_payload

    @staticmethod
    def _trace_result_key(invocation: AgentInvocation) -> str:
        return invocation.step_key or str(invocation.id)

    @staticmethod
    async def _model_name(session: AsyncSession, org_id: int, code: AgentCode) -> str:
        config = await session.scalar(
            select(ModelConfig).where(
                ModelConfig.org_id == org_id,
                ModelConfig.agent_code == code,
            )
        )
        return config.primary_model if config is not None else "deepseek-chat"

    @staticmethod
    def _budget(task: BrainTask) -> dict:
        if task.brief is None or task.brief.budget is None:
            return {}
        return {"task_budget": float(task.brief.budget)}

    @staticmethod
    def _payload_summary(payload: dict) -> str:
        for key in ("account_persona", "summary", "title", "objective", "visual_style"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return "The specialist generated a structured result for review."

    @staticmethod
    def _acceptance_items(payload: dict) -> list[dict]:
        items: list[dict] = []
        for key, value in payload.items():
            if len(items) >= 6:
                break
            if value in (None, "", [], {}):
                continue
            items.append(
                {
                    "label": key.replace("_", " "),
                    "status": "pending",
                    "note": "Confirm that this item matches the selected account.",
                }
            )
        return items


agent_harness = AgentHarness()

__all__ = [
    "AGENT_SPECS",
    "AgentHarness",
    "AgentHarnessError",
    "AgentHarnessResult",
    "AgentStepInProgress",
    "agent_harness",
]
