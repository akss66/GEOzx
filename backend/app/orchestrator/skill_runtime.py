"""Bounded, durable execution runtime for business-facing Skills."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    AgentInvocation,
    AgentQualityScore,
    AgentRun,
    AgentToolCall,
    BrainTask,
    ContentItem,
    ConversationThread,
    ConversationTurn,
    Deliverable,
    SkillRun,
    TaskBrief,
    User,
)
from app.models.enums import (
    AgentCode,
    AgentInvocationStatus,
    BrainTaskStatus,
    BrainTaskType,
    ContentStage,
    ContentStatus,
    DeliverableStatus,
    DeliverableType,
)
from app.orchestrator.agent_harness import agent_harness
from app.orchestrator.ai_coo_critic import (
    CriticDisposition,
    ai_coo_critic_service,
)
from app.orchestrator.brain_intelligence import brain_intelligence
from app.orchestrator.runtime_tools import build_runtime_tool_adapter
from app.orchestrator.skills.account_inspection import (
    AccountInspectionCriticOutcome,
    AccountInspectionInput,
    AccountInspectionMetric,
    AccountInspectionReport,
)
from app.orchestrator.skills.registry import skill_registry
from app.orchestrator.tool_executor import DurableToolExecutor
from app.schemas.brain import RuntimeToolCall
from app.services.agent_runs import acquire_agent_run, heartbeat_agent_run, utc_now

_ACCOUNT_INSPECTION = "account_inspection"
_MAX_CRITIC_IMPROVEMENTS = 2
DataSufficiency = Literal["insufficient", "partial", "sufficient"]
log = logging.getLogger("dyflow.skill_runtime")


@dataclass(frozen=True)
class SkillExecutionResult:
    status: str
    skill_run_id: int
    task_id: int | None
    artifact_id: int | None
    artifact_type: str
    report: dict[str, Any]
    response: str
    error_code: str | None = None


@dataclass(frozen=True)
class _CriticResult:
    passed: bool
    score: int
    issues: list[str]
    suggestions: list[str]


class _ToolScopeMismatch(PermissionError):
    pass


class _SkillLeaseLost(RuntimeError):
    pass


class SkillRuntime:
    """Execute one frozen Skill graph without entering the strategy runtime."""

    def __init__(
        self,
        *,
        tool_executor: Any | None = None,
        harness: Any | None = None,
        critic: Any | None = None,
    ) -> None:
        self._tool_executor = tool_executor
        self._harness = harness or agent_harness
        self._critic = critic

    async def execute(
        self,
        session: AsyncSession,
        *,
        user: User,
        thread: ConversationThread,
        turn: ConversationTurn,
        run: AgentRun,
        skill_code: str,
        days: int = 30,
        lease_owner: str | None = None,
    ) -> SkillExecutionResult:
        self._require_scope(user, thread, turn, run)
        run_id = run.id
        definition = skill_registry.get(skill_code)
        if definition.code != _ACCOUNT_INSPECTION:
            raise KeyError(skill_code)
        frozen_input = AccountInspectionInput.model_validate({"days": days})
        idempotency_key = f"skill:{definition.code}:v{definition.version}"
        lease_owner = lease_owner or f"skill-run:{run_id}:{uuid4().hex}"
        existing = await session.scalar(
            select(SkillRun).where(
                SkillRun.run_id == run_id,
                SkillRun.idempotency_key == idempotency_key,
            )
        )
        if existing is not None and existing.status in {
            "blocked",
            "completed",
            "failed",
            "stopped",
            "waiting_permission",
            "waiting_user",
        }:
            return self._existing_result(existing)
        recovering = False
        if existing is not None and existing.status == "running":
            recovering = True
            claimed = (
                run
                if run.status == "running" and run.lease_owner == lease_owner
                else await acquire_agent_run(
                    session,
                    run_id,
                    worker_id=lease_owner,
                    lease_seconds=settings.agent_run_lease_seconds,
                )
            )
            if claimed is None:
                await session.refresh(existing)
                return self._existing_result(existing)
            run = claimed

        task, content = await self._compatibility_task(
            session,
            user=user,
            thread=thread,
            turn=turn,
            run=run,
        )
        skill_run = existing
        if skill_run is None:
            skill_run = SkillRun(
                org_id=user.org_id,
                thread_id=thread.id,
                turn_id=turn.id,
                run_id=run_id,
                task_id=task.id,
                idempotency_key=idempotency_key,
                skill_code=definition.code,
                skill_version=definition.version,
                status="running",
                input_snapshot={
                    "account_id": thread.account_id,
                    **frozen_input.model_dump(mode="json"),
                },
                output_snapshot={},
            )
            session.add(skill_run)
            now = utc_now()
            run.status = "running"
            run.phase = "skill_runtime"
            run.attempt += 1
            run.lease_owner = lease_owner
            run.leased_until = now + timedelta(
                seconds=max(1, settings.agent_run_lease_seconds)
            )
            run.heartbeat_at = now
            run.started_at = run.started_at or now
            run.next_retry_at = None
            try:
                await session.commit()
                await session.refresh(skill_run)
            except IntegrityError as exc:
                await session.rollback()
                skill_run = await session.scalar(
                    select(SkillRun).where(
                        SkillRun.run_id == run_id,
                        SkillRun.idempotency_key == idempotency_key,
                    )
                )
                if skill_run is None:
                    raise
                if (
                    skill_run.skill_code != definition.code
                    or skill_run.skill_version != definition.version
                ):
                    raise PermissionError(
                        "concurrent SkillRun winner changed the frozen definition"
                    ) from exc
                return self._existing_result(skill_run)
        elif skill_run.task_id != task.id:
            raise PermissionError("SkillRun task ownership does not match")

        skill_run_id = skill_run.id
        task_id = task.id
        if recovering and await self._interrupt_ambiguous_side_effects(
            session,
            run=run,
            turn=turn,
            skill_run=skill_run,
            task=task,
        ):
            return self._existing_result(skill_run)
        try:
            return await self._execute_account_inspection(
                session,
                user=user,
                thread=thread,
                turn=turn,
                run=run,
                task=task,
                content=content,
                skill_run=skill_run,
                days=frozen_input.days,
                lease_owner=lease_owner,
            )
        except _SkillLeaseLost:
            await session.rollback()
            persisted = await session.get(SkillRun, skill_run_id)
            if persisted is None:
                raise
            return self._existing_result(persisted)
        except Exception as exc:
            log.exception(
                "Skill execution failed",
                extra={
                    "skill_code": definition.code,
                    "skill_run_id": skill_run_id,
                    "task_id": task_id,
                    "run_id": run_id,
                },
            )
            await session.rollback()
            persisted = await session.get(SkillRun, skill_run_id)
            persisted_task = await session.get(BrainTask, task_id)
            scope_mismatch = isinstance(exc, _ToolScopeMismatch)
            terminal_status = "blocked" if scope_mismatch else "failed"
            error_code = (
                "TOOL_RESULT_SCOPE_MISMATCH" if scope_mismatch else type(exc).__name__
            )
            response = (
                "工具返回的数据不属于当前账号，账号体检已停止。"
                if scope_mismatch
                else "账号体检执行失败，请稍后重试。"
            )
            if persisted is not None:
                persisted.status = terminal_status
                persisted.error_code = error_code
                persisted.output_snapshot = {
                    "status": terminal_status,
                    "error_code": error_code,
                    "response": response,
                    "artifact_type": definition.artifact_type,
                }
            if persisted_task is not None:
                persisted_task.status = BrainTaskStatus.FAILED
                persisted_task.progress = 0
            await session.commit()
            return SkillExecutionResult(
                status=terminal_status,
                skill_run_id=skill_run_id,
                task_id=task_id,
                artifact_id=None,
                artifact_type=definition.artifact_type or "account_inspection_report",
                report={},
                response=response,
                error_code=error_code,
            )

    async def _execute_account_inspection(
        self,
        session: AsyncSession,
        *,
        user: User,
        thread: ConversationThread,
        turn: ConversationTurn,
        run: AgentRun,
        task: BrainTask,
        content: ContentItem,
        skill_run: SkillRun,
        days: int,
        lease_owner: str,
    ) -> SkillExecutionResult:
        tool_executor = self._tool_executor or DurableToolExecutor(
            build_runtime_tool_adapter()
        )
        tool_results: dict[str, dict[str, Any]] = {}
        for tool_code, arguments in (
            ("account.profile", {}),
            ("account.data_context", {"days": days}),
        ):
            await self._heartbeat(session, run=run, lease_owner=lease_owner)
            outcome = await tool_executor.execute(
                task=task,
                user=user,
                request=RuntimeToolCall(
                    tool_code=tool_code,
                    arguments=arguments,
                    purpose=f"一键账号体检读取 {tool_code}",
                    idempotency_key=f"{skill_run.id}:{tool_code}",
                ),
                project_id=thread.project_id,
                account_id=thread.account_id,
                agent_code=AgentCode.DECISION.value,
                skill_run_id=skill_run.id,
                thread_id=thread.id,
                turn_id=turn.id,
            )
            await self._heartbeat(session, run=run, lease_owner=lease_owner)
            if outcome.status != "success" or outcome.result is None:
                return await self._pause_for_tool(
                    session,
                    skill_run=skill_run,
                    task=task,
                    status=outcome.status,
                )
            self._require_tool_scope(outcome.result, thread.account_id)
            tool_results[tool_code] = dict(outcome.result)
            if isinstance(outcome.tool_call, AgentToolCall):
                outcome.tool_call.skill_run_id = skill_run.id
                outcome.tool_call.thread_id = thread.id
                outcome.tool_call.turn_id = turn.id
                await session.commit()

        data_context = tool_results["account.data_context"]
        evidence_refs = _evidence_refs(data_context)
        expert_results: list[Any] = []
        upstream_outputs: list[dict[str, Any]] = []
        tool_packet = [
            {"tool_code": code, "result": value} for code, value in tool_results.items()
        ]
        for index, code in enumerate(
            (
                AgentCode.OPERATOR,
                AgentCode.POSITIONING,
                AgentCode.CONTENT_DIRECTOR,
            )
        ):
            await self._heartbeat(session, run=run, lease_owner=lease_owner)
            result = await self._harness.execute(
                session,
                user=user,
                task=task,
                code=code,
                purpose="基于所选账号证据完成一键账号体检，不得编造数据。",
                evidence_refs=[_evidence_label(item) for item in evidence_refs],
                run_id=run.id,
                step_key=f"account-inspection:{index}:{code.value}",
                attempt=0,
                upstream={
                    "tool_results": {"items": tool_packet},
                    "expert_outputs": upstream_outputs,
                },
                skill_run_id=skill_run.id,
                thread_id=thread.id,
                turn_id=turn.id,
                trace_only=True,
            )
            await self._heartbeat(session, run=run, lease_owner=lease_owner)
            await self._attach_expert_provenance(
                session,
                result=result,
                thread=thread,
                turn=turn,
                run=run,
                skill_run=skill_run,
            )
            expert_results.append(result)
            upstream_outputs.append(
                {
                    "agent_code": code.value,
                    "summary": result.invocation.output_summary
                    if hasattr(result.invocation, "output_summary")
                    else "",
                    "payload": dict(result.output or {}),
                }
            )

        latest_result = expert_results[-1]
        critic_history: list[_CriticResult] = []
        report = _build_report(
            account_id=thread.account_id,
            days=days,
            data_context=data_context,
            expert_results=expert_results,
            evidence_refs=evidence_refs,
            critic=_CriticResult(False, 0, [], []),
            critic_iterations=1,
        )
        for iteration in range(_MAX_CRITIC_IMPROVEMENTS + 1):
            await self._heartbeat(session, run=run, lease_owner=lease_owner)
            review = await self._review(
                session,
                user=user,
                task=task,
                invocation=latest_result.invocation,
                deliverable_id=None,
                report=report,
                evidence_refs=evidence_refs,
                iteration=iteration,
            )
            await self._heartbeat(session, run=run, lease_owner=lease_owner)
            critic_history.append(review)
            report = report.model_copy(
                update={
                    "critic": AccountInspectionCriticOutcome(
                        passed=review.passed,
                        score=review.score,
                        iterations=iteration + 1,
                        issues=review.issues,
                        suggestions=review.suggestions,
                    )
                }
            )
            if review.passed:
                break
            if iteration == _MAX_CRITIC_IMPROVEMENTS:
                return await self._block_after_critic(
                    session,
                    skill_run=skill_run,
                    task=task,
                    report=report,
                )
            await self._heartbeat(session, run=run, lease_owner=lease_owner)
            latest_result = await self._harness.execute(
                session,
                user=user,
                task=task,
                code=AgentCode.CONTENT_DIRECTOR,
                purpose="按质量审核意见修订账号体检建议，不得编造数据。",
                evidence_refs=[_evidence_label(item) for item in evidence_refs],
                run_id=run.id,
                step_key=(
                    f"account-inspection:critic-revision:{AgentCode.CONTENT_DIRECTOR.value}"
                ),
                attempt=iteration + 1,
                upstream={
                    "tool_results": {"items": tool_packet},
                    "critic": {
                        "issues": review.issues,
                        "suggestions": review.suggestions,
                    },
                },
                skill_run_id=skill_run.id,
                thread_id=thread.id,
                turn_id=turn.id,
                trace_only=True,
            )
            await self._heartbeat(session, run=run, lease_owner=lease_owner)
            await self._attach_expert_provenance(
                session,
                result=latest_result,
                thread=thread,
                turn=turn,
                run=run,
                skill_run=skill_run,
            )
            expert_results.append(latest_result)

        await self._heartbeat(session, run=run, lease_owner=lease_owner)
        final_deliverable = Deliverable(
            content_item_id=content.id,
            thread_id=thread.id,
            turn_id=turn.id,
            run_id=run.id,
            skill_run_id=skill_run.id,
            agent_code=AgentCode.DECISION.value,
            type=DeliverableType.REVIEW_REPORT,
            version=1,
            status=DeliverableStatus.PENDING_REVIEW,
            payload=_review_report_payload(report),
            note=(
                "business_artifact_type=account_inspection_report; "
                "generated by account_inspection Skill"
            ),
        )
        session.add(final_deliverable)
        await session.flush()
        quality = await session.scalar(
            select(AgentQualityScore)
            .where(
                AgentQualityScore.skill_run_id == skill_run.id,
                AgentQualityScore.passed.is_(True),
            )
            .order_by(AgentQualityScore.iteration.desc())
        )
        if quality is not None:
            quality.deliverable_id = final_deliverable.id

        task.status = BrainTaskStatus.COMPLETED
        task.progress = 100
        task.current_focus = "一键账号体检已完成。"
        skill_run.status = "completed"
        skill_run.error_code = None
        skill_run.quality_score = Decimal(str(report.critic.score / 100))
        output = {
            "status": "completed",
            "task_id": task.id,
            "artifact_id": final_deliverable.id,
            "artifact_type": "account_inspection_report",
            "report": report.model_dump(mode="json"),
            "response": "账号体检已完成，正式体检报告已生成。",
        }
        skill_run.output_snapshot = output
        await session.commit()
        return self._existing_result(skill_run)

    async def _review(
        self,
        session: AsyncSession,
        *,
        user: User,
        task: BrainTask,
        invocation: Any,
        deliverable_id: int | None,
        report: AccountInspectionReport,
        evidence_refs: list[dict[str, Any]],
        iteration: int,
    ) -> _CriticResult:
        if self._critic is not None:
            result = await self._critic.review(
                session=session,
                task=task,
                invocation=invocation,
                report=report.model_dump(mode="json"),
                evidence_refs=evidence_refs,
                iteration=iteration,
            )
            return _CriticResult(
                passed=bool(result.passed),
                score=int(result.score),
                issues=list(result.issues),
                suggestions=list(result.suggestions),
            )

        model_review = await brain_intelligence.review_expert_output(
            session,
            user.org_id,
            goal=task.title,
            expert_code=AgentCode.CONTENT_DIRECTOR.value,
            expert_name="内容策略专家",
            deliverable=report.model_dump(mode="json"),
            situation={},
            strategy={},
            evidence_refs=evidence_refs,
            iteration=iteration,
        )
        recorded = await ai_coo_critic_service.record(
            session,
            task=task,
            invocation=invocation,
            deliverable_id=deliverable_id,
            evaluation=model_review.evaluation,
            iteration=iteration,
            evidence_refs=evidence_refs,
            prompt_id=model_review.prompt.spec.id,
            prompt_version=model_review.prompt.spec.version,
            prompt_hash=model_review.prompt.content_hash,
            critic_model=model_review.model,
        )
        score = recorded.score
        score.thread_id = invocation.thread_id
        score.turn_id = invocation.turn_id
        score.run_id = invocation.run_id
        score.skill_run_id = invocation.skill_run_id
        await session.commit()
        return _CriticResult(
            passed=recorded.disposition is CriticDisposition.PASS,
            score=int(score.score),
            issues=list(score.issues or []),
            suggestions=list(score.suggestions or []),
        )

    @staticmethod
    async def _heartbeat(
        session: AsyncSession,
        *,
        run: AgentRun,
        lease_owner: str,
    ) -> None:
        renewed = await heartbeat_agent_run(
            session,
            run.id,
            worker_id=lease_owner,
            lease_seconds=settings.agent_run_lease_seconds,
        )
        if not renewed:
            raise _SkillLeaseLost("Skill execution lease ownership changed")

    @staticmethod
    async def _interrupt_ambiguous_side_effects(
        session: AsyncSession,
        *,
        run: AgentRun,
        turn: ConversationTurn,
        skill_run: SkillRun,
        task: BrainTask,
    ) -> bool:
        tool_calls = list(
            await session.scalars(
                select(AgentToolCall)
                .where(
                    AgentToolCall.skill_run_id == skill_run.id,
                    AgentToolCall.status.in_({"planned", "running"}),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        invocations = list(
            await session.scalars(
                select(AgentInvocation)
                .where(
                    AgentInvocation.skill_run_id == skill_run.id,
                    AgentInvocation.status.in_(
                        {
                            AgentInvocationStatus.QUEUED,
                            AgentInvocationStatus.RUNNING,
                        }
                    ),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        if not tool_calls and not invocations:
            return False

        now = datetime.now(UTC)
        error_code = "SKILL_EXECUTION_INTERRUPTED"
        response = (
            "账号体检执行被中断。为避免重复调用状态不明的工具或专家，"
            "本次执行已安全关闭，请重新发起一次新的体检。"
        )
        for tool_call in tool_calls:
            tool_call.status = "failed"
            tool_call.error = error_code
            tool_call.finished_at = now
        for invocation in invocations:
            invocation.status = AgentInvocationStatus.FAILED
            invocation.failure_reason = error_code
            invocation.finished_at = now
        task.status = BrainTaskStatus.FAILED
        task.progress = 0
        task.current_focus = response
        skill_run.status = "failed"
        skill_run.error_code = error_code
        output_snapshot = {
            "status": "failed",
            "task_id": task.id,
            "artifact_id": None,
            "artifact_type": "account_inspection_report",
            "report": {},
            "response": response,
            "error_code": error_code,
        }
        skill_run.output_snapshot = output_snapshot
        turn.assistant_response = response
        run.status = "failed"
        run.phase = "failed"
        run.finished_at = now
        run.lease_owner = None
        run.leased_until = None
        run.next_retry_at = None
        run.heartbeat_at = now
        run.error_code = error_code
        run.error_detail = None
        run.result_payload = {
            "mode": "skill",
            "status": "failed",
            "response": response,
            "task_id": task.id,
            "projections": [
                {
                    "type": "execution_blocked",
                    "artifact_type": "account_inspection_report",
                    "skill_run_id": skill_run.id,
                    "code": error_code,
                }
            ],
            "error_code": error_code,
        }
        await session.commit()
        return True

    @staticmethod
    async def _attach_expert_provenance(
        session: AsyncSession,
        *,
        result: Any,
        thread: ConversationThread,
        turn: ConversationTurn,
        run: AgentRun,
        skill_run: SkillRun,
    ) -> None:
        invocation = result.invocation
        for name, value in (
            ("skill_run_id", skill_run.id),
            ("thread_id", thread.id),
            ("turn_id", turn.id),
            ("run_id", run.id),
        ):
            if hasattr(invocation, name):
                setattr(invocation, name, value)
        await session.commit()

    @staticmethod
    async def _block_after_critic(
        session: AsyncSession,
        *,
        skill_run: SkillRun,
        task: BrainTask,
        report: AccountInspectionReport,
    ) -> SkillExecutionResult:
        task.status = BrainTaskStatus.FAILED
        task.progress = 0
        task.current_focus = "账号体检未通过质量审核，需要人工处理。"
        skill_run.status = "blocked"
        skill_run.error_code = "CRITIC_RETRY_EXHAUSTED"
        skill_run.output_snapshot = {
            "status": "blocked",
            "task_id": task.id,
            "artifact_id": None,
            "artifact_type": "account_inspection_report",
            "report": report.model_dump(mode="json"),
            "response": "体检报告连续未通过质量审核，已停止并等待人工处理。",
            "error_code": "CRITIC_RETRY_EXHAUSTED",
        }
        await session.commit()
        return SkillRuntime._existing_result(skill_run)

    @staticmethod
    async def _pause_for_tool(
        session: AsyncSession,
        *,
        skill_run: SkillRun,
        task: BrainTask,
        status: str,
    ) -> SkillExecutionResult:
        paused_status = (
            "waiting_permission" if status == "waiting_approval" else "failed"
        )
        error_code = (
            "TOOL_PERMISSION_REQUIRED"
            if paused_status == "waiting_permission"
            else "TOOL_EXECUTION_FAILED"
        )
        task.current_focus = (
            "账号体检正在等待工具授权。"
            if paused_status.startswith("waiting")
            else "账号体检工具执行失败。"
        )
        if paused_status == "failed":
            task.status = BrainTaskStatus.FAILED
        skill_run.status = paused_status
        skill_run.error_code = error_code
        skill_run.output_snapshot = {
            "status": paused_status,
            "task_id": task.id,
            "artifact_id": None,
            "artifact_type": "account_inspection_report",
            "report": {},
            "response": task.current_focus,
            "error_code": error_code,
        }
        await session.commit()
        return SkillRuntime._existing_result(skill_run)

    @staticmethod
    async def _compatibility_task(
        session: AsyncSession,
        *,
        user: User,
        thread: ConversationThread,
        turn: ConversationTurn,
        run: AgentRun,
    ) -> tuple[BrainTask, ContentItem]:
        task = await session.get(BrainTask, run.task_id) if run.task_id else None
        if task is None:
            content = ContentItem(
                project_id=thread.project_id,
                created_by_id=user.id,
                account_id=thread.account_id,
                title=f"账号体检：{turn.user_input[:240]}",
                current_stage=ContentStage.OPERATION,
                status=ContentStatus.IN_PROGRESS,
            )
            session.add(content)
            await session.flush()
            task = BrainTask(
                org_id=user.org_id,
                created_by_id=user.id,
                content_item_id=content.id,
                title=turn.user_input[:300],
                type=BrainTaskType.ACCOUNT_DIAGNOSIS,
                status=BrainTaskStatus.RUNNING,
                progress=0,
                current_focus="正在执行一键账号体检。",
                runtime_mode="skill",
            )
            task.brief = TaskBrief(
                goal=turn.user_input,
                project_id=thread.project_id,
                account_ids=[thread.account_id],
                platforms=[],
                cycle="current_turn",
                content_goal=turn.user_input,
                risk_constraints=[],
                expected_outputs=["account_inspection_report"],
                confirmation_actions=[],
            )
            session.add(task)
            await session.flush()
            run.task_id = task.id
            await session.commit()
            return task, content
        if task.org_id != user.org_id or task.content_item_id is None:
            raise PermissionError("existing compatibility task is unavailable")
        content_item_id = task.content_item_id
        persisted_content = await session.get(ContentItem, content_item_id)
        if persisted_content is None or persisted_content.account_id != thread.account_id:
            raise PermissionError("compatibility task account scope does not match")
        return task, persisted_content

    @staticmethod
    def _require_scope(
        user: User,
        thread: ConversationThread,
        turn: ConversationTurn,
        run: AgentRun,
    ) -> None:
        if (
            thread.org_id != user.org_id
            or thread.created_by_id != user.id
            or turn.org_id != user.org_id
            or turn.created_by_id != user.id
            or turn.thread_id != thread.id
            or run.org_id != user.org_id
            or run.requested_by_id != user.id
            or run.thread_id != thread.id
            or run.turn_id != turn.id
        ):
            raise PermissionError("Skill execution ownership does not match")

    @staticmethod
    def _require_tool_scope(result: dict[str, Any], account_id: int) -> None:
        if result.get("account_id") != account_id:
            raise _ToolScopeMismatch("tool result account scope does not match")

    @staticmethod
    def _existing_result(skill_run: SkillRun) -> SkillExecutionResult:
        output = dict(skill_run.output_snapshot or {})
        response = str(output.get("response") or "")
        if not response and skill_run.status == "running":
            response = "账号体检正在执行中，请稍候。"
        return SkillExecutionResult(
            status=str(output.get("status") or skill_run.status),
            skill_run_id=skill_run.id,
            task_id=skill_run.task_id,
            artifact_id=output.get("artifact_id"),
            artifact_type=str(
                output.get("artifact_type") or "account_inspection_report"
            ),
            report=dict(output.get("report") or {}),
            response=response,
            error_code=output.get("error_code") or skill_run.error_code,
        )


def _build_report(
    *,
    account_id: int,
    days: int,
    data_context: dict[str, Any],
    expert_results: list[Any],
    evidence_refs: list[dict[str, Any]],
    critic: _CriticResult,
    critic_iterations: int,
) -> AccountInspectionReport:
    metrics: list[AccountInspectionMetric] = []
    for name, item in (data_context.get("metrics") or {}).items():
        if not isinstance(item, dict) or item.get("value") is None:
            continue
        value = item["value"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        metrics.append(
            AccountInspectionMetric(
                name=str(name),
                value=value,
                evidence_refs=list(item.get("evidence_refs") or []),
            )
        )
    snapshot_count = int(data_context.get("content_snapshot_count") or 0)
    missing_data: list[str] = []
    if snapshot_count == 0:
        missing_data.append("缺少已确认的内容表现快照")
    if not metrics:
        missing_data.append("缺少可核验的账号核心指标")
    sufficiency: DataSufficiency = (
        "insufficient"
        if not metrics or snapshot_count == 0
        else ("partial" if len(metrics) < 3 else "sufficient")
    )
    summaries = [
        str(getattr(item.invocation, "output_summary", "") or "").strip()
        for item in expert_results
    ]
    findings = [item for item in summaries if item]
    if sufficiency == "insufficient":
        findings = ["当前只能确认数据缺口，尚不能形成账号表现或内容方向结论。"]
    if sufficiency == "insufficient":
        summary = (
            "现有数据不足，无法形成可靠的表现结论；本报告先列出缺失数据和补数动作。"
        )
        recommendations = ["先补齐账号指标和内容表现快照，再进行趋势与内容诊断。"]
        next_action = "补齐并确认最近30天账号及内容数据"
    else:
        summary = "已基于所选账号的可核验证据完成账号体检。"
        recommendations = findings[-2:] or ["围绕已有证据继续验证内容方向。"]
        next_action = "确认体检结论并选择一项优化建议进入执行"
    period = dict(data_context.get("period") or {"days": days})
    period.setdefault("days", days)
    return AccountInspectionReport(
        account_id=account_id,
        period=period,
        data_sufficiency=sufficiency,
        missing_data=missing_data,
        summary=summary,
        key_metrics=metrics,
        findings=findings,
        recommendations=recommendations,
        next_action=next_action,
        evidence_refs=evidence_refs,
        participating_experts=[
            AgentCode.OPERATOR.value,
            AgentCode.POSITIONING.value,
            AgentCode.CONTENT_DIRECTOR.value,
        ],
        critic=AccountInspectionCriticOutcome(
            passed=critic.passed,
            score=critic.score,
            iterations=critic_iterations,
            issues=critic.issues,
            suggestions=critic.suggestions,
        ),
    )


def _review_report_payload(report: AccountInspectionReport) -> dict[str, Any]:
    data = report.model_dump(mode="json")
    return {
        **data,
        "period": f"最近{report.period.get('days', 30)}天",
        "key_metrics": {item.name: item.value for item in report.key_metrics},
        "highlights": report.findings or ["当前没有足够数据形成表现亮点结论"],
        "issues": report.missing_data or report.critic.issues or ["未发现明确异常"],
        "optimization_suggestions": report.recommendations,
    }


def _evidence_refs(data_context: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for item in (data_context.get("metrics") or {}).values():
        if not isinstance(item, dict):
            continue
        for ref in item.get("evidence_refs") or []:
            if not isinstance(ref, dict):
                continue
            kind = str(ref.get("kind") or "")
            identifier = ref.get("id")
            if not kind or not isinstance(identifier, int):
                continue
            key = (kind, identifier)
            if key not in seen:
                seen.add(key)
                refs.append({"kind": kind, "id": identifier})
    for source in data_context.get("sources") or []:
        if not isinstance(source, dict):
            continue
        identifier = source.get("batch_id")
        if isinstance(identifier, int):
            key = ("data_import_batch", identifier)
            if key not in seen:
                seen.add(key)
                refs.append({"kind": key[0], "id": key[1]})
    return refs


def _evidence_label(ref: dict[str, Any]) -> str:
    return f"{ref.get('kind')}:{ref.get('id')}"


skill_runtime = SkillRuntime()

__all__ = ["SkillExecutionResult", "SkillRuntime", "skill_runtime"]
