"""Claude Code-style visible runtime for the operations brain.

This runtime keeps the existing ledgers (`BrainTask`, `AgentInvocation`,
`AgentToolCall`, `DeliverableAcceptance`) and adds a live LLM token stream on
top. The stream is broadcast through WebSocket events while durable checkpoints
are stored as `Event` rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.events import publish_realtime_event
from app.llm.gateway import gateway, reset_stream_observer, set_stream_observer
from app.models import AgentInvocation, AgentToolCall, BrainTask, Event
from app.models.enums import AgentCode, BrainTaskStatus
from app.orchestrator.brain_adapter import run_brain_task_pipeline, run_brain_task_steps
from app.orchestrator.brain_intelligence import IntelligenceUnavailable, brain_intelligence
from app.orchestrator.capability_registry import runtime_capabilities
from app.schemas.brain import DecisionRequest, IntentDecision


class BrainRuntimeState(TypedDict, total=False):
    task_id: int
    thread_id: str
    status: str
    pending_permissions: list[int]
    round_index: int
    selected_experts: list[str]
    observations: list[dict[str, Any]]
    pending_decision_id: str


@dataclass
class _StreamObserverState:
    counters: dict[str, int] = field(default_factory=dict)
    current: dict[str, str] = field(default_factory=dict)

    def message_id_for(self, agent_code: str) -> str:
        existing = self.current.get(agent_code)
        if existing:
            return existing
        next_index = self.counters.get(agent_code, 0) + 1
        self.counters[agent_code] = next_index
        message_id = f"{agent_code}:{next_index}"
        self.current[agent_code] = message_id
        return message_id

    def finish(self, agent_code: str) -> None:
        self.current.pop(agent_code, None)


class BrainRuntimeGraph:
    """LangGraph wrapper that exposes a brain task as a resumable agent runtime."""

    def __init__(self) -> None:
        graph = StateGraph(BrainRuntimeState)
        graph.add_node("load_context", self._load_context)
        graph.add_node("plan_execution", self._plan_execution)
        graph.add_node("dispatch_experts", self._dispatch_experts)
        graph.add_node("permission_gate", self._permission_gate)
        graph.add_node("summarize", self._summarize)
        graph.add_edge(START, "load_context")
        graph.add_edge("load_context", "plan_execution")
        graph.add_edge("plan_execution", "dispatch_experts")
        graph.add_edge("dispatch_experts", "permission_gate")
        graph.add_conditional_edges(
            "permission_gate",
            self._route_after_permission_gate,
            {"waiting": END, "continue": "summarize"},
        )
        graph.add_edge("summarize", END)
        self._graph = graph.compile()

        resume_graph = StateGraph(BrainRuntimeState)
        resume_graph.add_node("permission_gate", self._permission_gate)
        resume_graph.add_node("summarize", self._summarize)
        resume_graph.add_edge(START, "permission_gate")
        resume_graph.add_conditional_edges(
            "permission_gate",
            self._route_after_permission_gate,
            {"waiting": END, "continue": "summarize"},
        )
        resume_graph.add_edge("summarize", END)
        self._resume_graph = resume_graph.compile()

        smart_graph = StateGraph(BrainRuntimeState)
        smart_graph.add_node("dispatch_round", self._dispatch_round)
        smart_graph.add_node("observe_round", self._observe_round)
        smart_graph.add_node("smart_permission_gate", self._smart_permission_gate)
        smart_graph.add_node("decide_next", self._decide_next)
        smart_graph.add_node("smart_summarize", self._smart_summarize)
        smart_graph.add_edge(START, "decide_next")
        smart_graph.add_edge("dispatch_round", "observe_round")
        smart_graph.add_edge("observe_round", "smart_permission_gate")
        smart_graph.add_conditional_edges(
            "smart_permission_gate",
            self._route_after_smart_permission,
            {"waiting": END, "continue": "decide_next"},
        )
        smart_graph.add_conditional_edges(
            "decide_next",
            self._route_after_smart_decision,
            {"dispatch": "dispatch_round", "waiting": END, "finish": "smart_summarize"},
        )
        smart_graph.add_edge("smart_summarize", END)
        self._smart_graph = smart_graph.compile()

        smart_resume_graph = StateGraph(BrainRuntimeState)
        smart_resume_graph.add_node("decide_next", self._decide_next)
        smart_resume_graph.add_node("dispatch_round", self._dispatch_round)
        smart_resume_graph.add_node("observe_round", self._observe_round)
        smart_resume_graph.add_node("smart_permission_gate", self._smart_permission_gate)
        smart_resume_graph.add_node("smart_summarize", self._smart_summarize)
        smart_resume_graph.add_edge(START, "decide_next")
        smart_resume_graph.add_conditional_edges(
            "decide_next",
            self._route_after_smart_decision,
            {"dispatch": "dispatch_round", "waiting": END, "finish": "smart_summarize"},
        )
        smart_resume_graph.add_edge("dispatch_round", "observe_round")
        smart_resume_graph.add_edge("observe_round", "smart_permission_gate")
        smart_resume_graph.add_conditional_edges(
            "smart_permission_gate",
            self._route_after_smart_permission,
            {"waiting": END, "continue": "decide_next"},
        )
        smart_resume_graph.add_edge("smart_summarize", END)
        self._smart_resume_graph = smart_resume_graph.compile()

    async def start_smart(
        self,
        session: AsyncSession,
        task: BrainTask,
        intent: IntentDecision,
    ) -> BrainTask:
        """Start one server-classified conversation or dynamic expert workflow."""

        task.runtime_mode = "langgraph"
        task.thread_id = task.thread_id or self.thread_id_for(task.id)
        task.status = BrainTaskStatus.RUNNING
        task.current_focus = "主 Agent 正在理解你的目标"
        await session.commit()
        await self._record_event(
            session,
            task,
            "brain.runtime.started",
            {"message": "主 Agent 已接收你的消息。"},
        )
        await self._record_event(
            session,
            task,
            "brain.runtime.intent_classified",
            {"intent": intent.model_dump(mode="json")},
        )

        _session_from_state._active_session = session
        observer_state = _StreamObserverState()
        token = set_stream_observer(self._stream_observer(session, task, observer_state))
        try:
            if intent.intent == "conversation":
                await self._stream_conversation_turn(session, task)
                task.status = BrainTaskStatus.COMPLETED
                task.progress = 100
                task.current_focus = "主 Agent 已完成回复，未调用专家"
                await session.commit()
                return task

            if intent.intent == "clarification":
                question = intent.clarifying_question or "这次你最希望优先解决什么问题？"
                task.current_focus = "等待你补充一个关键信息"
                await session.commit()
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.message_done",
                    {
                        "message_id": "00-decision:1",
                        "agent_code": AgentCode.DECISION.value,
                        "agent_name": "主 Agent",
                        "model": "system",
                        "message": question,
                        "content": question,
                    },
                )
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.clarification_requested",
                    {"message": question, "missing_field": intent.missing_field},
                )
                return task

            await self._stream_main_agent_turn(session, task)
            await self._smart_graph.ainvoke(
                {
                    "task_id": task.id,
                    "thread_id": task.thread_id,
                    "round_index": 1,
                    "selected_experts": [],
                    "observations": [],
                }
            )
        finally:
            reset_stream_observer(token)
            _session_from_state._active_session = None
        await session.refresh(task)
        return task

    async def start(self, session: AsyncSession, task: BrainTask) -> BrainTask:
        task.runtime_mode = "langgraph"
        task.thread_id = task.thread_id or self.thread_id_for(task.id)
        task.status = BrainTaskStatus.RUNNING
        task.current_focus = "主 Agent 正在理解目标并准备调度专家"
        await session.commit()
        await self._record_event(
            session,
            task,
            "brain.runtime.started",
            {"message": "主 Agent 已接收目标，开始建立运行时上下文。"},
        )

        _session_from_state._active_session = session
        observer_state = _StreamObserverState()
        observer = self._stream_observer(session, task, observer_state)
        token = set_stream_observer(observer)
        try:
            await self._stream_main_agent_turn(session, task)
            await self._graph.ainvoke({"task_id": task.id, "thread_id": task.thread_id})
        finally:
            reset_stream_observer(token)
            _session_from_state._active_session = None
        await session.refresh(task)
        return task

    async def start_casual_turn(self, session: AsyncSession, task: BrainTask) -> BrainTask:
        task.runtime_mode = "langgraph"
        task.thread_id = task.thread_id or self.thread_id_for(task.id)
        task.status = BrainTaskStatus.COMPLETED
        task.progress = 100
        task.current_focus = "主 Agent 已完成普通对话，未启动专家工作流"
        await session.commit()
        await self._record_event(
            session,
            task,
            "brain.runtime.message_done",
            {
                "message_id": "00-decision:1",
                "agent_code": "00-decision",
                "agent_name": "主 Agent",
                "model": "system",
                "message": _casual_reply(task.brief.goal if task.brief else ""),
                "content": _casual_reply(task.brief.goal if task.brief else ""),
            },
        )
        await session.refresh(task)
        return task

    async def resume_after_permission(
        self,
        session: AsyncSession,
        task: BrainTask,
        tool_call: AgentToolCall,
        approved: bool,
    ) -> BrainTask:
        if task.runtime_mode != "langgraph":
            return task
        task.thread_id = task.thread_id or self.thread_id_for(task.id)
        await self._record_event(
            session,
            task,
            "brain.runtime.resumed",
            {
                "message": "人工确认已返回，主 Agent 正在检查是否可以继续执行。",
                "tool_call_id": tool_call.id,
                "tool_code": tool_call.tool_code,
                "approved": approved,
                "comment": str(((tool_call.meta or {}).get("decision") or {}).get("comment") or ""),
            },
        )
        remaining = await _pending_permissions(session, task.id, task.org_id)
        if remaining:
            task.current_focus = "等待你确认剩余受控动作"
            await session.commit()
            return task

        events = await runtime_events(session, task.id)
        is_smart_runtime = any(
            event.type == "brain.runtime.intent_classified" for event in events
        )
        _session_from_state._active_session = session
        observer_state = _StreamObserverState()
        observer = self._stream_observer(session, task, observer_state)
        token = set_stream_observer(observer)
        try:
            if is_smart_runtime:
                observations = await _runtime_observations(session, task.id)
                await self._smart_resume_graph.ainvoke(
                    {
                        "task_id": task.id,
                        "thread_id": task.thread_id,
                        "round_index": _next_round_index(events),
                        "selected_experts": [],
                        "observations": observations,
                    }
                )
            else:
                await self._resume_graph.ainvoke(
                    {"task_id": task.id, "thread_id": task.thread_id}
                )
        finally:
            reset_stream_observer(token)
            _session_from_state._active_session = None
        await session.refresh(task)
        return task

    @staticmethod
    def thread_id_for(task_id: int) -> str:
        return f"brain-task-{task_id}"

    async def record_user_message(
        self,
        session: AsyncSession,
        task: BrainTask,
        message: str,
    ) -> None:
        task.thread_id = task.thread_id or self.thread_id_for(task.id)
        await self._record_event(
            session,
            task,
            "brain.runtime.user_message",
            {"message": message, "content": message},
        )

    async def resume_after_decision(
        self,
        session: AsyncSession,
        task: BrainTask,
        *,
        decision_id: str,
        choice_id: str,
        choice_title: str,
    ) -> BrainTask:
        await self._record_event(
            session,
            task,
            "brain.runtime.decision_selected",
            {
                "message": f"已选择：{choice_title}",
                "decision_id": decision_id,
                "choice_id": choice_id,
                "choice_title": choice_title,
            },
        )
        observations = await _runtime_observations(session, task.id)
        observations.append(
            {
                "kind": "user_decision",
                "decision_id": decision_id,
                "choice_id": choice_id,
                "summary": choice_title,
            }
        )
        task.current_focus = "主 Agent 正在根据你的选择继续"
        await session.commit()

        _session_from_state._active_session = session
        observer_state = _StreamObserverState()
        token = set_stream_observer(self._stream_observer(session, task, observer_state))
        try:
            await self._smart_resume_graph.ainvoke(
                {
                    "task_id": task.id,
                    "thread_id": task.thread_id or self.thread_id_for(task.id),
                    "round_index": max(1, len(observations)),
                    "selected_experts": [],
                    "observations": observations,
                }
            )
        finally:
            reset_stream_observer(token)
            _session_from_state._active_session = None
        await session.refresh(task)
        return task

    async def revise_decision(
        self,
        session: AsyncSession,
        task: BrainTask,
        *,
        decision: DecisionRequest,
        comment: str,
        request_new_options: bool,
    ) -> BrainTask:
        try:
            revised = await brain_intelligence.revise_decision(
                session,
                task.org_id,
                task.brief.goal if task.brief else task.title,
                decision,
                comment,
                request_new_options=request_new_options,
            )
        except IntelligenceUnavailable as exc:
            await self._record_event(
                session,
                task,
                "brain.runtime.message_error",
                {
                    "agent_code": AgentCode.DECISION.value,
                    "agent_name": "主 Agent",
                    "message": str(exc),
                    "error": str(exc),
                    "decision_id": decision.id,
                },
            )
            task.current_focus = "原方案仍可选择，也可以稍后再次修改"
            await session.commit()
            return task

        revised = revised.model_copy(
            update={"id": f"{decision.id}-revision-{uuid4().hex[:8]}", "status": "pending"}
        )
        await self._record_event(
            session,
            task,
            "brain.runtime.decision_revised",
            {
                "message": "已收到你的修改方向，主 Agent 会据此重新整理方案。",
                "decision_id": decision.id,
                "comment": comment,
                "request_new_options": request_new_options,
            },
        )
        await self._record_event(
            session,
            task,
            "brain.runtime.decision_requested",
            {
                "message": "我已经按你的意见重整了方案，请选择下一步。",
                "decision": revised.model_dump(mode="json"),
            },
        )
        task.current_focus = "等待你选择重整后的推进方案"
        await session.commit()
        return task

    async def _load_context(self, state: BrainRuntimeState) -> BrainRuntimeState:
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            task.current_focus = "主 Agent 已锁定当前账号、平台与任务边界"
            await self._record_event(
                session,
                task,
                "brain.runtime.context_loaded",
                {
                    "message": "主 Agent 已加载账号上下文。",
                    "platforms": task.brief.platforms if task.brief else [],
                    "account_ids": task.brief.account_ids if task.brief else [],
                },
            )
        return {**state, "status": "context_loaded"}

    async def _plan_execution(self, state: BrainRuntimeState) -> BrainRuntimeState:
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            task.current_focus = "主 Agent 已生成专家执行计划"
            steps = task.plan.steps if task.plan else []
            await self._record_event(
                session,
                task,
                "brain.runtime.plan_created",
                {
                    "message": "主 Agent 已生成执行计划，准备派发专家。",
                    "steps": [
                        {
                            "id": step.get("id"),
                            "agent_code": step.get("agent_code"),
                            "agent_name": step.get("agent_name"),
                            "human_gate": bool(step.get("human_gate")),
                        }
                        for step in steps
                        if step.get("status") != "skipped"
                    ],
                },
            )
        return {**state, "status": "planned"}

    async def _dispatch_experts(self, state: BrainRuntimeState) -> BrainRuntimeState:
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            first_step = next(
                (
                    step
                    for step in (task.plan.steps if task.plan else [])
                    if step.get("status") != "skipped"
                ),
                None,
            )
            if first_step:
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.subagent_started",
                    {
                        "message": f"主 Agent 正在调用 {first_step.get('agent_name')}。",
                        "agent_code": first_step.get("agent_code"),
                        "agent_name": first_step.get("agent_name"),
                    },
                )
            await run_brain_task_pipeline(session, task)
            await self._record_subagent_results(session, task)
        return {**state, "status": "experts_dispatched"}

    async def _dispatch_round(self, state: BrainRuntimeState) -> BrainRuntimeState:
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            selected = state.get("selected_experts", [])[:3]
            for code in selected:
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.subagent_started",
                    {
                        "message": f"{_agent_display_name(code)}开始处理。",
                        "agent_code": code,
                        "agent_name": _agent_display_name(code),
                        "round_index": state.get("round_index", 1),
                    },
                )
            await run_brain_task_steps(session, task, selected)
        return {**state, "status": "round_dispatched"}

    async def _observe_round(self, state: BrainRuntimeState) -> BrainRuntimeState:
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            selected = set(state.get("selected_experts", []))
            rows = (
                await session.scalars(
                    select(AgentInvocation)
                    .where(AgentInvocation.task_id == task.id)
                    .order_by(AgentInvocation.id)
                )
            ).all()
            current = [
                row
                for row in rows
                if _agent_code_value(row.agent_code) in selected
            ]
            observations = list(state.get("observations", []))
            for invocation in current:
                code = _agent_code_value(invocation.agent_code)
                observation = {
                    "agent_code": code,
                    "agent_name": invocation.agent_name,
                    "status": invocation.status.value
                    if hasattr(invocation.status, "value")
                    else str(invocation.status),
                    "summary": invocation.output_summary,
                }
                observations.append(observation)
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.subagent_completed",
                    {
                        "message": f"{invocation.agent_name}已完成本轮处理。",
                        "agent_code": code,
                        "agent_name": invocation.agent_name,
                        "invocation_id": invocation.id,
                        "round_index": state.get("round_index", 1),
                    },
                )
        return {**state, "status": "round_observed", "observations": observations}

    async def _smart_permission_gate(self, state: BrainRuntimeState) -> BrainRuntimeState:
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            pending = await _pending_permissions(session, task.id, task.org_id)
            if pending:
                task.current_focus = "等待你确认下一步动作"
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.permission_request",
                    {
                        "message": "下一步涉及受控动作，需要你的确认。",
                        "tool_call_ids": [row.id for row in pending],
                    },
                )
                return {
                    **state,
                    "status": "waiting_permission",
                    "pending_permissions": [row.id for row in pending],
                }
        return {**state, "status": "ready_to_decide", "pending_permissions": []}

    async def _decide_next(self, state: BrainRuntimeState) -> BrainRuntimeState:
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            if state.get("round_index", 1) >= 6:
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.round_limit",
                    {"message": "本轮已达到执行上限，我先汇总已有结论。"},
                )
                return {**state, "status": "finish"}

            capabilities = await runtime_capabilities(session, task.org_id)
            try:
                step = await brain_intelligence.decide_next(
                    session,
                    task.org_id,
                    task.brief.goal if task.brief else task.title,
                    state.get("observations", []),
                    capabilities,
                    state.get("round_index", 1),
                )
            except IntelligenceUnavailable as exc:
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.message_error",
                    {
                        "agent_code": AgentCode.DECISION.value,
                        "agent_name": "主 Agent",
                        "message": str(exc),
                        "error": str(exc),
                    },
                )
                return {**state, "status": "finish"}

            await self._record_event(
                session,
                task,
                "brain.runtime.next_step",
                {
                    "action": step.action,
                    "expert_codes": [code.value for code in step.expert_codes],
                    "rationale": step.rationale,
                    "message": step.handoff_message,
                    "round_index": state.get("round_index", 1),
                },
            )

            if step.action == "dispatch_experts":
                allowed_codes = {str(item["code"]) for item in capabilities}
                completed = {
                    str(item.get("agent_code")) for item in state.get("observations", [])
                }
                selected = [
                    code.value
                    for code in step.expert_codes
                    if code.value in allowed_codes and code.value not in completed
                ]
                if selected:
                    await self._record_event(
                        session,
                        task,
                        "brain.runtime.handoff",
                        {
                            "message": step.handoff_message,
                            "agent_codes": selected,
                        },
                    )
                    return {
                        **state,
                        "status": "dispatch",
                        "selected_experts": selected,
                        "round_index": state.get("round_index", 1) + 1,
                    }

            if step.action == "request_decision" and step.decision_request is not None:
                decision = step.decision_request.model_copy(update={"status": "pending"})
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.decision_requested",
                    {"message": step.handoff_message, "decision": decision.model_dump(mode="json")},
                )
                task.current_focus = "等待你选择一个推进方案"
                await session.commit()
                return {
                    **state,
                    "status": "waiting_decision",
                    "pending_decision_id": decision.id,
                }

            if step.action == "ask_user":
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.clarification_requested",
                    {"message": step.handoff_message},
                )
                task.current_focus = "等待你补充信息"
                await session.commit()
                return {**state, "status": "waiting_user"}

        return {**state, "status": "finish"}

    async def _smart_summarize(self, state: BrainRuntimeState) -> BrainRuntimeState:
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            await self._stream_summary_turn(session, task, state.get("observations", []))
            task.current_focus = "本轮专家工作已完成，等待你查看结果"
            await self._record_event(
                session,
                task,
                "brain.runtime.completed",
                {"message": "本轮需要的专家工作已经完成。"},
            )
        return {**state, "status": "completed"}

    @staticmethod
    def _route_after_smart_permission(state: BrainRuntimeState) -> str:
        return "waiting" if state.get("pending_permissions") else "continue"

    @staticmethod
    def _route_after_smart_decision(state: BrainRuntimeState) -> str:
        if state.get("status") == "dispatch":
            return "dispatch"
        if state.get("status") in {"waiting_decision", "waiting_user"}:
            return "waiting"
        return "finish"

    async def _permission_gate(self, state: BrainRuntimeState) -> BrainRuntimeState:
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            pending = await _pending_permissions(session, task.id, task.org_id)
            if pending:
                task.current_focus = "等待质量门与 Agent 工具调用人工确认"
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.permission_request",
                    {
                        "message": "主 Agent 已暂停，等待人工确认高风险工具调用。",
                        "tool_call_ids": [row.id for row in pending],
                    },
                )
                return {
                    **state,
                    "status": "waiting_permission",
                    "pending_permissions": [row.id for row in pending],
                }
        return {**state, "status": "ready_to_summarize", "pending_permissions": []}

    async def _summarize(self, state: BrainRuntimeState) -> BrainRuntimeState:
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            task.current_focus = "专家执行已完成，等待交付物验收"
            await self._record_event(
                session,
                task,
                "brain.runtime.completed",
                {"message": "主 Agent 已完成本轮专家调度，等待用户验收交付物。"},
            )
        return {**state, "status": "completed"}

    def _route_after_permission_gate(self, state: BrainRuntimeState) -> str:
        return "waiting" if state.get("pending_permissions") else "continue"

    async def _record_subagent_results(self, session: AsyncSession, task: BrainTask) -> None:
        invocations = (
            await session.scalars(
                select(AgentInvocation)
                .where(AgentInvocation.task_id == task.id)
                .order_by(AgentInvocation.id)
            )
        ).all()
        for invocation in invocations:
            await self._record_event(
                session,
                task,
                "brain.runtime.subagent_completed",
                {
                    "message": f"{invocation.agent_name} 已完成本轮处理。",
                    "agent_code": invocation.agent_code.value
                    if hasattr(invocation.agent_code, "value")
                    else str(invocation.agent_code),
                    "agent_name": invocation.agent_name,
                    "invocation_id": invocation.id,
                },
            )

    async def _stream_main_agent_turn(self, session: AsyncSession, task: BrainTask) -> None:
        if task.brief is None:
            return
        history = await _parent_thread_messages(session, task, task.brief.goal)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是同舟行AI新媒体运营平台的主 Agent。"
                    "用户希望像 Claude Code 一样在对话里完成运营工作流。"
                    "请先用自然语言确认你理解了用户目标，并说明你会根据需要动态选择专家或工具。"
                    "此时尚未完成调度决策，不要承诺具体专家名单或固定执行顺序。"
                    "不要输出 JSON，不要使用 Brief 这个词。"
                ),
            },
            *history,
            {
                "role": "user",
                "content": (
                    f"用户运营目标：{task.brief.goal}\n"
                    f"平台：{', '.join(task.brief.platforms)}\n"
                    f"账号 ID：{', '.join(str(item) for item in task.brief.account_ids)}"
                ),
            },
        ]
        await gateway.chat(session, task.org_id, "00-decision", messages)

    async def _stream_summary_turn(
        self,
        session: AsyncSession,
        task: BrainTask,
        observations: list[dict[str, Any]],
    ) -> None:
        if task.brief is None or not observations:
            return
        history = await _parent_thread_messages(session, task, "")
        await gateway.chat(
            session,
            task.org_id,
            AgentCode.DECISION.value,
            [
                {
                    "role": "system",
                    "content": (
                        "你是同舟行的主 Agent。根据专家的压缩结论向用户完成本轮汇总。"
                        "先给核心结论，再给不超过三条下一步建议。"
                        "不要输出 JSON、内部 action、专家 code、模型名或技术日志。"
                    ),
                },
                *history,
                {
                    "role": "user",
                    "content": f"原目标：{task.brief.goal}\n本轮观察：{observations}",
                },
            ],
        )

    async def _stream_conversation_turn(self, session: AsyncSession, task: BrainTask) -> None:
        if task.brief is None:
            return
        history = await _parent_thread_messages(session, task, task.brief.goal)
        result, _cost = await gateway.chat(
            session,
            task.org_id,
            AgentCode.DECISION.value,
            [
                {
                    "role": "system",
                    "content": (
                        "你是同舟行的主 Agent。自然、简洁地回应用户当前消息。"
                        "普通交流不得虚构专家调用、任务进度或已经读取的数据。"
                        "不要输出 JSON、模型名或技术说明。"
                    ),
                },
                *history,
                {"role": "user", "content": task.brief.goal},
            ],
        )
        events = await runtime_events(session, task.id)
        if not any(event.type == "brain.runtime.message_done" for event in events):
            await self._record_event(
                session,
                task,
                "brain.runtime.message_done",
                {
                    "message_id": "00-decision:1",
                    "agent_code": AgentCode.DECISION.value,
                    "agent_name": "主 Agent",
                    "model": result.model,
                    "message": result.content,
                    "content": result.content,
                },
            )

    def _stream_observer(
        self,
        session: AsyncSession,
        task: BrainTask,
        observer_state: _StreamObserverState,
    ):
        async def observer(event: dict[str, Any]) -> None:
            agent_code = str(event.get("agent_code") or "00-decision")
            phase = str(event.get("phase") or "")
            model = str(event.get("model") or "")
            message_id = observer_state.message_id_for(agent_code)
            agent_name = _agent_display_name(agent_code)
            base_payload = {
                "task_id": task.id,
                "thread_id": task.thread_id or self.thread_id_for(task.id),
                "message_id": message_id,
                "agent_code": agent_code,
                "agent_name": agent_name,
                "model": model,
            }
            if phase == "start":
                await publish_realtime_event(
                    "brain.runtime.message_start",
                    base_payload,
                    content_item_id=task.content_item_id,
                    project_id=task.brief.project_id if task.brief else None,
                )
            elif phase == "delta":
                await publish_realtime_event(
                    "brain.runtime.message_delta",
                    {**base_payload, "delta": str(event.get("delta") or "")},
                    content_item_id=task.content_item_id,
                    project_id=task.brief.project_id if task.brief else None,
                )
            elif phase == "done":
                content = str(event.get("content") or "")
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.message_done",
                    {**base_payload, "message": content, "content": content},
                )
                observer_state.finish(agent_code)
            elif phase == "error":
                message = str(event.get("error") or "模型调用失败")
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.message_error",
                    {**base_payload, "message": message, "error": message},
                )
                observer_state.finish(agent_code)

        return observer

    async def _record_event(
        self,
        session: AsyncSession,
        task: BrainTask,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        event_payload = {
            **payload,
            "task_id": task.id,
            "thread_id": task.thread_id or self.thread_id_for(task.id),
        }
        session.add(
            Event(
                type=event_type,
                content_item_id=task.content_item_id,
                project_id=task.brief.project_id if task.brief else None,
                payload=event_payload,
            )
        )
        await session.commit()
        await publish_realtime_event(
            event_type,
            event_payload,
            content_item_id=task.content_item_id,
            project_id=task.brief.project_id if task.brief else None,
        )


runtime_graph = BrainRuntimeGraph()


def _casual_reply(goal: str) -> str:
    normalized = goal.strip()
    if "谢" in normalized:
        return "不客气。我在这里，后续你可以直接告诉我运营目标，我会先判断是否需要调用专家。"
    return (
        "你好，我在。你可以直接告诉我具体运营目标，例如账号诊断、内容选题、"
        "脚本生成、发布前检查或复盘分析；只有明确进入工作流时，我才会调用专家 Agent。"
    )


async def runtime_events(session: AsyncSession, task_id: int) -> list[Event]:
    rows = (
        await session.scalars(
            select(Event).where(Event.type.like("brain.runtime.%")).order_by(Event.id)
        )
    ).all()
    return [row for row in rows if (row.payload or {}).get("task_id") == task_id]


async def runtime_status(session: AsyncSession, task: BrainTask) -> str:
    if task.runtime_mode != "langgraph":
        return "legacy"
    pending = await _pending_permissions(session, task.id, task.org_id)
    if pending:
        return "waiting_permission"
    if task.status == BrainTaskStatus.FAILED:
        return "failed"
    if task.status == BrainTaskStatus.COMPLETED:
        return "completed"
    return "running"


async def next_actions(session: AsyncSession, task: BrainTask) -> list[str]:
    pending = await _pending_permissions(session, task.id, task.org_id)
    if pending:
        return ["review_pending_permissions"]
    if task.acceptances:
        return ["review_deliverables"]
    if task.context_closed_at is None and task.status == BrainTaskStatus.COMPLETED:
        return ["close_task_memory"]
    return []


async def _pending_permissions(
    session: AsyncSession, task_id: int, org_id: int
) -> list[AgentToolCall]:
    return (
        await session.scalars(
            select(AgentToolCall)
            .where(
                AgentToolCall.task_id == task_id,
                AgentToolCall.org_id == org_id,
                AgentToolCall.requires_human_confirmation.is_(True),
                AgentToolCall.status == "waiting_approval",
            )
            .order_by(AgentToolCall.id)
        )
    ).all()


async def _runtime_observations(
    session: AsyncSession,
    task_id: int,
) -> list[dict[str, Any]]:
    rows = (
        await session.scalars(
            select(AgentInvocation)
            .where(AgentInvocation.task_id == task_id)
            .order_by(AgentInvocation.id)
        )
    ).all()
    observations = [
        {
            "agent_code": _agent_code_value(row.agent_code),
            "agent_name": row.agent_name,
            "status": row.status.value if hasattr(row.status, "value") else str(row.status),
            "summary": row.output_summary,
        }
        for row in rows
    ]
    tool_calls = (
        await session.scalars(
            select(AgentToolCall)
            .where(AgentToolCall.task_id == task_id)
            .order_by(AgentToolCall.id)
        )
    ).all()
    for tool_call in tool_calls:
        decision = (tool_call.meta or {}).get("decision")
        if not isinstance(decision, dict) or "approved" not in decision:
            continue
        observations.append(
            {
                "kind": "tool_permission",
                "tool_call_id": tool_call.id,
                "tool_code": tool_call.tool_code,
                "approved": bool(decision.get("approved")),
                "comment": str(decision.get("comment") or ""),
                "summary": (
                    f"{tool_call.tool_name} 已由用户"
                    f"{'允许' if decision.get('approved') else '驳回'}"
                ),
            }
        )
    return observations


def _next_round_index(events: list[Event]) -> int:
    rounds = [
        int((event.payload or {}).get("round_index") or 1)
        for event in events
        if str((event.payload or {}).get("round_index") or "").isdigit()
    ]
    return max(rounds, default=1)


async def _parent_thread_messages(
    session: AsyncSession,
    task: BrainTask,
    current_message: str,
) -> list[dict[str, str]]:
    """Build a compact parent-session transcript for the next main-Agent turn.

    Expert contexts stay isolated; only their durable summaries are projected back
    into the parent thread. The latest user event is omitted because callers append
    the current message with its full account and platform context.
    """

    events = await runtime_events(session, task.id)
    latest_user_event_id = next(
        (
            event.id
            for event in reversed(events)
            if event.type == "brain.runtime.user_message"
            and str((event.payload or {}).get("message") or "").strip()
            == current_message.strip()
        ),
        None,
    )
    invocations = (
        await session.scalars(
            select(AgentInvocation)
            .where(AgentInvocation.task_id == task.id)
            .order_by(AgentInvocation.id)
        )
    ).all()
    invocations_by_id = {row.id: row for row in invocations}
    transcript: list[dict[str, str]] = []

    for event in events:
        payload = event.payload or {}
        if event.type == "brain.runtime.user_message":
            if event.id == latest_user_event_id:
                continue
            content = str(payload.get("message") or payload.get("content") or "").strip()
            if content:
                transcript.append({"role": "user", "content": content})
            continue

        if event.type == "brain.runtime.message_done":
            agent_code = str(payload.get("agent_code") or AgentCode.DECISION.value)
            if agent_code != AgentCode.DECISION.value:
                continue
            content = str(payload.get("content") or payload.get("message") or "").strip()
            if content:
                transcript.append({"role": "assistant", "content": content})
            continue

        if event.type == "brain.runtime.subagent_completed":
            invocation_id = payload.get("invocation_id")
            invocation = (
                invocations_by_id.get(invocation_id)
                if isinstance(invocation_id, int)
                else None
            )
            if invocation and invocation.output_summary:
                transcript.append(
                    {
                        "role": "assistant",
                        "content": (
                            f"专家摘要（{invocation.agent_name}）："
                            f"{_compact_context_text(invocation.output_summary)}"
                        ),
                    }
                )
            continue

        if event.type == "brain.runtime.decision_selected":
            choice = str(payload.get("choice_title") or "").strip()
            if choice:
                transcript.append({"role": "user", "content": f"已选择方案：{choice}"})
            continue

        if event.type == "brain.runtime.resumed":
            result = "允许" if payload.get("approved") else "驳回"
            transcript.append({"role": "user", "content": f"工具权限决定：{result}"})

    compact = transcript[-12:]
    while sum(len(item["content"]) for item in compact) > 6000 and len(compact) > 1:
        compact.pop(0)
    return compact


def _compact_context_text(value: str, limit: int = 1200) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else f"{text[:limit]}..."


async def _load_task(session: AsyncSession, task_id: int) -> BrainTask:
    task = await session.scalar(
        select(BrainTask)
        .options(
            selectinload(BrainTask.brief),
            selectinload(BrainTask.plan),
            selectinload(BrainTask.acceptances),
        )
        .where(BrainTask.id == task_id)
    )
    if task is None:
        raise ValueError(f"brain task not found: {task_id}")
    return task


def _agent_display_name(agent_code: str) -> str:
    names = {
        "00-decision": "主 Agent",
        "01-positioning": "账号定位专家",
        "02-content-director": "编导文案专家",
        "03-art-director": "美术提示词专家",
        "04-video-creator": "视频创作专家",
        "05-editor": "剪辑专家",
        "06-operator": "账号运营专家",
        "07-advertiser": "投放专家",
        "08-customer-service": "客服专家",
    }
    return names.get(agent_code, agent_code)


def _agent_code_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


class _session_from_state:
    """Bind the active AsyncSession to LangGraph nodes during one invocation."""

    _active_session: AsyncSession | None = None

    def __init__(self, _state: BrainRuntimeState) -> None:
        if self._active_session is None:
            raise RuntimeError("BrainRuntimeGraph active session is not bound")
        self.session = self._active_session

    async def __aenter__(self) -> AsyncSession:
        return self.session

    async def __aexit__(self, *_exc: object) -> None:
        return None
