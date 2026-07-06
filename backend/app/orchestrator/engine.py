"""编排引擎：驱动 ContentItem 沿 PIPELINE 流转。

- `start` / `advance`：推进到下一个未完成步骤；Agent 步执行并落版本化交付物；
  自动门直接放行；强制门创建待审 GateApproval 并阻塞（status=BLOCKED）。
- `approve_gate`：审批通过则续跑，否则保持阻塞。
- 每个关键节点发事件（经 T6 事件总线广播给前端看板）。`emit` 可注入便于测试。
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentContext
from app.compliance.checker import check_script
from app.core.events import publish_event
from app.models import (
    AgentTask,
    ComplianceCheck,
    ContentItem,
    Deliverable,
    GateApproval,
    KnowledgeEntry,
    OptimizationSuggestion,
    Project,
)
from app.models.enums import (
    AgentTaskStatus,
    ContentStage,
    ContentStatus,
    DeliverableStatus,
    DeliverableType,
    GateStatus,
    GateType,
    OptimizationSuggestionStatus,
)
from app.orchestrator.gates import is_forced
from app.orchestrator.pipeline import PIPELINE, AgentStep

EmitFn = Callable[..., Awaitable[None]]

# 注入 Agent 上下文的知识库条目上限（避免上下文过大；后续可按 category/相关性精选）。
_KNOWLEDGE_LIMIT = 20
_SUGGESTION_LIMIT = 20


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

    async def rerun_stage(
        self, session: AsyncSession, content_item_id: int, stage: ContentStage
    ) -> ContentItem:
        """重跑某阶段 Agent：产新版交付物（旧版自动 superseded），不重置下游。"""
        ci = await session.get(ContentItem, content_item_id)
        if ci is None:
            raise ValueError(f"内容不存在: {content_item_id}")
        step = next(
            (s for s in PIPELINE if isinstance(s, AgentStep) and s.stage == stage), None
        )
        if step is None:
            raise ValueError(f"阶段无对应 Agent: {stage.value}")
        await self._run_agent(session, ci, step)
        return ci

    async def rollback_deliverable(
        self, session: AsyncSession, deliverable_id: int
    ) -> Deliverable:
        """回滚：把指定历史版本设回生效（approved），同内容同 type 的其余版本置 superseded。"""
        target = await session.get(Deliverable, deliverable_id)
        if target is None:
            raise ValueError("交付物不存在")
        others = (
            await session.scalars(
                select(Deliverable).where(
                    Deliverable.content_item_id == target.content_item_id,
                    Deliverable.type == target.type,
                    Deliverable.id != target.id,
                )
            )
        ).all()
        for d in others:
            d.status = DeliverableStatus.SUPERSEDED
        target.status = DeliverableStatus.APPROVED
        await session.commit()
        await self._emit(
            "deliverable.rolledback",
            {"type": target.type.value, "version": target.version},
            content_item_id=target.content_item_id,
        )
        return target

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

        org_id = await self._org_id(session, ci)
        ctx = AgentContext(
            content_item_id=ci.id,
            upstream=await self._upstream(session, ci.id),
            knowledge=await self._knowledge(session, org_id),
            optimization_suggestions=await self._accepted_suggestions(
                session, org_id, step.stage
            ),
        )
        try:
            result = await step.agent.run(session, org_id, ctx)
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
        # 同 type 的旧版标记 superseded（版本化：始终保留历史，仅最新版生效）
        await self._supersede_prior(session, ci.id, step.agent.output_type)
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
        suggestions = await self._capture_optimization_suggestions(
            session, ci, deliverable, org_id, result.model_dump()
        )

        task.status = AgentTaskStatus.DONE
        task.finished_at = _now()
        task.output_deliverable_id = deliverable.id
        await session.commit()

        await self._emit(
            "agent.done",
            {"stage": step.stage.value, "deliverable_id": deliverable.id},
            content_item_id=ci.id,
        )
        for suggestion in suggestions:
            await self._emit(
                "optimization.suggestion",
                {
                    "suggestion_id": suggestion.id,
                    "target_stage": str(suggestion.target_stage)
                    if suggestion.target_stage is not None
                    else None,
                },
                content_item_id=ci.id,
            )

    async def _capture_optimization_suggestions(
        self,
        session: AsyncSession,
        ci: ContentItem,
        deliverable: Deliverable,
        org_id: int | None,
        payload: dict,
    ) -> list[OptimizationSuggestion]:
        """从运营复盘报告抽取优化建议，落为可追踪闭环记录。"""
        if deliverable.type != DeliverableType.REVIEW_REPORT or org_id is None:
            return []
        raw = payload.get("optimization_suggestions")
        if not isinstance(raw, list):
            return []

        rows: list[OptimizationSuggestion] = []
        for item in raw:
            if not isinstance(item, str) or not item.strip():
                continue
            row = OptimizationSuggestion(
                org_id=org_id,
                content_item_id=ci.id,
                source_deliverable_id=deliverable.id,
                target_stage=_suggestion_target_stage(item),
                suggestion=item.strip(),
            )
            session.add(row)
            rows.append(row)
        if rows:
            await session.flush()
        return rows

    async def _upstream(self, session: AsyncSession, ci_id: int) -> dict[str, dict]:
        """上游交付物：每个 type 仅取当前生效版本（最高 version 且非 superseded）。"""
        rows = (
            await session.scalars(
                select(Deliverable)
                .where(
                    Deliverable.content_item_id == ci_id,
                    Deliverable.status != DeliverableStatus.SUPERSEDED,
                )
                .order_by(Deliverable.version)
            )
        ).all()
        # 按 version 升序遍历，后者（更高版本）覆盖前者，留下每 type 的最新生效版
        return {r.type.value: r.payload for r in rows}

    async def _supersede_prior(
        self, session: AsyncSession, ci_id: int, dtype: DeliverableType
    ) -> None:
        """把某内容某 type 的所有现存交付物标记 superseded（产新版前调用）。"""
        prior = (
            await session.scalars(
                select(Deliverable).where(
                    Deliverable.content_item_id == ci_id,
                    Deliverable.type == dtype,
                    Deliverable.status != DeliverableStatus.SUPERSEDED,
                )
            )
        ).all()
        for d in prior:
            d.status = DeliverableStatus.SUPERSEDED
        await session.flush()

    async def _org_id(self, session: AsyncSession, ci: ContentItem) -> int | None:
        """经 project 解析内容所属 org，供 Agent 按 org 路由 ModelConfig。"""
        project = await session.get(Project, ci.project_id)
        return project.org_id if project else None

    async def _knowledge(
        self, session: AsyncSession, org_id: int | None
    ) -> dict[str, list[dict]]:
        """加载 org 的知识库切片，按 category 分组注入 Agent 上下文。

        每类取最近若干条（标题+payload+tags），供 Agent 参考爆款结构/画像/提示词/话术。
        """
        if org_id is None:
            return {}
        rows = (
            await session.scalars(
                select(KnowledgeEntry)
                .where(KnowledgeEntry.org_id == org_id)
                .order_by(KnowledgeEntry.id.desc())
                .limit(_KNOWLEDGE_LIMIT)
            )
        ).all()
        grouped: dict[str, list[dict]] = {}
        for k in rows:
            grouped.setdefault(k.category.value, []).append(
                {"title": k.title, "payload": k.payload, "tags": k.tags}
            )
        return grouped

    async def _accepted_suggestions(
        self, session: AsyncSession, org_id: int | None, stage: ContentStage
    ) -> list[dict]:
        """加载已采纳优化建议，注入下一轮对应 Agent 的上下文。"""
        if org_id is None:
            return []
        rows = (
            await session.scalars(
                select(OptimizationSuggestion)
                .where(
                    OptimizationSuggestion.org_id == org_id,
                    OptimizationSuggestion.status == OptimizationSuggestionStatus.ACCEPTED,
                    or_(
                        OptimizationSuggestion.target_stage == stage.value,
                        OptimizationSuggestion.target_stage.is_(None),
                    ),
                )
                .order_by(OptimizationSuggestion.id.desc())
                .limit(_SUGGESTION_LIMIT)
            )
        ).all()
        return [
            {
                "suggestion": row.suggestion,
                "target_stage": row.target_stage,
                "source_content_item_id": row.content_item_id,
                "note": row.note,
            }
            for row in rows
        ]

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
            # 脚本合规门：阻塞时自动跑合规预检，结果落库供人工审批参考
            if gate == GateType.SCRIPT_COMPLIANCE:
                await self._run_compliance_check(session, ci.id)
            await self._emit("gate.pending", {"gate": gate.value}, content_item_id=ci.id)

    async def _run_compliance_check(self, session: AsyncSession, ci_id: int) -> None:
        """对最新生效的视频脚本跑合规预检，落 ComplianceCheck 记录。"""
        script = await session.scalar(
            select(Deliverable)
            .where(
                Deliverable.content_item_id == ci_id,
                Deliverable.type == DeliverableType.VIDEO_SCRIPT,
                Deliverable.status != DeliverableStatus.SUPERSEDED,
            )
            .order_by(Deliverable.version.desc())
        )
        if script is None:
            return
        risk, summary, findings = check_script(script.payload)
        session.add(
            ComplianceCheck(
                content_item_id=ci_id,
                deliverable_id=script.id,
                risk=risk,
                summary=summary,
                findings=findings,
            )
        )
        await session.commit()
        await self._emit(
            "compliance.checked",
            {"risk": risk.value, "findings": len(findings)},
            content_item_id=ci_id,
        )

    async def _auto_pass_gate(self, session: AsyncSession, ci: ContentItem, gate: GateType) -> None:
        session.add(GateApproval(content_item_id=ci.id, gate=gate, status=GateStatus.AUTO_PASSED))
        await session.commit()
        await self._emit("gate.passed", {"gate": gate.value}, content_item_id=ci.id)


# 默认引擎实例（事件经 arq 总线广播）
engine = OrchestrationEngine()


def _suggestion_target_stage(text: str) -> ContentStage | None:
    """从建议文本粗略映射目标环节；无法判断则留空，交给人工分派。"""
    mapping = {
        "定位": ContentStage.POSITIONING,
        "编导": ContentStage.CONTENT_DIRECTION,
        "脚本": ContentStage.CONTENT_DIRECTION,
        "美术": ContentStage.ART_DIRECTION,
        "视觉": ContentStage.ART_DIRECTION,
        "视频": ContentStage.VIDEO_CREATION,
        "剪辑": ContentStage.EDITING,
        "运营": ContentStage.OPERATION,
    }
    for keyword, stage in mapping.items():
        if keyword in text:
            return stage
    return None
