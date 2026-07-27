"""Claude Code-style visible runtime for the operations brain.

This runtime keeps the existing ledgers (`BrainTask`, `AgentInvocation`,
`AgentToolCall`, `DeliverableAcceptance`) and adds a live LLM token stream on
top. The stream is broadcast through WebSocket events while durable checkpoints
are stored as `Event` rows.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.core.events import publish_realtime_event
from app.llm.gateway import (
    LLMCallContext,
    bind_llm_call_context,
    gateway,
    reset_stream_observer,
    set_stream_observer,
)
from app.models import Account, AgentInvocation, AgentToolCall, BrainTask, Event, User
from app.models.enums import AgentCode, BrainTaskStatus
from app.orchestrator.agent_harness import agent_harness
from app.orchestrator.agent_kernel import AgentKernelPolicyError, main_kernel_policy
from app.orchestrator.brain_adapter import run_brain_task_pipeline
from app.orchestrator.brain_intelligence import IntelligenceUnavailable, brain_intelligence
from app.orchestrator.capability_registry import runtime_capabilities
from app.orchestrator.main_kernel import MainKernelActionExecutor, MainKernelRoute
from app.orchestrator.runtime_budget import RuntimeBudgetGuard, RuntimeBudgetLimits
from app.orchestrator.runtime_tools import build_runtime_tool_adapter
from app.orchestrator.tool_executor import DurableToolExecutor
from app.prompts import prompt_registry
from app.schemas.brain import DecisionRequest, IntentDecision, RuntimeToolCall
from app.services.runtime_memory import runtime_memory_service


class BrainRuntimeState(TypedDict, total=False):
    task_id: int
    agent_run_id: int
    agent_run_attempt: int
    thread_id: str
    status: str
    pending_permissions: list[int]
    round_index: int
    required_expert_codes: list[str]
    selected_experts: list[str]
    selected_tool_calls: list[dict[str, Any]]
    observations: list[dict[str, Any]]
    pending_decision_id: str
    runtime_started_at: str
    expert_dispatch_history: list[dict[str, Any]]
    tool_call_count: int
    token_count: int
    cost_usd: float
    selected_expert_purpose: str
    selected_expert_evidence_refs: list[str]
    termination_reason: str
    kernel_route: str


_runtime_session: ContextVar[AsyncSession | None] = ContextVar(
    "brain_runtime_session",
    default=None,
)


@contextmanager
def bind_runtime_session(session: AsyncSession) -> Iterator[None]:
    """Bind one database session to the current async execution context."""

    token = _runtime_session.set(session)
    try:
        yield
    finally:
        _runtime_session.reset(token)


@dataclass
class _StreamObserverState:
    run_id: str = field(default_factory=lambda: uuid4().hex)
    counters: dict[str, int] = field(default_factory=dict)
    current: dict[str, str] = field(default_factory=dict)
    contents: dict[str, str] = field(default_factory=dict)
    models: dict[str, str] = field(default_factory=dict)

    def message_id_for(self, agent_code: str) -> str:
        existing = self.current.get(agent_code)
        if existing:
            return existing
        next_index = self.counters.get(agent_code, 0) + 1
        self.counters[agent_code] = next_index
        message_id = f"{self.run_id}:{agent_code}:{next_index}"
        self.current[agent_code] = message_id
        self.contents.setdefault(agent_code, "")
        return message_id

    def append(self, agent_code: str, delta: str) -> None:
        self.contents[agent_code] = f"{self.contents.get(agent_code, '')}{delta}"

    def active_messages(self) -> list[tuple[str, str, str, str]]:
        return [
            (
                agent_code,
                message_id,
                self.contents.get(agent_code, ""),
                self.models.get(agent_code, ""),
            )
            for agent_code, message_id in self.current.items()
        ]

    def finish(self, agent_code: str) -> None:
        self.current.pop(agent_code, None)
        self.contents.pop(agent_code, None)
        self.models.pop(agent_code, None)


class BrainRuntimeGraph:
    """LangGraph wrapper that exposes a brain task as a resumable agent runtime."""

    def __init__(self, checkpointer: Any | None = None) -> None:
        self._compile_graphs(checkpointer)

    async def configure_checkpointer(self, checkpointer: Any | None) -> None:
        """Atomically rebuild compiled graphs around one worker-owned saver."""

        self._compile_graphs(checkpointer)

    @staticmethod
    def graph_config(thread_id: str) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": thread_id}}

    def _compile_graphs(self, checkpointer: Any | None) -> None:
        self._native_interrupts = checkpointer is not None
        graph = StateGraph(BrainRuntimeState)
        graph.add_node("load_context", self._load_context)
        graph.add_node("plan_execution", self._plan_execution)
        graph.add_node("dispatch_experts", self._dispatch_experts)
        graph.add_node("collect_permissions", self._collect_permissions)
        graph.add_node("permission_gate", self._permission_gate)
        graph.add_node("summarize", self._summarize)
        graph.add_edge(START, "load_context")
        graph.add_edge("load_context", "plan_execution")
        graph.add_edge("plan_execution", "dispatch_experts")
        graph.add_edge("dispatch_experts", "collect_permissions")
        graph.add_edge("collect_permissions", "permission_gate")
        graph.add_conditional_edges(
            "permission_gate",
            self._route_after_permission_gate,
            {"waiting": END, "continue": "summarize"},
        )
        graph.add_edge("summarize", END)
        self._graph = graph.compile(checkpointer=checkpointer)

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
        self._resume_graph = resume_graph.compile(checkpointer=checkpointer)

        smart_graph = StateGraph(BrainRuntimeState)
        smart_graph.add_node("dispatch_round", self._dispatch_round)
        smart_graph.add_node("execute_tools", self._execute_tools)
        smart_graph.add_node("observe_round", self._observe_round)
        smart_graph.add_node("collect_permissions", self._collect_permissions)
        smart_graph.add_node("smart_permission_gate", self._smart_permission_gate)
        smart_graph.add_node("decide_next", self._decide_next)
        smart_graph.add_node("decision_gate", self._decision_gate)
        smart_graph.add_node("smart_summarize", self._smart_summarize)
        smart_graph.add_edge(START, "decide_next")
        smart_graph.add_edge("dispatch_round", "observe_round")
        smart_graph.add_edge("observe_round", "collect_permissions")
        smart_graph.add_edge("collect_permissions", "smart_permission_gate")
        smart_graph.add_conditional_edges(
            "smart_permission_gate",
            self._route_after_smart_permission,
            {"waiting": END, "continue": "decide_next"},
        )
        smart_graph.add_conditional_edges(
            "decide_next",
            self._route_after_smart_decision,
            {
                "dispatch": "dispatch_round",
                "tools": "execute_tools",
                "decision": "decision_gate",
                "waiting": END,
                "finish": "smart_summarize",
            },
        )
        smart_graph.add_edge("execute_tools", "collect_permissions")
        smart_graph.add_conditional_edges(
            "decision_gate",
            self._route_after_decision_gate,
            {"continue": "decide_next", "waiting": END},
        )
        smart_graph.add_edge("smart_summarize", END)
        self._smart_graph = smart_graph.compile(checkpointer=checkpointer)

        smart_resume_graph = StateGraph(BrainRuntimeState)
        smart_resume_graph.add_node("decide_next", self._decide_next)
        smart_resume_graph.add_node("dispatch_round", self._dispatch_round)
        smart_resume_graph.add_node("execute_tools", self._execute_tools)
        smart_resume_graph.add_node("observe_round", self._observe_round)
        smart_resume_graph.add_node("smart_permission_gate", self._smart_permission_gate)
        smart_resume_graph.add_node("decision_gate", self._decision_gate)
        smart_resume_graph.add_node("smart_summarize", self._smart_summarize)
        smart_resume_graph.add_edge(START, "decide_next")
        smart_resume_graph.add_conditional_edges(
            "decide_next",
            self._route_after_smart_decision,
            {
                "dispatch": "dispatch_round",
                "tools": "execute_tools",
                "decision": "decision_gate",
                "waiting": END,
                "finish": "smart_summarize",
            },
        )
        smart_resume_graph.add_edge("execute_tools", "smart_permission_gate")
        smart_resume_graph.add_conditional_edges(
            "decision_gate",
            self._route_after_decision_gate,
            {"continue": "decide_next", "waiting": END},
        )
        smart_resume_graph.add_edge("dispatch_round", "observe_round")
        smart_resume_graph.add_edge("observe_round", "smart_permission_gate")
        smart_resume_graph.add_conditional_edges(
            "smart_permission_gate",
            self._route_after_smart_permission,
            {"waiting": END, "continue": "decide_next"},
        )
        smart_resume_graph.add_edge("smart_summarize", END)
        self._smart_resume_graph = smart_resume_graph.compile(checkpointer=checkpointer)

    async def start_smart(
        self,
        session: AsyncSession,
        task: BrainTask,
        intent: IntentDecision,
        *,
        client_message_id: str | None = None,
        agent_run_id: int | None = None,
        agent_run_attempt: int = 0,
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
            {
                "message": "主 Agent 已接收你的消息。",
                "client_message_id": client_message_id,
            },
        )
        await self._record_event(
            session,
            task,
            "brain.runtime.intent_classified",
            {
                "intent": intent.model_dump(mode="json"),
                "client_message_id": client_message_id,
            },
        )

        runtime_session_token = _runtime_session.set(session)
        observer_state = _StreamObserverState(run_id=client_message_id or uuid4().hex)
        token = set_stream_observer(
            self._stream_observer(
                session,
                task,
                observer_state,
                client_message_id=client_message_id,
            )
        )
        try:
            if intent.intent == "conversation":
                await self._stream_conversation_turn(
                    session,
                    task,
                    client_message_id=client_message_id,
                )
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
                        "message_id": _runtime_message_id(
                            client_message_id,
                            AgentCode.DECISION.value,
                        ),
                        "agent_code": AgentCode.DECISION.value,
                        "agent_name": "主 Agent",
                        "model": "system",
                        "message": question,
                        "content": question,
                        "client_message_id": client_message_id,
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
                    "agent_run_id": agent_run_id,
                    "agent_run_attempt": agent_run_attempt,
                    "thread_id": task.thread_id,
                    "round_index": 1,
                    "required_expert_codes": [
                        code.value for code in intent.suggested_expert_codes
                    ],
                    "selected_experts": [],
                    "selected_tool_calls": [],
                    "observations": [],
                    "runtime_started_at": datetime.now(UTC).isoformat(),
                    "expert_dispatch_history": [],
                    "tool_call_count": 0,
                    "token_count": 0,
                    "cost_usd": 0.0,
                },
                config=self.graph_config(task.thread_id),
            )
        finally:
            reset_stream_observer(token)
            _runtime_session.reset(runtime_session_token)
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

        runtime_session_token = _runtime_session.set(session)
        observer_state = _StreamObserverState()
        observer = self._stream_observer(session, task, observer_state)
        token = set_stream_observer(observer)
        try:
            await self._stream_main_agent_turn(session, task)
            await self._graph.ainvoke(
                {"task_id": task.id, "thread_id": task.thread_id},
                config=self.graph_config(task.thread_id),
            )
        finally:
            reset_stream_observer(token)
            _runtime_session.reset(runtime_session_token)
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
                "message_id": _runtime_message_id(None, AgentCode.DECISION.value),
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
        *,
        agent_run_id: int | None = None,
        agent_run_attempt: int = 0,
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
        is_smart_runtime = any(event.type == "brain.runtime.intent_classified" for event in events)
        runtime_session_token = _runtime_session.set(session)
        observer_state = _StreamObserverState()
        observer = self._stream_observer(session, task, observer_state)
        token = set_stream_observer(observer)
        try:
            if self._native_interrupts:
                target_graph = self._smart_graph if is_smart_runtime else self._graph
                await target_graph.ainvoke(
                    Command(
                        update={
                            "agent_run_id": agent_run_id,
                            "agent_run_attempt": agent_run_attempt,
                        },
                        resume={
                            "kind": "permission",
                            "tool_call_id": tool_call.id,
                            "approved": approved,
                        }
                    ),
                    config=self.graph_config(task.thread_id),
                )
            elif is_smart_runtime:
                observations = await _runtime_observations(session, task.id)
                await self._smart_resume_graph.ainvoke(
                    {
                        "task_id": task.id,
                        "agent_run_id": agent_run_id,
                        "agent_run_attempt": agent_run_attempt,
                        "thread_id": task.thread_id,
                        "round_index": _next_round_index(events),
                        "selected_experts": [],
                        "observations": observations,
                    },
                    config=self.graph_config(task.thread_id),
                )
            else:
                await self._resume_graph.ainvoke(
                    {"task_id": task.id, "thread_id": task.thread_id},
                    config=self.graph_config(task.thread_id),
                )
        finally:
            reset_stream_observer(token)
            _runtime_session.reset(runtime_session_token)
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
        *,
        client_message_id: str | None = None,
    ) -> None:
        task.thread_id = task.thread_id or self.thread_id_for(task.id)
        await self._record_event(
            session,
            task,
            "brain.runtime.user_message",
            {
                "message": message,
                "content": message,
                "client_message_id": client_message_id,
            },
        )

    async def record_regeneration_requested(
        self,
        session: AsyncSession,
        task: BrainTask,
        *,
        source_event_id: int,
        client_message_id: str,
    ) -> None:
        await self._record_event(
            session,
            task,
            "brain.runtime.regeneration_requested",
            {
                "message": "主 Agent 正在重新生成这一轮回答。",
                "source_event_id": source_event_id,
                "client_message_id": client_message_id,
            },
        )

    async def record_generation_stopped(
        self,
        session: AsyncSession,
        task: BrainTask,
        *,
        client_message_id: str,
    ) -> None:
        task.status = BrainTaskStatus.COMPLETED
        task.progress = 100
        task.current_focus = "本轮生成已停止，可以重新生成或继续输入"
        await session.commit()
        await self._record_event(
            session,
            task,
            "brain.runtime.generation_stopped",
            {
                "message": "已停止生成。",
                "client_message_id": client_message_id,
            },
        )

    async def resume_after_decision(
        self,
        session: AsyncSession,
        task: BrainTask,
        *,
        decision_id: str,
        choice_id: str,
        choice_title: str,
        record_selection: bool = True,
        agent_run_id: int | None = None,
        agent_run_attempt: int = 0,
    ) -> BrainTask:
        if record_selection:
            await self.record_decision_selected(
                session,
                task,
                decision_id=decision_id,
                choice_id=choice_id,
                choice_title=choice_title,
            )
        task.current_focus = "主 Agent 正在根据你的选择继续"
        await session.commit()

        runtime_session_token = _runtime_session.set(session)
        observer_state = _StreamObserverState()
        token = set_stream_observer(self._stream_observer(session, task, observer_state))
        try:
            thread_id = task.thread_id or self.thread_id_for(task.id)
            if self._native_interrupts:
                await self._smart_graph.ainvoke(
                    Command(
                        update={
                            "agent_run_id": agent_run_id,
                            "agent_run_attempt": agent_run_attempt,
                        },
                        resume={
                            "kind": "decision",
                            "decision_id": decision_id,
                            "choice_id": choice_id,
                            "choice_title": choice_title,
                        }
                    ),
                    config=self.graph_config(thread_id),
                )
            else:
                observations = await _runtime_observations(session, task.id)
                observations.append(
                    {
                        "kind": "user_decision",
                        "decision_id": decision_id,
                        "choice_id": choice_id,
                        "summary": choice_title,
                    }
                )
                await self._smart_resume_graph.ainvoke(
                    {
                        "task_id": task.id,
                        "agent_run_id": agent_run_id,
                        "agent_run_attempt": agent_run_attempt,
                        "thread_id": thread_id,
                        "round_index": max(1, len(observations)),
                        "selected_experts": [],
                        "observations": observations,
                    },
                    config=self.graph_config(thread_id),
                )
        finally:
            reset_stream_observer(token)
            _runtime_session.reset(runtime_session_token)
        await session.refresh(task)
        return task

    async def record_decision_selected(
        self,
        session: AsyncSession,
        task: BrainTask,
        *,
        decision_id: str,
        choice_id: str,
        choice_title: str,
    ) -> None:
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
        await self._check_main_turn_boundary(state)
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            if task.created_by_id is None:
                raise RuntimeError("brain task has no authenticated creator")
            user = await session.get(User, task.created_by_id)
            if user is None or user.org_id != task.org_id:
                raise PermissionError("brain task creator is unavailable")
            selected = state.get("selected_experts", [])[:3]
            round_index = state.get("round_index", 1)
            purpose = state.get("selected_expert_purpose") or (
                task.brief.goal if task.brief else task.title
            )
            evidence_refs = list(state.get("selected_expert_evidence_refs", []))
            for code in selected:
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.subagent_started",
                    {
                        "message": f"{_agent_display_name(code)}开始处理。",
                        "agent_code": code,
                        "agent_name": _agent_display_name(code),
                        "round_index": round_index,
                    },
                )
                agent_code = AgentCode(code)
                await agent_harness.execute(
                    session,
                    user=user,
                    task=task,
                    code=agent_code,
                    purpose=purpose,
                    evidence_refs=evidence_refs,
                    run_id=state.get("agent_run_id"),
                    step_key=f"round-{round_index}:{agent_code.value}",
                    attempt=state.get("agent_run_attempt", 0),
                )
        return {**state, "status": "round_dispatched"}

    async def _observe_round(self, state: BrainRuntimeState) -> BrainRuntimeState:
        await self._check_main_turn_boundary(state)
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
            current = [row for row in rows if _agent_code_value(row.agent_code) in selected]
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

    async def _execute_tools(self, state: BrainRuntimeState) -> BrainRuntimeState:
        await self._check_main_turn_boundary(state)
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            if task.created_by_id is None:
                raise RuntimeError("brain task has no authenticated creator")
            user = await session.get(User, task.created_by_id)
            if user is None or user.org_id != task.org_id:
                raise PermissionError("brain task creator is unavailable")

            account_ids = list(task.brief.account_ids if task.brief else [])
            account_id = int(account_ids[0]) if len(account_ids) == 1 else None
            project_id = task.brief.project_id if task.brief else None
            executor = DurableToolExecutor(build_runtime_tool_adapter())
            observations = list(state.get("observations", []))

            for payload in state.get("selected_tool_calls", [])[:5]:
                request = RuntimeToolCall.model_validate(payload)
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.tool_started",
                    {
                        "message": f"主 Agent 正在调用 {request.tool_code}。",
                        "tool_code": request.tool_code,
                        "purpose": request.purpose,
                    },
                )
                outcome = await executor.execute(
                    task=task,
                    user=user,
                    request=request,
                    project_id=project_id,
                    account_id=account_id,
                )
                if outcome.status == "waiting_approval":
                    continue
                observation = {
                    "kind": "tool_result",
                    "tool_call_id": outcome.tool_call.id,
                    "tool_code": request.tool_code,
                    "summary": outcome.tool_call.output_summary,
                    "result": outcome.result or {},
                }
                observations.append(observation)
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.tool_completed",
                    {
                        "message": f"{request.tool_code} 已完成。",
                        **observation,
                    },
                )
        return {
            **state,
            "status": "tools_executed",
            "selected_tool_calls": [],
            "observations": observations,
        }

    async def _collect_permissions(self, state: BrainRuntimeState) -> BrainRuntimeState:
        await self._check_main_turn_boundary(state)
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            pending = await _pending_permissions(session, task.id, task.org_id)
            if pending:
                task.current_focus = "等待你确认质量门与下一步动作"
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
                    "status": "permissions_collected",
                    "pending_permissions": [row.id for row in pending],
                }
        return {**state, "status": "permissions_collected", "pending_permissions": []}

    async def _smart_permission_gate(self, state: BrainRuntimeState) -> BrainRuntimeState:
        await self._check_main_turn_boundary(state)
        return await self._run_permission_gate(state, ready_status="ready_to_decide")

    async def _decide_next(self, state: BrainRuntimeState) -> BrainRuntimeState:
        await self._check_main_turn_boundary(state)
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            budget_guard = _runtime_budget_guard()
            action_executor = _main_action_executor(budget_guard)
            budget_reason = budget_guard.exhaustion_reason(state)
            if budget_reason is not None:
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.budget_exhausted",
                    {
                        "message": "本轮已达到安全执行预算，我先汇总已有结论。",
                        "reason": budget_reason,
                    },
                )
                return {
                    **state,
                    "status": "finish",
                    "kernel_route": MainKernelRoute.FINISH.value,
                    "termination_reason": budget_reason,
                }

            if task.created_by_id is None:
                raise RuntimeError("brain task has no authenticated creator")
            user = await session.get(User, task.created_by_id)
            if user is None or user.org_id != task.org_id:
                raise PermissionError("brain task creator is unavailable")
            capabilities = await runtime_capabilities(session, user)
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
                return {
                    **state,
                    "status": "finish",
                    "kernel_route": MainKernelRoute.FINISH.value,
                }

            required_expert_codes = _required_expert_codes(state, task)
            successful_expert_codes = _successful_expert_codes(
                state.get("observations", [])
            )
            available_expert_codes = {
                str(item["code"])
                for item in capabilities
                if item.get("kind") == "expert"
            }
            if (
                step.action in {"respond", "finish"}
                and required_expert_codes
                and not successful_expert_codes.intersection(required_expert_codes)
            ):
                pending_expert_codes = [
                    code
                    for code in required_expert_codes
                    if code in available_expert_codes
                ][:3]
                if pending_expert_codes:
                    step = step.model_copy(
                        update={
                            "action": "dispatch_experts",
                            "expert_codes": [
                                AgentCode(code) for code in pending_expert_codes
                            ],
                            "rationale": (
                                "专业任务必须先取得对应专家的有效结论，"
                                "主 Agent 才能汇总或结束本轮。"
                            ),
                            "handoff_message": (
                                "我先把这项专业任务交给对应专家处理，"
                                "完成后再为你汇总结论。"
                            ),
                            "purpose": step.purpose or step.rationale,
                            "evidence_refs": list(step.evidence_refs) or ["intent-routing"],
                        }
                    )

            try:
                transition = action_executor.prepare(step)
            except (AgentKernelPolicyError, ValueError) as exc:
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.policy_denied",
                    {
                        "message": "主 Agent 请求的下一步不符合运行时权限策略。",
                        "action": step.action,
                        "reason": str(exc),
                    },
                )
                return {
                    **state,
                    "status": "finish",
                    "kernel_route": MainKernelRoute.FINISH.value,
                    "termination_reason": "kernel_policy_denied",
                }

            await self._record_event(
                session,
                task,
                "brain.runtime.next_step",
                {
                    "action": step.action,
                    "expert_codes": [code.value for code in step.expert_codes],
                    "tool_codes": [call.tool_code for call in step.tool_calls],
                    "rationale": step.rationale,
                    "message": step.handoff_message,
                    "round_index": state.get("round_index", 1),
                },
            )

            if step.action == "dispatch_experts":
                allowed_codes = {
                    str(item["code"])
                    for item in capabilities
                    if item.get("kind") == "expert"
                }
                requested = [
                    code.value
                    for code in step.expert_codes
                    if code.value in allowed_codes
                ]
                authorization = budget_guard.authorize_experts(
                    state,
                    requested,
                    purpose=step.purpose or step.rationale,
                    evidence_refs=step.evidence_refs,
                )
                selected = authorization.allowed_codes
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
                        **authorization.state,
                        "status": transition.status,
                        "kernel_route": transition.route.value,
                        "selected_experts": selected,
                        "selected_expert_purpose": step.purpose or step.rationale,
                        "selected_expert_evidence_refs": step.evidence_refs,
                        "round_index": state.get("round_index", 1) + 1,
                    }
                if authorization.blocked_reason is not None:
                    await self._record_event(
                        session,
                        task,
                        "brain.runtime.loop_blocked",
                        {
                            "message": "检测到重复调度或专家预算已耗尽，我先汇总已有结论。",
                            "reason": authorization.blocked_reason,
                            "expert_codes": requested,
                        },
                    )
                    return {
                        **authorization.state,
                        "status": "finish",
                        "kernel_route": MainKernelRoute.FINISH.value,
                        "termination_reason": authorization.blocked_reason,
                    }

            if step.action in {"call_tools", "request_permission"} and step.tool_calls:
                authorization = budget_guard.authorize_tools(state, len(step.tool_calls))
                if authorization.allowed_count == 0:
                    await self._record_event(
                        session,
                        task,
                        "brain.runtime.budget_exhausted",
                        {
                            "message": "工具调用已达到本轮安全预算，我先汇总已有结论。",
                            "reason": authorization.blocked_reason,
                        },
                    )
                    return {
                        **authorization.state,
                        "status": "finish",
                        "kernel_route": MainKernelRoute.FINISH.value,
                        "termination_reason": authorization.blocked_reason or "tool_blocked",
                    }
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.handoff",
                    {
                        "message": step.handoff_message,
                        "tool_codes": [call.tool_code for call in step.tool_calls],
                    },
                )
                return {
                    **authorization.state,
                    "status": transition.status,
                    "kernel_route": transition.route.value,
                    "selected_tool_calls": [
                        call.model_dump(mode="json") for call in step.tool_calls
                    ],
                    "round_index": state.get("round_index", 1) + 1,
                }

            if step.action == "respond":
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.message_done",
                    {
                        "message_id": _runtime_message_id(None, AgentCode.DECISION.value),
                        "agent_code": AgentCode.DECISION.value,
                        "agent_name": "主 Agent",
                        "model": "runtime-decision",
                        "message": step.handoff_message,
                        "content": step.handoff_message,
                    },
                )
                task.status = BrainTaskStatus.COMPLETED
                task.progress = 100
                task.current_focus = "等待你的下一条消息"
                await session.commit()
                return {
                    **state,
                    "status": transition.status,
                    "kernel_route": transition.route.value,
                }

            if step.action == "request_decision" and step.decision_request is not None:
                decision = step.decision_request.model_copy(update={"status": "pending"})
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.decision_requested",
                    {"message": step.handoff_message, "decision": decision.model_dump(mode="json")},
                )
                task.status = BrainTaskStatus.PENDING_CONFIRMATION
                task.current_focus = "等待你选择一个推进方案"
                await session.commit()
                return {
                    **state,
                    "status": transition.status,
                    "kernel_route": transition.route.value,
                    "pending_decision_id": decision.id,
                }

            if step.action == "ask_user":
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.clarification_requested",
                    {"message": step.handoff_message},
                )
                task.status = BrainTaskStatus.PENDING_CONFIRMATION
                task.current_focus = "等待你补充信息"
                await session.commit()
                return {
                    **state,
                    "status": transition.status,
                    "kernel_route": transition.route.value,
                }

        return {
            **state,
            "status": "finish",
            "kernel_route": MainKernelRoute.FINISH.value,
        }

    async def _smart_summarize(self, state: BrainRuntimeState) -> BrainRuntimeState:
        await self._check_main_turn_boundary(state)
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            await self._stream_summary_turn(session, task, state.get("observations", []))
            if task.status == BrainTaskStatus.PENDING_ACCEPTANCE:
                task.progress = max(task.progress, 90)
                task.current_focus = "本轮专家工作已完成，等待你验收结果"
            else:
                task.status = BrainTaskStatus.COMPLETED
                task.progress = 100
                task.current_focus = "本轮工作已完成，等待你查看结果"
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
        kernel_route = state.get("kernel_route")
        if kernel_route in {route.value for route in MainKernelRoute}:
            return str(kernel_route)
        if state.get("status") == "dispatch":
            return "dispatch"
        if state.get("status") == "tools":
            return "tools"
        if state.get("status") == "waiting_decision":
            return "decision"
        if state.get("status") == "waiting_user":
            return "waiting"
        return "finish"

    async def _decision_gate(self, state: BrainRuntimeState) -> BrainRuntimeState:
        await self._check_main_turn_boundary(state)
        if state.get("status") != "waiting_decision" or not self._native_interrupts:
            return state
        result = interrupt(
            {
                "kind": "decision",
                "decision_id": state.get("pending_decision_id", ""),
            }
        )
        if not isinstance(result, dict) or result.get("kind") != "decision":
            raise ValueError("Invalid decision resume payload")
        expected_id = str(state.get("pending_decision_id") or "")
        if str(result.get("decision_id") or "") != expected_id:
            raise ValueError("Decision resume payload does not match the pending decision")
        observations = list(state.get("observations", []))
        observations.append(
            {
                "kind": "user_decision",
                "decision_id": expected_id,
                "choice_id": str(result.get("choice_id") or ""),
                "summary": str(result.get("choice_title") or ""),
            }
        )
        return {
            **state,
            "status": "ready_to_decide",
            "pending_decision_id": "",
            "observations": observations,
        }

    async def _check_main_turn_boundary(self, state: BrainRuntimeState) -> None:
        if not state.get("agent_run_id"):
            return
        async with _session_from_state(state) as session:
            await _main_action_executor().check_turn_boundary(session, state)

    @staticmethod
    def _route_after_decision_gate(state: BrainRuntimeState) -> str:
        return "waiting" if state.get("status") == "waiting_decision" else "continue"

    async def _permission_gate(self, state: BrainRuntimeState) -> BrainRuntimeState:
        return await self._run_permission_gate(state, ready_status="ready_to_summarize")

    async def _run_permission_gate(
        self,
        state: BrainRuntimeState,
        *,
        ready_status: str,
    ) -> BrainRuntimeState:
        pending_ids = list(state.get("pending_permissions", []))
        if not pending_ids:
            return {**state, "status": ready_status, "pending_permissions": []}
        if not self._native_interrupts:
            return {**state, "status": "waiting_permission"}

        result = interrupt({"kind": "permission", "tool_call_ids": pending_ids})
        if not isinstance(result, dict) or result.get("kind") != "permission":
            raise ValueError("Invalid permission resume payload")

        async with _session_from_state(state) as session:
            rows = (
                await session.scalars(
                    select(AgentToolCall)
                    .where(
                        AgentToolCall.id.in_(pending_ids),
                        AgentToolCall.task_id == state["task_id"],
                    )
                    .order_by(AgentToolCall.id)
                )
            ).all()
            if any(row.status == "waiting_approval" for row in rows):
                raise RuntimeError("Cannot resume while tool approvals are still pending")
            observations = list(state.get("observations", []))
            observations.extend(
                {
                    "kind": "permission_decision",
                    "tool_call_id": row.id,
                    "tool_code": row.tool_code,
                    "approved": row.status == "success",
                    "summary": row.output_summary,
                }
                for row in rows
            )
        return {
            **state,
            "status": ready_status,
            "pending_permissions": [],
            "observations": observations,
        }

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
        operating_context = await _main_agent_operating_context(session, task)
        messages = [
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
        await _chat_main_agent(
            session,
            task,
            "main-agent.acknowledgement",
            operating_context,
            messages,
        )

    async def _stream_summary_turn(
        self,
        session: AsyncSession,
        task: BrainTask,
        observations: list[dict[str, Any]],
    ) -> None:
        if task.brief is None or not observations:
            return
        history = await _parent_thread_messages(session, task, "")
        operating_context = await _main_agent_operating_context(session, task)
        await _chat_main_agent(
            session,
            task,
            "main-agent.summary",
            operating_context,
            [
                *history,
                {
                    "role": "user",
                    "content": f"原目标：{task.brief.goal}\n本轮观察：{observations}",
                },
            ],
        )

    async def _stream_conversation_turn(
        self,
        session: AsyncSession,
        task: BrainTask,
        *,
        client_message_id: str | None = None,
    ) -> None:
        if task.brief is None:
            return
        history = await _parent_thread_messages(session, task, task.brief.goal)
        operating_context = await _main_agent_operating_context(session, task)
        completed_event_ids_before = {
            event.id
            for event in await runtime_events(session, task.id)
            if event.type == "brain.runtime.message_done"
        }
        result, _cost = await _chat_main_agent(
            session,
            task,
            "main-agent.conversation",
            operating_context,
            [
                *history,
                {"role": "user", "content": task.brief.goal},
            ],
        )
        events = await runtime_events(session, task.id)
        completed_this_turn = any(
            event.type == "brain.runtime.message_done"
            and event.id not in completed_event_ids_before
            for event in events
        )
        if not completed_this_turn:
            await self._record_event(
                session,
                task,
                "brain.runtime.message_done",
                {
                    "message_id": _runtime_message_id(
                        client_message_id,
                        AgentCode.DECISION.value,
                    ),
                    "agent_code": AgentCode.DECISION.value,
                    "agent_name": "主 Agent",
                    "model": result.model,
                    "message": result.content,
                    "content": result.content,
                    "client_message_id": client_message_id,
                },
            )

    def _stream_observer(
        self,
        session: AsyncSession,
        task: BrainTask,
        observer_state: _StreamObserverState,
        *,
        client_message_id: str | None = None,
    ):
        async def observer(event: dict[str, Any]) -> None:
            agent_code = str(event.get("agent_code") or "00-decision")
            phase = str(event.get("phase") or "")
            model = str(event.get("model") or "")
            message_id = observer_state.message_id_for(agent_code)
            if model:
                observer_state.models[agent_code] = model
            agent_name = _agent_display_name(agent_code)
            base_payload = {
                "task_id": task.id,
                "thread_id": task.thread_id or self.thread_id_for(task.id),
                "message_id": message_id,
                "agent_code": agent_code,
                "agent_name": agent_name,
                "model": model,
                "client_message_id": client_message_id,
            }
            if phase == "start":
                await publish_realtime_event(
                    "brain.runtime.message_start",
                    base_payload,
                    content_item_id=task.content_item_id,
                    project_id=task.brief.project_id if task.brief else None,
                )
            elif phase == "delta":
                delta = str(event.get("delta") or "")
                observer_state.append(agent_code, delta)
                await publish_realtime_event(
                    "brain.runtime.message_delta",
                    {**base_payload, "delta": delta},
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
        event_row = Event(
            type=event_type,
            content_item_id=task.content_item_id,
            project_id=task.brief.project_id if task.brief else None,
            payload=event_payload,
        )
        session.add(event_row)
        await session.commit()
        await session.refresh(event_row)
        await publish_realtime_event(
            event_type,
            event_payload,
            content_item_id=task.content_item_id,
            project_id=task.brief.project_id if task.brief else None,
            event_id=event_row.id,
        )
        if (
            event_type == "brain.runtime.message_done"
            and settings.agent_runtime_memory_auto_compact_enabled
        ):
            try:
                await runtime_memory_service.maybe_compact(session, task)
            except Exception:
                pass


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
    events = await runtime_events(session, task.id)
    latest_started_id = max(
        (event.id for event in events if event.type == "brain.runtime.started"),
        default=0,
    )
    latest_stopped_id = max(
        (event.id for event in events if event.type == "brain.runtime.generation_stopped"),
        default=0,
    )
    if latest_stopped_id > latest_started_id:
        return "stopped"
    latest_waiting_user_id = max(
        (
            event.id
            for event in events
            if event.type == "brain.runtime.clarification_requested"
        ),
        default=0,
    )
    latest_waiting_decision_id = max(
        (
            event.id
            for event in events
            if event.type == "brain.runtime.decision_requested"
        ),
        default=0,
    )
    latest_decision_selected_id = max(
        (
            event.id
            for event in events
            if event.type == "brain.runtime.decision_selected"
        ),
        default=0,
    )
    pending = await _pending_permissions(session, task.id, task.org_id)
    if pending:
        return "waiting_permission"
    if latest_waiting_decision_id > max(latest_started_id, latest_decision_selected_id):
        return "waiting_decision"
    if latest_waiting_user_id > latest_started_id:
        return "waiting_user"
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
            select(AgentToolCall).where(AgentToolCall.task_id == task_id).order_by(AgentToolCall.id)
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

    return await runtime_memory_service.build_runtime_context(
        session,
        task,
        current_message=current_message,
        budget_chars=settings.agent_runtime_context_char_budget,
    )


async def _main_agent_operating_context(
    session: AsyncSession,
    task: BrainTask,
) -> str:
    if task.brief is None:
        return "当前未绑定平台、账号或项目。"

    platform_names = [
        _platform_display_name(platform) for platform in task.brief.platforms if platform
    ]
    account_ids = [int(account_id) for account_id in task.brief.account_ids]
    account_rows = (
        (
            await session.scalars(
                select(Account)
                .where(Account.org_id == task.org_id, Account.id.in_(account_ids))
                .order_by(Account.id)
            )
        ).all()
        if account_ids
        else []
    )
    accounts = "、".join(
        f"{account.nickname}（{_platform_display_name(account.platform.value)}，"
        f"账号 ID {account.id}）"
        for account in account_rows
    )
    parts = [f"当前平台：{'、'.join(platform_names) or '未指定'}"]
    parts.append(f"当前账号：{accounts or '未选择账号'}")
    parts.append(f"当前项目：{task.brief.project_name or '未选择项目'}")
    return "；".join(parts) + "。"


async def _chat_main_agent(
    session: AsyncSession,
    task: BrainTask,
    prompt_id: str,
    operating_context: str,
    messages: list[dict],
):
    prompt = prompt_registry.render(
        prompt_id,
        variables={"operating_context": operating_context},
    )
    account_ids = list(task.brief.account_ids if task.brief else [])
    scope: dict[str, Any] = {"org_id": task.org_id}
    if task.brief is not None and task.brief.project_id is not None:
        scope["project_id"] = task.brief.project_id
    if len(account_ids) == 1:
        scope["account_id"] = int(account_ids[0])
    elif account_ids:
        scope["account_ids"] = [int(item) for item in account_ids]
    context = LLMCallContext(
        task_id=task.id,
        trace_id=task.thread_id,
        prompt_id=prompt.spec.id,
        prompt_version=prompt.spec.version,
        prompt_hash=prompt.content_hash,
        prompt_schema_version=prompt.spec.schema_version,
        scope=scope,
        budget={
            "max_tokens": settings.agent_runtime_max_tokens,
            "max_cost_usd": settings.agent_runtime_max_cost_usd,
        },
    )
    with bind_llm_call_context(context):
        return await gateway.chat(
            session,
            task.org_id,
            AgentCode.DECISION.value,
            [{"role": "system", "content": prompt.content}, *messages],
        )


def _platform_display_name(platform: str) -> str:
    return {
        "douyin": "抖音",
        "xiaohongshu": "小红书",
        "shipinhao": "视频号",
        "tencent": "视频号",
    }.get(platform, platform)


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


def _required_expert_codes(
    state: BrainRuntimeState,
    task: BrainTask,
) -> list[str]:
    required = [
        code
        for code in state.get("required_expert_codes", [])
        if code != AgentCode.DECISION.value
    ]
    if required or task.plan is None:
        return list(dict.fromkeys(required))
    return list(
        dict.fromkeys(
            str(step.get("agent_code"))
            for step in task.plan.steps
            if step.get("agent_code")
            and str(step.get("agent_code")) != AgentCode.DECISION.value
        )
    )


def _successful_expert_codes(observations: list[dict[str, Any]]) -> set[str]:
    successful_statuses = {"done", "success", "completed"}
    return {
        str(observation.get("agent_code"))
        for observation in observations
        if str(observation.get("status") or "").lower() in successful_statuses
        and str(observation.get("summary") or "").strip()
    }


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


def _runtime_message_id(client_message_id: str | None, agent_code: str) -> str:
    run_id = client_message_id or uuid4().hex
    return f"{run_id}:{agent_code}:1"


def _agent_code_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _runtime_budget_guard() -> RuntimeBudgetGuard:
    return RuntimeBudgetGuard(
        RuntimeBudgetLimits(
            max_rounds=settings.agent_runtime_max_rounds,
            max_expert_calls=settings.agent_runtime_max_expert_calls,
            max_expert_calls_per_code=settings.agent_runtime_max_expert_calls_per_code,
            max_tool_calls=settings.agent_runtime_max_tool_calls,
            max_tokens=settings.agent_runtime_max_tokens,
            max_cost_usd=settings.agent_runtime_max_cost_usd,
            max_elapsed_seconds=settings.agent_runtime_max_elapsed_seconds,
        )
    )


def _main_action_executor(
    budget_guard: RuntimeBudgetGuard | None = None,
) -> MainKernelActionExecutor:
    guard = budget_guard or _runtime_budget_guard()
    return MainKernelActionExecutor(
        main_kernel_policy(
            max_rounds=guard.limits.max_rounds,
            max_tool_calls=guard.limits.max_tool_calls,
        )
    )


class _session_from_state:
    """Bind the active AsyncSession to LangGraph nodes during one invocation."""

    def __init__(self, _state: BrainRuntimeState) -> None:
        active_session = _runtime_session.get()
        if active_session is None:
            raise RuntimeError("BrainRuntimeGraph active session is not bound")
        self.session = active_session

    async def __aenter__(self) -> AsyncSession:
        return self.session

    async def __aexit__(self, *_exc: object) -> None:
        return None
