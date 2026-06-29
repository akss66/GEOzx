"""编排引擎：驱动 ContentItem 沿 PIPELINE 流转。

- `start` / `advance`：推进到下一个未完成步骤；Agent 步执行并落版本化交付物；
  自动门直接放行；强制门创建待审 GateApproval 并阻塞（status=BLOCKED）。
- `approve_gate`：审批通过则续跑，否则保持阻塞。
- 每个关键节点发事件（经 T6 事件总线广播给前端看板）。`emit` 可注入便于测试。
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentContext
from app.core.events import publish_event
from app.models import AgentTask, ContentItem, Deliverable, GateApproval
from app.models.enums import (
    AgentTaskStatus,
    ContentStatus,
    DeliverableStatus,
    DeliverableType,
    GateStatus,
    GateType,
)
from app.orchestrator.gates import is_forced
from app.orchestrator.pipeline import PIPELINE, AgentStep

EmitFn = Callable[..., Awaitable[None]]


def _now() -> datetime:
    return datetime.now(UTC)


class OrchestrationEngine:
    def __init__(self, emit: EmitFn = publish_event) -> None:
        self._emit = emit

    # —— 公开入口 ——

    async def start(self, session: AsyncSession, content_item_id: int) -> ContentItem:
        ci = await session.get(ContentItem, content_item_id)
        if ci is None:
            raise ValueError(f"内容不存在: {content_item_id}")
        await self.advance(session, ci)
        return ci

    async def approve_gate(
        self,
        session: AsyncSession,
        approval_id: int,
        user_id: int | None,
        approved: bool,
        comment: str | None = None,
    ) -> ContentItem:
        ga = await session.get(GateApproval, approval_id)
        if ga is None or ga.status != GateStatus.PENDING:
            raise ValueError("质量门不存在或已决策")
        ga.status = GateStatus.APPROVED if approved else GateStatus.REJECTED
        ga.decided_by = user_id
        ga.decided_at = _now()
        ga.comment = comment
        ci = await session.get(ContentItem, ga.content_item_id)
        await session.commit()
        await self._emit(
            "gate.decided",
            {"gate": ga.gate.value, "approved": approved},
            content_item_id=ci.id,
        )
        if approved:
            ci.status = ContentStatus.IN_PROGRESS
            await session.commit()
            await self.advance(session, ci)
        return ci

    # —— 核心推进 ——

    async def advance(self, session: AsyncSession, ci: ContentItem) -> None:
        while True:
            idx = await self._next_step_index(session, ci.id)
            if idx is None:
                ci.status = ContentStatus.PUBLISHED
                await session.commit()
                await self._emit("pipeline.done", {}, content_item_id=ci.id)
                return

            step = PIPELINE[idx]
            if isinstance(step, AgentStep):
                await self._run_agent(session, ci, step)
                continue

            # GateStep
            if is_forced(step.gate):
                await self._block_on_gate(session, ci, step.gate)
                return
            await self._auto_pass_gate(session, ci, step.gate)

    # —— 步骤完成判定 ——

    async def _next_step_index(self, session: AsyncSession, ci_id: int) -> int | None:
        for i, step in enumerate(PIPELINE):
            if isinstance(step, AgentStep):
                done = await session.scalar(
                    select(AgentTask).where(
                        AgentTask.content_item_id == ci_id,
                        AgentTask.stage == step.stage,
                        AgentTask.status == AgentTaskStatus.DONE,
                    )
                )
                if done is None:
                    return i
            else:
                resolved = await session.scalar(
                    select(GateApproval).where(
                        GateApproval.content_item_id == ci_id,
                        GateApproval.gate == step.gate,
                        GateApproval.status.in_([GateStatus.APPROVED, GateStatus.AUTO_PASSED]),
                    )
                )
                if resolved is None:
                    return i
        return None

    # —— Agent 执行 ——

    async def _run_agent(self, session: AsyncSession, ci: ContentItem, step: AgentStep) -> None:
        task = AgentTask(
            content_item_id=ci.id,
            agent_code=step.agent.code,
            stage=step.stage,
            status=AgentTaskStatus.RUNNING,
            started_at=_now(),
        )
        session.add(task)
        ci.current_stage = step.stage
        ci.status = ContentStatus.IN_PROGRESS
        await session.flush()

        ctx = AgentContext(content_item_id=ci.id, upstream=await self._upstream(session, ci.id))
        try:
            result = await step.agent.run(ctx)
        except Exception as exc:  # noqa: BLE001 — 记录失败并停下
            task.status = AgentTaskStatus.FAILED
            task.error = str(exc)
            task.finished_at = _now()
            ci.status = ContentStatus.BLOCKED
            await session.commit()
            await self._emit(
                "agent.failed",
                {"stage": step.stage.value, "error": str(exc)},
                content_item_id=ci.id,
            )
            raise

        version = await self._next_version(session, ci.id, step.agent.output_type)
        deliverable = Deliverable(
            content_item_id=ci.id,
            agent_code=step.agent.code,
            type=step.agent.output_type,
            version=version,
            status=DeliverableStatus.DRAFT,
            payload=result.model_dump(),
        )
        session.add(deliverable)
        await session.flush()

        task.status = AgentTaskStatus.DONE
        task.finished_at = _now()
        task.output_deliverable_id = deliverable.id
        await session.commit()

        await self._emit(
            "agent.done",
            {"stage": step.stage.value, "deliverable_id": deliverable.id},
            content_item_id=ci.id,
        )

    async def _upstream(self, session: AsyncSession, ci_id: int) -> dict[str, dict]:
        rows = (
            await session.scalars(select(Deliverable).where(Deliverable.content_item_id == ci_id))
        ).all()
        return {r.type.value: r.payload for r in rows}

    async def _next_version(self, session: AsyncSession, ci_id: int, dtype: DeliverableType) -> int:
        current = await session.scalar(
            select(func.max(Deliverable.version)).where(
                Deliverable.content_item_id == ci_id, Deliverable.type == dtype
            )
        )
        return (current or 0) + 1

    # —— 质量门 ——

    async def _block_on_gate(self, session: AsyncSession, ci: ContentItem, gate: GateType) -> None:
        # 幂等：若该门已有待审记录，则只保持阻塞，不重复创建/发事件
        existing = await session.scalar(
            select(GateApproval).where(
                GateApproval.content_item_id == ci.id,
                GateApproval.gate == gate,
                GateApproval.status == GateStatus.PENDING,
            )
        )
        if existing is None:
            session.add(GateApproval(content_item_id=ci.id, gate=gate, status=GateStatus.PENDING))
        ci.status = ContentStatus.BLOCKED
        await session.commit()
        if existing is None:
            await self._emit("gate.pending", {"gate": gate.value}, content_item_id=ci.id)

    async def _auto_pass_gate(self, session: AsyncSession, ci: ContentItem, gate: GateType) -> None:
        session.add(GateApproval(content_item_id=ci.id, gate=gate, status=GateStatus.AUTO_PASSED))
        await session.commit()
        await self._emit("gate.passed", {"gate": gate.value}, content_item_id=ci.id)


# 默认引擎实例（事件经 arq 总线广播）
engine = OrchestrationEngine()
