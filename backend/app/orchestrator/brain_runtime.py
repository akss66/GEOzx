"""Claude Code-style visible runtime for the operations brain.

This runtime keeps the existing ledgers (`BrainTask`, `AgentInvocation`,
`AgentToolCall`, `DeliverableAcceptance`) and adds a live LLM token stream on
top. The stream is broadcast through WebSocket events while durable checkpoints
are stored as `Event` rows.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypedDict, cast
from uuid import uuid4

from langchain_core.runnables.config import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.core.events import (
    publish_realtime_event,
    record_runtime_event_once,
    runtime_event_idempotency_key,
)
from app.llm.gateway import (
    LLMCallContext,
    bind_llm_call_context,
    gateway,
    reset_stream_observer,
    set_stream_observer,
)
from app.models import (
    Account,
    AgentInvocation,
    AgentRun,
    AgentToolCall,
    BrainTask,
    ConversationTurn,
    Deliverable,
    DeliverableAcceptance,
    Event,
    ReflectionRecord,
    SkillRun,
    StrategyPlan,
    TaskBrief,
    User,
)
from app.models.enums import (
    AgentCode,
    AgentInvocationStatus,
    BrainTaskStatus,
    DeliverableAcceptanceStatus,
)
from app.orchestrator.agent_harness import agent_harness
from app.orchestrator.agent_identity import (
    OPERATIONS_BRAIN_DISPLAY_NAME,
    with_operations_brain_public_identity,
)
from app.orchestrator.agent_kernel import AgentKernelPolicyError, main_kernel_policy
from app.orchestrator.ai_coo_critic import (
    CriticDisposition,
    ai_coo_critic_service,
)
from app.orchestrator.ai_coo_runtime import ai_coo_operating_service
from app.orchestrator.brain_adapter import run_brain_task_pipeline
from app.orchestrator.brain_intelligence import IntelligenceUnavailable, brain_intelligence
from app.orchestrator.capability_registry import runtime_capabilities
from app.orchestrator.main_kernel import MainKernelActionExecutor, MainKernelRoute
from app.orchestrator.runtime_budget import RuntimeBudgetGuard, RuntimeBudgetLimits
from app.orchestrator.runtime_scope import RuntimeScope, RuntimeScopeConflict
from app.orchestrator.runtime_tools import build_runtime_tool_adapter
from app.orchestrator.tool_executor import DurableToolExecutor, ToolExecutionOutcome
from app.prompts import prompt_registry
from app.schemas.brain import (
    DecisionRequest,
    IntentDecision,
    RuntimeNextStep,
    RuntimeToolCall,
    route_decision_from_legacy_intent,
)
from app.schemas.conversation import TurnExecutionMode, TurnRouteDecision
from app.services.ai_coo_evidence import build_account_situation
from app.services.ai_coo_learning import ai_coo_learning_service
from app.services.runtime_memory import runtime_memory_service
from app.services.runtime_state import (
    RuntimeEventSpec,
    RuntimeStateScope,
    close_runtime_state,
)

_MIGRATED_OPERATION_INTENTS = frozenset(
    {
        "account_positioning",
        "topic_planning",
        "script_generation",
        "visual_brief_generation",
        "content_calendar_planning",
        "publishing_preparation",
        "content_publishing",
        "engagement_review",
        "performance_review",
        "operation_iteration",
    }
)


class BrainRuntimeState(TypedDict, total=False):
    task_id: int
    agent_run_id: int | None
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
    active_client_id: int | None
    active_project_id: int | None
    account_id: int | None
    available_client_ids: list[int]
    available_project_ids: list[int]
    normalized_goal: dict[str, Any]
    situation_summary: dict[str, Any]
    evidence_refs: list[dict[str, Any]]
    memory_context: dict[str, Any]
    strategy_plan_id: int
    strategy_status: str
    decision_trace_id: int
    task_plan: list[dict[str, Any]]
    current_outputs: list[dict[str, Any]]
    quality_score_ids: list[int]
    critic_iteration: int
    critic_route: str
    critic_feedback: list[dict[str, Any]]
    reflection_record_id: int
    reflection_status: str
    experience_candidate_ids: list[str]
    next_strategy: dict[str, Any]


_runtime_session: ContextVar[AsyncSession | None] = ContextVar(
    "brain_runtime_session",
    default=None,
)
_runtime_event_identity: ContextVar[tuple[int, int, int, str] | None] = ContextVar(
    "brain_runtime_event_identity",
    default=None,
)
_runtime_message_semantic: ContextVar[str | None] = ContextVar(
    "brain_runtime_message_semantic",
    default=None,
)
_BOUNDED_WORKFLOW_ACKNOWLEDGEMENT = (
    "已收到你的账号运营需求。我会先核对数据和执行条件；"
    "只有对应专家实际完成分析后，才会向你交付正式结论。"
)
_SPECIALIST_RESULT_BLOCKED_MESSAGE = (
    "本轮未获得已完成的专家分析，因此不能生成正式诊断结论。请检查账号授权和专家执行状态后重试。"
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


@dataclass
class _TaskFreeRealtimeStream:
    turn: ConversationTurn
    run: AgentRun
    has_deltas: bool = False
    next_sequence: int = 0

    async def observe(self, event: dict[str, Any]) -> None:
        phase = str(event.get("phase") or "")
        client_message_id = str(self.run.client_message_id or "")
        base_payload = {
            "task_id": None,
            "thread_id": self.turn.thread_id,
            "turn_id": self.turn.id,
            "message_id": _runtime_message_id(
                client_message_id,
                AgentCode.DECISION.value,
            ),
            "agent_code": AgentCode.DECISION.value,
            "agent_name": OPERATIONS_BRAIN_DISPLAY_NAME,
            "model": str(event.get("model") or ""),
            "client_message_id": client_message_id,
        }
        if phase == "start":
            self.has_deltas = False
            await publish_realtime_event(
                "brain.runtime.message_start",
                {**base_payload, "stream_seq": self.next_sequence},
            )
            self.next_sequence += 1
        elif phase == "delta":
            delta = str(event.get("delta") or "")
            if not delta:
                return
            self.has_deltas = True
            await publish_realtime_event(
                "brain.runtime.message_delta",
                {
                    **base_payload,
                    "delta": delta,
                    "stream_seq": self.next_sequence,
                },
            )
            self.next_sequence += 1


class BrainRuntimeGraph:
    """LangGraph wrapper that exposes a brain task as a resumable agent runtime."""

    def __init__(self, checkpointer: Any | None = None) -> None:
        self._compile_graphs(checkpointer)

    async def configure_checkpointer(self, checkpointer: Any | None) -> None:
        """Atomically rebuild compiled graphs around one worker-owned saver."""

        self._compile_graphs(checkpointer)

    @staticmethod
    def graph_config(thread_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": thread_id}}

    @staticmethod
    def task_free_realtime_stream(
        *,
        turn: ConversationTurn,
        run: AgentRun,
    ) -> _TaskFreeRealtimeStream:
        if (
            run.task_id is not None
            or run.turn_id != turn.id
            or run.thread_id != turn.thread_id
            or not run.client_message_id
        ):
            raise ValueError("task-free realtime stream ownership does not match")
        return _TaskFreeRealtimeStream(turn=turn, run=run)

    async def deliver_task_free_turn(
        self,
        session: AsyncSession,
        *,
        turn: ConversationTurn,
        run: AgentRun,
        account_id: int,
        route_decision: TurnRouteDecision,
        response: str,
        result_payload: dict[str, Any],
        status: str = "completed",
        error_code: str | None = None,
        response_streamed: bool = False,
        extra_events: list[tuple[str, str, dict[str, Any]]] | None = None,
    ) -> None:
        """Durably deliver a Turn response without creating or reading a BrainTask."""

        if run.task_id is not None:
            raise ValueError("task-free Turn delivery cannot own a BrainTask")
        if run.turn_id != turn.id or run.thread_id != turn.thread_id or run.org_id != turn.org_id:
            raise ValueError("AgentRun and ConversationTurn ownership do not match")
        client_message_id = str(run.client_message_id or "")
        if not client_message_id:
            raise ValueError("task-free Turn delivery requires client_message_id")

        turn.assistant_response = response
        turn.intent = route_decision.model_dump(mode="json")
        run.status = status
        run.phase = status
        run.finished_at = datetime.now(UTC)
        run.lease_owner = None
        run.leased_until = None
        run.next_retry_at = None
        run.error_code = error_code
        run.error_detail = None
        run.result_payload = result_payload

        lineage = {
            "task_id": None,
            "thread_id": turn.thread_id,
            "turn_id": turn.id,
        }
        event_specs: list[tuple[str, str, dict[str, Any]]] = [
            (
                "brain.runtime.started",
                "turn-started",
                {"message": "Main Agent received this conversation Turn."},
            ),
            (
                "brain.runtime.intent_classified",
                "turn-route",
                {
                    "message": "Main Agent selected the execution route.",
                    "route_decision": route_decision.model_dump(mode="json"),
                },
            ),
            *(extra_events or []),
            (
                "brain.runtime.message_done",
                "turn-response",
                {
                    "message": response,
                    "content": response,
                    "message_id": _runtime_message_id(
                        client_message_id,
                        AgentCode.DECISION.value,
                    ),
                    "agent_code": AgentCode.DECISION.value,
                    "agent_name": OPERATIONS_BRAIN_DISPLAY_NAME,
                    "model": "system",
                },
            ),
            (
                {
                    "completed": "brain.runtime.completed",
                    "failed": "brain.runtime.failed",
                }.get(status, "brain.runtime.blocked"),
                "turn-terminal",
                {
                    "message": (
                        "This conversation Turn completed."
                        if status == "completed"
                        else "This conversation Turn needs a recoverable next step."
                    ),
                    **({"error_code": error_code} if error_code else {}),
                },
            ),
        ]
        broadcasts: list[tuple[Event, str]] = []
        for event_type, semantic_key, payload in event_specs:
            event_row, created = await record_runtime_event_once(
                session,
                org_id=turn.org_id,
                account_id=account_id,
                run_id=run.id,
                client_message_id=client_message_id,
                event_type=event_type,
                semantic_key=semantic_key,
                payload={**lineage, **payload},
            )
            if not created:
                continue
            event_row.thread_id = turn.thread_id
            event_row.turn_id = turn.id
            event_row.run_id = run.id
            skill_run_id = payload.get("skill_run_id")
            if isinstance(skill_run_id, int) and skill_run_id > 0:
                event_row.skill_run_id = skill_run_id
            broadcasts.append((event_row, event_type))

        await session.commit()
        for event_row, event_type in broadcasts:
            await session.refresh(event_row)
            payload = dict(event_row.payload or {})
            await publish_realtime_event(
                event_type,
                payload,
                event_id=event_row.id,
            )

    async def deliver_operation_turn_state(
        self,
        session: AsyncSession,
        *,
        task: BrainTask,
        turn: ConversationTurn,
        run: AgentRun,
        account_id: int,
        project_id: int | None,
        response: str,
        result_payload: dict[str, Any],
        run_status: str,
        task_status: BrainTaskStatus,
        error_code: str | None = None,
    ) -> None:
        """Persist one routed operation's visible terminal or paused state."""

        if run.task_id != task.id:
            raise ValueError("operation AgentRun does not own the BrainTask")
        if (
            run.turn_id != turn.id
            or run.thread_id != turn.thread_id
            or run.org_id != turn.org_id
            or task.org_id != turn.org_id
        ):
            raise ValueError("operation Task, Run, and Turn ownership do not match")

        turn.assistant_response = response
        task.status = task_status
        task.current_focus = response[:500]
        if task_status is BrainTaskStatus.COMPLETED:
            task.progress = 100
        elif task_status is BrainTaskStatus.FAILED:
            task.progress = 0
        run.status = run_status
        run.phase = run_status
        run.finished_at = datetime.now(UTC)
        run.lease_owner = None
        run.leased_until = None
        run.next_retry_at = None
        run.error_code = error_code
        run.error_detail = None
        run.result_payload = result_payload

        event_type = {
            "completed": "brain.runtime.completed",
            "failed": "brain.runtime.failed",
            "stopped": "brain.runtime.generation_stopped",
        }.get(run_status, "brain.runtime.turn_paused")
        event_row, created = await record_runtime_event_once(
            session,
            org_id=turn.org_id,
            account_id=account_id,
            run_id=run.id,
            client_message_id=run.client_message_id,
            event_type=event_type,
            semantic_key="operation-turn-state",
            payload={
                "task_id": task.id,
                "thread_id": turn.thread_id,
                "turn_id": turn.id,
                "message": response,
                "status": run_status,
                **({"error_code": error_code} if error_code else {}),
            },
            content_item_id=task.content_item_id,
            project_id=project_id,
        )
        if created:
            event_row.thread_id = turn.thread_id
            event_row.turn_id = turn.id
            event_row.run_id = run.id
        await session.commit()
        if created:
            await session.refresh(event_row)
            await publish_realtime_event(
                event_type,
                dict(event_row.payload or {}),
                content_item_id=task.content_item_id,
                project_id=project_id,
                event_id=event_row.id,
            )

    async def refresh_observation(
        self,
        session: AsyncSession,
        task: BrainTask,
    ) -> ReflectionRecord:
        """Run the evidence-gated post-execution learning graph."""

        task.thread_id = task.thread_id or self.thread_id_for(task.id)
        runtime_session_token = _runtime_session.set(session)
        try:
            observation_state: BrainRuntimeState = {
                "task_id": task.id,
                "thread_id": task.thread_id,
                "status": "refreshing_observation",
            }
            await self._observation_graph.ainvoke(
                observation_state,
                config=self.graph_config(f"{task.thread_id}:observation"),
            )
        finally:
            _runtime_session.reset(runtime_session_token)
        reflection = await session.scalar(
            select(ReflectionRecord)
            .where(
                ReflectionRecord.org_id == task.org_id,
                ReflectionRecord.task_id == task.id,
            )
            .order_by(ReflectionRecord.id.desc())
            .limit(1)
        )
        if reflection is None:
            raise ValueError("任务尚未形成效果观测记录")
        return reflection

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
        smart_graph.add_node("goal_understanding", self._goal_understanding)
        smart_graph.add_node("context_resolution", self._context_resolution)
        smart_graph.add_node("situation_awareness", self._situation_awareness)
        smart_graph.add_node("strategy_planning", self._strategy_planning)
        smart_graph.add_node("task_planning", self._task_planning)
        smart_graph.add_node("dispatch_round", self._dispatch_round)
        smart_graph.add_node("execute_tools", self._execute_tools)
        smart_graph.add_node("observe_round", self._observe_round)
        smart_graph.add_node("critic_review", self._critic_review)
        smart_graph.add_node("collect_permissions", self._collect_permissions)
        smart_graph.add_node("smart_permission_gate", self._smart_permission_gate)
        smart_graph.add_node("decide_next", self._decide_next)
        smart_graph.add_node("decision_gate", self._decision_gate)
        smart_graph.add_node("smart_summarize", self._smart_summarize)
        smart_graph.add_node("wait_for_measurement", self._wait_for_measurement)
        smart_graph.add_edge(START, "goal_understanding")
        smart_graph.add_edge("goal_understanding", "context_resolution")
        smart_graph.add_edge("context_resolution", "situation_awareness")
        smart_graph.add_edge("situation_awareness", "strategy_planning")
        smart_graph.add_edge("strategy_planning", "task_planning")
        smart_graph.add_edge("task_planning", "decide_next")
        smart_graph.add_edge("dispatch_round", "observe_round")
        smart_graph.add_edge("observe_round", "critic_review")
        smart_graph.add_conditional_edges(
            "critic_review",
            self._route_after_critic,
            {
                "pass": "collect_permissions",
                "improve": "dispatch_round",
                "human": "collect_permissions",
            },
        )
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
        smart_graph.add_conditional_edges(
            "execute_tools",
            self._route_after_tool_execution,
            {"waiting": END, "continue": "collect_permissions"},
        )
        smart_graph.add_conditional_edges(
            "decision_gate",
            self._route_after_decision_gate,
            {"continue": "decide_next", "waiting": END},
        )
        smart_graph.add_edge("smart_summarize", "wait_for_measurement")
        smart_graph.add_edge("wait_for_measurement", END)
        self._smart_graph = smart_graph.compile(checkpointer=checkpointer)

        diagnostic_graph = StateGraph(BrainRuntimeState)
        diagnostic_graph.add_node("context_resolution", self._context_resolution)
        diagnostic_graph.add_node("dispatch_round", self._dispatch_round)
        diagnostic_graph.add_node("observe_round", self._observe_round)
        diagnostic_graph.add_node("critic_review", self._critic_review)
        diagnostic_graph.add_node("smart_summarize", self._smart_summarize)
        diagnostic_graph.add_edge(START, "context_resolution")
        diagnostic_graph.add_edge("context_resolution", "dispatch_round")
        diagnostic_graph.add_edge("dispatch_round", "observe_round")
        diagnostic_graph.add_edge("observe_round", "critic_review")
        diagnostic_graph.add_conditional_edges(
            "critic_review",
            self._route_after_critic,
            {
                "pass": "smart_summarize",
                "improve": "dispatch_round",
                "human": END,
            },
        )
        diagnostic_graph.add_edge("smart_summarize", END)
        self._diagnostic_graph = diagnostic_graph.compile(checkpointer=checkpointer)

        query_graph = StateGraph(BrainRuntimeState)
        query_graph.add_node("context_resolution", self._context_resolution)
        query_graph.add_node("query_data_card", self._query_data_card)
        query_graph.add_edge(START, "context_resolution")
        query_graph.add_edge("context_resolution", "query_data_card")
        query_graph.add_edge("query_data_card", END)
        self._query_graph = query_graph.compile(checkpointer=checkpointer)

        smart_resume_graph = StateGraph(BrainRuntimeState)
        smart_resume_graph.add_node("decide_next", self._decide_next)
        smart_resume_graph.add_node("dispatch_round", self._dispatch_round)
        smart_resume_graph.add_node("execute_tools", self._execute_tools)
        smart_resume_graph.add_node("observe_round", self._observe_round)
        smart_resume_graph.add_node("critic_review", self._critic_review)
        smart_resume_graph.add_node("smart_permission_gate", self._smart_permission_gate)
        smart_resume_graph.add_node("decision_gate", self._decision_gate)
        smart_resume_graph.add_node("smart_summarize", self._smart_summarize)
        smart_resume_graph.add_node("wait_for_measurement", self._wait_for_measurement)
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
        smart_resume_graph.add_conditional_edges(
            "execute_tools",
            self._route_after_tool_execution,
            {"waiting": END, "continue": "smart_permission_gate"},
        )
        smart_resume_graph.add_conditional_edges(
            "decision_gate",
            self._route_after_decision_gate,
            {"continue": "decide_next", "waiting": END},
        )
        smart_resume_graph.add_edge("dispatch_round", "observe_round")
        smart_resume_graph.add_edge("observe_round", "critic_review")
        smart_resume_graph.add_conditional_edges(
            "critic_review",
            self._route_after_critic,
            {
                "pass": "smart_permission_gate",
                "improve": "dispatch_round",
                "human": "smart_permission_gate",
            },
        )
        smart_resume_graph.add_conditional_edges(
            "smart_permission_gate",
            self._route_after_smart_permission,
            {"waiting": END, "continue": "decide_next"},
        )
        smart_resume_graph.add_edge("smart_summarize", "wait_for_measurement")
        smart_resume_graph.add_edge("wait_for_measurement", END)
        self._smart_resume_graph = smart_resume_graph.compile(checkpointer=checkpointer)

        observation_graph = StateGraph(BrainRuntimeState)
        observation_graph.add_node("performance_analysis", self._performance_analysis)
        observation_graph.add_node("reflection", self._reflection)
        observation_graph.add_node(
            "experience_verification",
            self._experience_verification,
        )
        observation_graph.add_node("next_strategy", self._next_strategy)
        observation_graph.add_edge(START, "performance_analysis")
        observation_graph.add_conditional_edges(
            "performance_analysis",
            self._route_after_performance_analysis,
            {
                "pending": END,
                "observed": "reflection",
            },
        )
        observation_graph.add_edge("reflection", "experience_verification")
        observation_graph.add_edge("experience_verification", "next_strategy")
        observation_graph.add_edge("next_strategy", END)
        self._observation_graph = observation_graph.compile(checkpointer=checkpointer)

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
        """Compatibility entry point for legacy intent-only callers."""

        return await self.start_routed(
            session,
            task,
            route_decision=intent.route_decision
            or route_decision_from_legacy_intent(
                intent,
                has_account=bool(task.brief and task.brief.account_ids),
            ),
            intent=intent,
            client_message_id=client_message_id,
            agent_run_id=agent_run_id,
            agent_run_attempt=agent_run_attempt,
        )

    async def start_routed(
        self,
        session: AsyncSession,
        task: BrainTask,
        *,
        route_decision: TurnRouteDecision,
        intent: IntentDecision | None = None,
        client_message_id: str | None = None,
        agent_run_id: int | None = None,
        agent_run_attempt: int = 0,
    ) -> BrainTask:
        """Start one turn through the graph selected by its persisted route."""

        if (
            route_decision.mode in {TurnExecutionMode.TASK, TurnExecutionMode.ACTION}
            and route_decision.intent.strip().lower() in _MIGRATED_OPERATION_INTENTS
        ):
            raise ValueError("MIGRATED_OPERATION_REQUIRES_TYPED_SKILL")

        effective_intent = intent or _intent_for_route(route_decision)
        return await self._start_routed_with_intent(
            session,
            task,
            effective_intent,
            route_decision,
            client_message_id=client_message_id,
            agent_run_id=agent_run_id,
            agent_run_attempt=agent_run_attempt,
        )

    async def _start_routed_with_intent(
        self,
        session: AsyncSession,
        task: BrainTask,
        intent: IntentDecision,
        route_decision: TurnRouteDecision,
        *,
        client_message_id: str | None = None,
        agent_run_id: int | None = None,
        agent_run_attempt: int = 0,
    ) -> BrainTask:
        """Execute a validated route while preserving the legacy response contract."""

        task.runtime_mode = "langgraph"
        task.thread_id = task.thread_id or self.thread_id_for(task.id)
        task.status = BrainTaskStatus.RUNNING
        task.current_focus = "运营大脑正在理解你的目标"
        account_ids = list(task.brief.account_ids if task.brief else [])
        runtime_identity_token = _runtime_event_identity.set(
            (task.org_id, account_ids[0], agent_run_id, client_message_id)
            if agent_run_id is not None and client_message_id and len(account_ids) == 1
            else None
        )
        await session.commit()
        await self._record_event(
            session,
            task,
            "brain.runtime.started",
            {
                "message": "运营大脑已接收你的消息。",
                "client_message_id": client_message_id,
            },
        )
        await self._record_event(
            session,
            task,
            "brain.runtime.intent_classified",
            {
                "intent": intent.model_dump(mode="json"),
                "route_decision": route_decision.model_dump(mode="json"),
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
            if route_decision.mode is TurnExecutionMode.ANSWER:
                await self._stream_conversation_turn(
                    session,
                    task,
                    client_message_id=client_message_id,
                )
                task.status = BrainTaskStatus.COMPLETED
                task.progress = 100
                task.current_focus = "运营大脑已完成回复，未调用专家"
                await session.commit()
                return task

            if route_decision.mode is TurnExecutionMode.CLARIFY:
                question = intent.clarifying_question or "这次你最希望优先解决什么问题？"
                task.current_focus = "等待你补充一个关键信息"
                await session.commit()
                await self._stream_runtime_message(
                    session,
                    task,
                    question,
                    model="system",
                    client_message_id=client_message_id,
                )
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.clarification_requested",
                    {
                        "message": question,
                        "missing_field": route_decision.missing_field,
                    },
                )
                return task

            task.runtime_mode = "coo_v1"
            task.current_focus = "运营大脑正在建立运营态势与策略上下文"
            await session.commit()
            expert_codes = _expert_codes_for_route(route_decision, intent)
            state: BrainRuntimeState = {
                "task_id": task.id,
                "agent_run_attempt": agent_run_attempt,
                "thread_id": task.thread_id,
                "round_index": 1,
                "required_expert_codes": expert_codes,
                "selected_experts": (
                    expert_codes if route_decision.mode is TurnExecutionMode.SKILL else []
                ),
                "selected_tool_calls": [],
                "observations": [],
                "runtime_started_at": datetime.now(UTC).isoformat(),
                "expert_dispatch_history": [],
                "tool_call_count": 0,
                "token_count": 0,
                "cost_usd": 0.0,
                "current_outputs": [],
                "quality_score_ids": [],
                "critic_iteration": 0,
                "critic_feedback": [],
            }
            if agent_run_id is not None:
                state["agent_run_id"] = agent_run_id
            if route_decision.mode is TurnExecutionMode.QUERY:
                await self._query_graph.ainvoke(
                    state,
                    config=self.graph_config(task.thread_id),
                )
            elif route_decision.mode is TurnExecutionMode.SKILL:
                await self._stream_main_agent_turn(session, task)
                await self._diagnostic_graph.ainvoke(
                    state,
                    config=self.graph_config(task.thread_id),
                )
            else:
                await self._stream_main_agent_turn(session, task)
                await self._smart_graph.ainvoke(
                    state,
                    config=self.graph_config(task.thread_id),
                )
        finally:
            reset_stream_observer(token)
            _runtime_session.reset(runtime_session_token)
            _runtime_event_identity.reset(runtime_identity_token)
        await session.refresh(task)
        return task

    async def start(self, session: AsyncSession, task: BrainTask) -> BrainTask:
        task.runtime_mode = "langgraph"
        task.thread_id = task.thread_id or self.thread_id_for(task.id)
        task.status = BrainTaskStatus.RUNNING
        task.current_focus = "运营大脑正在理解目标并准备调度专家"
        await session.commit()
        await self._record_event(
            session,
            task,
            "brain.runtime.started",
            {"message": "运营大脑已接收目标，开始建立运行时上下文。"},
        )

        runtime_session_token = _runtime_session.set(session)
        observer_state = _StreamObserverState()
        observer = self._stream_observer(session, task, observer_state)
        token = set_stream_observer(observer)
        try:
            await self._stream_main_agent_turn(session, task)
            graph_state: BrainRuntimeState = {
                "task_id": task.id,
                "thread_id": task.thread_id,
            }
            await self._graph.ainvoke(
                graph_state,
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
        task.current_focus = "运营大脑已完成普通对话，未启动专家工作流"
        await session.commit()
        await self._record_event(
            session,
            task,
            "brain.runtime.message_done",
            {
                "message_id": _runtime_message_id(None, AgentCode.DECISION.value),
                "agent_code": "00-decision",
                "agent_name": OPERATIONS_BRAIN_DISPLAY_NAME,
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
        if not _is_runtime_mode(task.runtime_mode):
            return task
        runtime_identity_token = _runtime_event_identity.set(
            await self._runtime_identity_for_run(session, task, agent_run_id)
        )
        try:
            return await self._resume_after_permission_bound(
                session,
                task,
                tool_call,
                approved,
                agent_run_id=agent_run_id,
                agent_run_attempt=agent_run_attempt,
            )
        finally:
            _runtime_event_identity.reset(runtime_identity_token)

    async def _resume_after_permission_bound(
        self,
        session: AsyncSession,
        task: BrainTask,
        tool_call: AgentToolCall,
        approved: bool,
        *,
        agent_run_id: int | None,
        agent_run_attempt: int,
    ) -> BrainTask:
        task.thread_id = task.thread_id or self.thread_id_for(task.id)
        await self._record_event(
            session,
            task,
            "brain.runtime.resumed",
            {
                "message": "人工确认已返回，运营大脑正在检查是否可以继续执行。",
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
                        },
                    ),
                    config=self.graph_config(task.thread_id),
                )
            elif is_smart_runtime:
                observations = await _runtime_observations(session, task.id)
                resume_state: BrainRuntimeState = {
                    "task_id": task.id,
                    "agent_run_attempt": agent_run_attempt,
                    "thread_id": task.thread_id,
                    "round_index": _next_round_index(events),
                    "selected_experts": [],
                    "observations": observations,
                }
                if agent_run_id is not None:
                    resume_state["agent_run_id"] = agent_run_id
                await self._smart_resume_graph.ainvoke(
                    resume_state,
                    config=self.graph_config(task.thread_id),
                )
            else:
                resume_graph_state: BrainRuntimeState = {
                    "task_id": task.id,
                    "thread_id": task.thread_id,
                }
                await self._resume_graph.ainvoke(
                    resume_graph_state,
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
                "message": "运营大脑正在重新生成这一轮回答。",
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
        runtime_identity_token = _runtime_event_identity.set(
            await self._runtime_identity_for_run(session, task, agent_run_id)
        )
        try:
            return await self._resume_after_decision_bound(
                session,
                task,
                decision_id=decision_id,
                choice_id=choice_id,
                choice_title=choice_title,
                record_selection=record_selection,
                agent_run_id=agent_run_id,
                agent_run_attempt=agent_run_attempt,
            )
        finally:
            _runtime_event_identity.reset(runtime_identity_token)

    async def _resume_after_decision_bound(
        self,
        session: AsyncSession,
        task: BrainTask,
        *,
        decision_id: str,
        choice_id: str,
        choice_title: str,
        record_selection: bool,
        agent_run_id: int | None,
        agent_run_attempt: int,
    ) -> BrainTask:
        if record_selection:
            await self.record_decision_selected(
                session,
                task,
                decision_id=decision_id,
                choice_id=choice_id,
                choice_title=choice_title,
            )
        task.current_focus = "运营大脑正在根据你的选择继续"
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
                        },
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
                    "agent_name": OPERATIONS_BRAIN_DISPLAY_NAME,
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
                "message": "已收到你的修改方向，运营大脑会据此重新整理方案。",
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
            task.current_focus = "运营大脑已锁定当前账号、平台与任务边界"
            await self._record_event(
                session,
                task,
                "brain.runtime.context_loaded",
                {
                    "message": "运营大脑已加载账号上下文。",
                    "platforms": task.brief.platforms if task.brief else [],
                    "account_ids": task.brief.account_ids if task.brief else [],
                },
            )
        return {**state, "status": "context_loaded"}

    async def _plan_execution(self, state: BrainRuntimeState) -> BrainRuntimeState:
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            task.current_focus = "运营大脑已生成专家执行计划"
            steps = task.plan.steps if task.plan else []
            await self._record_event(
                session,
                task,
                "brain.runtime.plan_created",
                {
                    "message": "运营大脑已生成执行计划，准备派发专家。",
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
            existing_invocation_ids = set(
                (
                    await session.scalars(
                        select(AgentInvocation.id).where(AgentInvocation.task_id == task.id)
                    )
                ).all()
            )
            await run_brain_task_pipeline(session, task)
            await self._record_subagent_results(
                session,
                task,
                exclude_invocation_ids=existing_invocation_ids,
            )
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
            completed_tool_results = [
                observation
                for observation in state.get("observations", [])
                if observation.get("kind") == "tool_result"
                and isinstance(observation.get("result"), dict)
            ]
            current_outputs: list[dict[str, Any]] = []
            for code in selected:
                agent_code = AgentCode(code)
                result = await agent_harness.execute(
                    session,
                    user=user,
                    task=task,
                    code=agent_code,
                    purpose=purpose,
                    evidence_refs=evidence_refs,
                    upstream={"tool_results": {"items": completed_tool_results}},
                    run_id=state.get("agent_run_id"),
                    step_key=f"round-{round_index}:{agent_code.value}",
                    attempt=state.get("agent_run_attempt", 0),
                )
                if result is not None:
                    if result.deliverable is None or result.acceptance is None:
                        continue
                    current_outputs.append(
                        {
                            "agent_code": code,
                            "invocation_id": result.invocation.id,
                            "deliverable_id": result.deliverable.id,
                            "acceptance_id": result.acceptance.id,
                        }
                    )
        return {
            **state,
            "status": "round_dispatched",
            "current_outputs": current_outputs,
        }

    async def _observe_round(self, state: BrainRuntimeState) -> BrainRuntimeState:
        await self._check_main_turn_boundary(state)
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            current_invocation_ids = {
                int(item["invocation_id"])
                for item in state.get("current_outputs", [])
                if item.get("invocation_id") is not None
            }
            rows = (
                await session.scalars(
                    select(AgentInvocation)
                    .where(AgentInvocation.task_id == task.id)
                    .order_by(AgentInvocation.id)
                )
            ).all()
            current = [row for row in rows if row.id in current_invocation_ids]
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
                    "invocation_id": invocation.id,
                }
                observations.append(observation)
        return {**state, "status": "round_observed", "observations": observations}

    async def _critic_review(self, state: BrainRuntimeState) -> BrainRuntimeState:
        """Score only the current expert round and bound autonomous rework."""

        await self._check_main_turn_boundary(state)
        outputs = list(state.get("current_outputs", []))
        if not outputs:
            return {**state, "status": "critic_passed", "critic_route": "pass"}

        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            strategy = (
                await session.get(StrategyPlan, state["strategy_plan_id"])
                if state.get("strategy_plan_id") is not None
                else None
            )
            iteration = int(state.get("critic_iteration", 0))
            failed: list[dict[str, Any]] = []
            quality_score_ids = list(state.get("quality_score_ids", []))

            for output in outputs:
                invocation = await session.get(
                    AgentInvocation,
                    int(output["invocation_id"]),
                )
                deliverable = await session.get(
                    Deliverable,
                    int(output["deliverable_id"]),
                )
                if invocation is None or invocation.task_id != task.id or deliverable is None:
                    failed.append(
                        {
                            **output,
                            "issues": ["质量审核找不到本轮专家交付物。"],
                            "suggestions": ["转人工核对调用账本与交付物。"],
                            "disposition": CriticDisposition.HUMAN.value,
                        }
                    )
                    continue

                try:
                    review = await brain_intelligence.review_expert_output(
                        session,
                        task.org_id,
                        goal=task.brief.goal if task.brief else task.title,
                        expert_code=_agent_code_value(invocation.agent_code),
                        expert_name=invocation.agent_name,
                        deliverable=deliverable.payload,
                        situation=dict(state.get("situation_summary", {})),
                        strategy=dict(strategy.strategy if strategy else {}),
                        evidence_refs=list(state.get("evidence_refs", [])),
                        iteration=iteration,
                    )
                    recorded = await ai_coo_critic_service.record(
                        session,
                        task=task,
                        invocation=invocation,
                        deliverable_id=deliverable.id,
                        evaluation=review.evaluation,
                        iteration=iteration,
                        evidence_refs=list(state.get("evidence_refs", [])),
                        prompt_id=review.prompt.spec.id,
                        prompt_version=review.prompt.spec.version,
                        prompt_hash=review.prompt.content_hash,
                        critic_model=review.model,
                    )
                except IntelligenceUnavailable as exc:
                    failed.append(
                        {
                            **output,
                            "agent_name": invocation.agent_name,
                            "issues": [str(exc)],
                            "suggestions": ["转人工审核当前专家交付物。"],
                            "disposition": CriticDisposition.HUMAN.value,
                        }
                    )
                    await self._record_event(
                        session,
                        task,
                        "brain.runtime.critic_unavailable",
                        {
                            "message": "质量审核暂时不可用，已转交人工处理。",
                            "invocation_id": invocation.id,
                            "deliverable_id": deliverable.id,
                            "error": str(exc),
                        },
                    )
                    continue

                quality_score_ids.append(recorded.score.id)
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.critic_scored",
                    {
                        "message": (f"{invocation.agent_name}质量评分 {recorded.score.score} 分。"),
                        "invocation_id": invocation.id,
                        "deliverable_id": deliverable.id,
                        "quality_score_id": recorded.score.id,
                        "score": recorded.score.score,
                        "dimensions": recorded.score.dimensions,
                        "issues": recorded.score.issues,
                        "suggestions": recorded.score.suggestions,
                        "passed": recorded.score.passed,
                        "iteration": iteration,
                    },
                )
                if recorded.disposition != CriticDisposition.PASS:
                    failed.append(
                        {
                            **output,
                            "agent_name": invocation.agent_name,
                            "issues": recorded.score.issues,
                            "suggestions": recorded.score.suggestions,
                            "disposition": recorded.disposition.value,
                        }
                    )

            if not failed:
                return {
                    **state,
                    "status": "critic_passed",
                    "critic_route": "pass",
                    "critic_iteration": 0,
                    "critic_feedback": [],
                    "quality_score_ids": quality_score_ids,
                }

            requires_human = any(
                item["disposition"] == CriticDisposition.HUMAN.value for item in failed
            )
            if not requires_human and iteration < 2:
                for item in failed:
                    acceptance = await session.get(
                        DeliverableAcceptance,
                        int(item["acceptance_id"]),
                    )
                    if acceptance is not None:
                        acceptance.status = DeliverableAcceptanceStatus.RERUN_REQUESTED
                selected = list(dict.fromkeys(str(item["agent_code"]) for item in failed))
                feedback_text = "\n".join(
                    (
                        f"{item.get('agent_name') or _agent_display_name(item['agent_code'])}: "
                        f"{'; '.join(item['suggestions'] or item['issues'])}"
                    )
                    for item in failed
                )
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.improvement_requested",
                    {
                        "message": "质量审核未通过，已要求相关专家按意见返工。",
                        "iteration": iteration + 1,
                        "expert_codes": selected,
                        "feedback": failed,
                    },
                )
                return {
                    **state,
                    "status": "critic_improve",
                    "critic_route": "improve",
                    "critic_iteration": iteration + 1,
                    "critic_feedback": failed,
                    "quality_score_ids": quality_score_ids,
                    "selected_experts": selected,
                    "selected_expert_purpose": (
                        f"{state.get('selected_expert_purpose', '')}\n"
                        f"Critic 返工要求：\n{feedback_text}"
                    ).strip(),
                    "round_index": int(state.get("round_index", 1)) + 1,
                    "current_outputs": [],
                }

            task.status = BrainTaskStatus.PENDING_ACCEPTANCE
            task.current_focus = "质量审核需要人工接管"
            await self._record_event(
                session,
                task,
                "brain.runtime.approval_required",
                {
                    "message": "自动返工已达到上限，当前交付物需要人工审核。",
                    "reason": "critic_human_takeover",
                    "feedback": failed,
                },
            )
            return {
                **state,
                "status": "critic_human",
                "critic_route": "human",
                "critic_feedback": failed,
                "quality_score_ids": quality_score_ids,
            }

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
            scope = await _main_tool_runtime_scope(
                session,
                task=task,
                user=user,
                account_id=account_id,
                agent_run_id=state.get("agent_run_id"),
            )

            for payload in state.get("selected_tool_calls", [])[:5]:
                request = RuntimeToolCall.model_validate(payload)
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.tool_started",
                    {
                        "message": f"运营大脑正在调用 {request.tool_code}。",
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
                    scope=scope,
                )
                if outcome.status == "waiting_approval":
                    continue
                if outcome.status == "ambiguous":
                    return await self._converge_ambiguous_tool_result(
                        session=session,
                        state=state,
                        task=task,
                        request=request,
                        outcome=outcome,
                        account_id=account_id,
                        project_id=project_id,
                        observations=observations,
                    )
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

    async def _converge_ambiguous_tool_result(
        self,
        *,
        session: AsyncSession,
        state: BrainRuntimeState,
        task: BrainTask,
        request: RuntimeToolCall,
        outcome: ToolExecutionOutcome,
        account_id: int | None,
        project_id: int | None,
        observations: list[dict[str, Any]],
    ) -> BrainRuntimeState:
        """Pause an uncertain external write without replaying or claiming success."""

        tool_call = outcome.tool_call
        if tool_call.task_id != task.id:
            raise ValueError("ambiguous ToolCall does not belong to the active BrainTask")

        error_code = "TOOL_RESULT_AMBIGUOUS"
        message = (
            "外部操作的最终结果暂时无法确认。为避免重复执行，系统已停止自动重试；"
            "请先核对平台实际状态，再决定下一步。"
        )
        run_id = int(state.get("agent_run_id") or 0)
        run: AgentRun | None = None
        if run_id:
            run = await session.scalar(
                select(AgentRun)
                .where(AgentRun.id == run_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                run is None
                or run.task_id != task.id
                or run.org_id != task.org_id
                or (tool_call.thread_id is not None and tool_call.thread_id != run.thread_id)
                or (tool_call.turn_id is not None and tool_call.turn_id != run.turn_id)
            ):
                raise ValueError("ambiguous ToolCall AgentRun provenance does not match")
        skill_run_id = int(tool_call.skill_run_id or 0)
        skill_run: SkillRun | None = None
        if skill_run_id:
            if run is None:
                raise ValueError("ambiguous SkillRun requires an active AgentRun")
            skill_run = await session.scalar(
                select(SkillRun)
                .where(SkillRun.id == skill_run_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                skill_run is None
                or skill_run.task_id != task.id
                or skill_run.run_id != run.id
                or skill_run.thread_id != tool_call.thread_id
                or skill_run.turn_id != tool_call.turn_id
                or skill_run.org_id != task.org_id
            ):
                raise ValueError("ambiguous ToolCall SkillRun provenance does not match")
            skill_run.status = "stopped"
            skill_run.error_code = error_code
            skill_run.output_snapshot = {
                **dict(skill_run.output_snapshot or {}),
                "status": "ambiguous",
                "tool_call_id": tool_call.id,
                "tool_code": request.tool_code,
                "error_code": error_code,
            }

        ambiguous_payload = {
            "message": message,
            "status": "ambiguous",
            "error_code": error_code,
            "tool_call_id": tool_call.id,
            "tool_code": request.tool_code,
            **({"skill_run_id": skill_run.id} if skill_run is not None else {}),
        }
        if run_id:
            closure = await close_runtime_state(
                session,
                scope=RuntimeStateScope(
                    run_id=run_id,
                    turn_id=run.turn_id,
                    task_id=task.id,
                    account_id=account_id,
                    project_id=project_id,
                    content_item_id=task.content_item_id,
                    result_payload={
                        **ambiguous_payload,
                        "runtime_status": "waiting_user",
                    },
                    error_detail=message,
                    extra_events=(
                        RuntimeEventSpec(
                            event_type="brain.runtime.tool_ambiguous",
                            semantic_key=f"tool-ambiguous:{tool_call.id}",
                            payload=ambiguous_payload,
                        ),
                    ),
                ),
                status="waiting_user",
                message=message,
                error_code=error_code,
            )
            if closure.turn is None:
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.tool_ambiguous",
                    ambiguous_payload,
                )
        else:
            task.status = BrainTaskStatus.PENDING_CONFIRMATION
            task.current_focus = message[:500]
            await self._record_event(
                session,
                task,
                "brain.runtime.tool_ambiguous",
                ambiguous_payload,
            )

        return {
            **state,
            "status": "waiting_user",
            "kernel_route": MainKernelRoute.WAITING.value,
            "termination_reason": "tool_result_ambiguous",
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

    async def _goal_understanding(self, state: BrainRuntimeState) -> BrainRuntimeState:
        await self._check_main_turn_boundary(state)
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            brief = task.brief
            normalized_goal = {
                "objective": (brief.goal if brief else task.title).strip(),
                "content_goal": brief.content_goal if brief else "",
                "expected_outputs": list(brief.expected_outputs) if brief else [],
                "risk_constraints": list(brief.risk_constraints) if brief else [],
                "platforms": list(brief.platforms) if brief else [],
                "account_ids": list(brief.account_ids) if brief else [],
            }
            task.current_focus = "运营大脑正在理解目标与约束"
            await self._record_event(
                session,
                task,
                "brain.runtime.goal_understood",
                {
                    "message": "运营大脑已明确本轮目标、账号范围与风险约束。",
                    "normalized_goal": normalized_goal,
                },
            )
        return {
            **state,
            "status": "goal_understood",
            "normalized_goal": normalized_goal,
        }

    async def _context_resolution(self, state: BrainRuntimeState) -> BrainRuntimeState:
        await self._check_main_turn_boundary(state)
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            account_ids = list(task.brief.account_ids if task.brief else [])
            account_id = int(account_ids[0]) if account_ids else None
            scope = await ai_coo_operating_service.resolve_context(
                session,
                task=task,
                account_id=account_id,
            )
            task.current_focus = "运营大脑正在确认账号、客户与项目上下文"
            await self._record_event(
                session,
                task,
                "brain.runtime.context_resolved",
                {
                    "message": "运营大脑已锁定本轮可访问的运营上下文。",
                    "account_id": account_id,
                    **scope,
                },
            )
        context_state = cast(BrainRuntimeState, dict(state))
        context_state["status"] = "context_resolved"
        context_state["account_id"] = account_id
        context_state["active_client_id"] = cast(int | None, scope.get("active_client_id"))
        context_state["active_project_id"] = cast(int | None, scope.get("active_project_id"))
        context_state["available_client_ids"] = list(
            cast(list[int], scope.get("available_client_ids", []))
        )
        context_state["available_project_ids"] = list(
            cast(list[int], scope.get("available_project_ids", []))
        )
        return context_state

    async def _query_data_card(self, state: BrainRuntimeState) -> BrainRuntimeState:
        """Return a deterministic account data card without strategy generation."""

        await self._check_main_turn_boundary(state)
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            account_id = state.get("account_id")
            if account_id is None:
                card: dict[str, Any] = {
                    "data_sufficiency": "insufficient",
                    "missing_data": ["account_id"],
                    "evidence_refs": [],
                }
                tool_call_id = None
            else:
                if task.created_by_id is None:
                    raise RuntimeError("brain task has no authenticated creator")
                user = await session.get(User, task.created_by_id)
                if user is None or user.org_id != task.org_id:
                    raise PermissionError("brain task creator is unavailable")
                outcome = await DurableToolExecutor(build_runtime_tool_adapter()).execute(
                    task=task,
                    user=user,
                    request=RuntimeToolCall(
                        tool_code="account.data_context",
                        arguments={"days": 30},
                        purpose="Load the selected account data card.",
                        idempotency_key=(f"query-data-card:{state.get('agent_run_id') or task.id}"),
                    ),
                    project_id=task.brief.project_id if task.brief else None,
                    account_id=int(account_id),
                )
                if outcome.status != "success" or outcome.result is None:
                    raise RuntimeError("account data-card query did not complete")
                card = outcome.result
                tool_call_id = outcome.tool_call.id
            runtime_identity = _runtime_event_identity.get()
            await self._stream_runtime_message(
                session,
                task,
                str(card),
                model="tool:account.data_context",
                client_message_id=(runtime_identity[3] if runtime_identity is not None else None),
            )
            await self._record_event(
                session,
                task,
                "brain.runtime.tool_completed",
                {
                    "message": "Account data card loaded.",
                    "tool_code": "account.data_context",
                    "tool_call_id": tool_call_id,
                    "result": card,
                },
            )
            task.status = BrainTaskStatus.COMPLETED
            task.progress = 100
            task.current_focus = "Account data query completed"
            await session.commit()
        return {
            **state,
            "status": "completed",
            "observations": [
                *state.get("observations", []),
                {
                    "kind": "tool_result",
                    "tool_call_id": tool_call_id,
                    "tool_code": "account.data_context",
                    "result": card,
                },
            ],
        }

    async def _situation_awareness(self, state: BrainRuntimeState) -> BrainRuntimeState:
        await self._check_main_turn_boundary(state)
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            account_ids = list(task.brief.account_ids if task.brief else [])
            account_id = int(account_ids[0]) if account_ids else None
            if account_id is None:
                situation_summary = {
                    "data_sufficiency": "insufficient",
                    "conclusion": "数据不足",
                    "diagnosis": [],
                    "evidence_refs": [],
                    "missing_data": ["账号上下文"],
                    "confidence": "0",
                }
                evidence_refs: list[dict[str, Any]] = []
            else:
                situation = await build_account_situation(
                    session,
                    org_id=task.org_id,
                    account_id=account_id,
                )
                situation_summary = situation.model_dump(mode="json")
                evidence_refs = [item.model_dump(mode="json") for item in situation.evidence_refs]
            task.current_focus = "运营大脑正在核对真实数据与运营态势"
            await self._record_event(
                session,
                task,
                "brain.runtime.situation_assessed",
                {
                    "message": (
                        "运营大脑已完成运营态势核对。"
                        if evidence_refs
                        else "当前证据不足，运营大脑不会生成无依据诊断。"
                    ),
                    "account_id": account_id,
                    "data_sufficiency": situation_summary["data_sufficiency"],
                    "evidence_count": len(evidence_refs),
                    "missing_data": situation_summary["missing_data"],
                },
            )
        return {
            **state,
            "status": "situation_assessed",
            "account_id": account_id,
            "situation_summary": situation_summary,
            "evidence_refs": evidence_refs,
        }

    async def _strategy_planning(self, state: BrainRuntimeState) -> BrainRuntimeState:
        await self._check_main_turn_boundary(state)
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            result = await ai_coo_operating_service.prepare(
                session,
                task=task,
                run_id=state.get("agent_run_id"),
                required_expert_codes=_required_expert_codes(state, task),
            )
            task.current_focus = "运营大脑已形成证据约束下的运营策略"
            await self._record_event(
                session,
                task,
                "brain.runtime.strategy_planned",
                {
                    "message": (
                        "真实数据不足，先补齐基线并继续必要的专业分析。"
                        if result.strategy_status == "data_collection_required"
                        else "运营大脑已基于真实证据形成本轮运营策略。"
                    ),
                    "strategy_plan_id": result.strategy_plan_id,
                    "decision_trace_id": result.decision_trace_id,
                    "strategy_status": result.strategy_status,
                },
            )
        return {
            **state,
            "status": "strategy_planned",
            "active_client_id": result.active_client_id,
            "active_project_id": result.active_project_id,
            "available_client_ids": result.available_client_ids,
            "available_project_ids": result.available_project_ids,
            "normalized_goal": result.normalized_goal,
            "situation_summary": result.situation_summary,
            "evidence_refs": result.evidence_refs,
            "memory_context": result.memory_context.model_dump(mode="json"),
            "strategy_plan_id": result.strategy_plan_id,
            "strategy_status": result.strategy_status,
            "decision_trace_id": result.decision_trace_id,
            "task_plan": result.task_plan,
        }

    async def _task_planning(self, state: BrainRuntimeState) -> BrainRuntimeState:
        await self._check_main_turn_boundary(state)
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            task_plan = list(state.get("task_plan", []))
            task.current_focus = "运营大脑正在按需调度必要专家"
            await self._record_event(
                session,
                task,
                "brain.runtime.task_planned",
                {
                    "message": ("运营大脑已完成按需任务拆解，接下来只调用必要专家。"),
                    "steps": task_plan,
                },
            )
        return {**state, "status": "task_planned"}

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
            required_expert_codes = _required_expert_codes(state, task)
            successful_expert_codes = _successful_expert_codes(state.get("observations", []))
            available_expert_codes = {
                str(item["code"]) for item in capabilities if item.get("kind") == "expert"
            }
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
                pending_expert_codes = [
                    code
                    for code in required_expert_codes
                    if code in available_expert_codes and code not in successful_expert_codes
                ][:3]
                if pending_expert_codes:
                    step = RuntimeNextStep(
                        action="dispatch_experts",
                        expert_codes=[AgentCode(code) for code in pending_expert_codes],
                        rationale=(
                            "本轮结构化控制决策未通过校验，"
                            "按运营大脑已经生成的动态任务计划继续执行必要专家步骤。"
                        ),
                        handoff_message=(
                            "我按刚刚制定的计划继续推进，先把这一步交给对应专家处理。"
                        ),
                        purpose="恢复动态计划中的必要专家步骤",
                        evidence_refs=["dynamic-plan-recovery"],
                    )
                    await self._record_event(
                        session,
                        task,
                        "brain.runtime.decision_recovered",
                        {
                            "message": ("结构化决策已安全恢复，继续执行动态计划中的必要专家步骤。"),
                            "reason": str(exc),
                            "expert_codes": pending_expert_codes,
                        },
                    )
                else:
                    task.status = BrainTaskStatus.FAILED
                    task.current_focus = "运营大脑决策未通过校验，请重试本轮任务"
                    await self._record_event(
                        session,
                        task,
                        "brain.runtime.message_error",
                        {
                            "agent_code": AgentCode.DECISION.value,
                            "agent_name": OPERATIONS_BRAIN_DISPLAY_NAME,
                            "message": str(exc),
                            "error": str(exc),
                            "retryable": True,
                        },
                    )
                    return {
                        **state,
                        "status": "waiting_user",
                        "kernel_route": MainKernelRoute.WAITING.value,
                        "termination_reason": "controller_decision_invalid",
                    }

            if (
                step.action in {"respond", "finish"}
                and required_expert_codes
                and not successful_expert_codes.intersection(required_expert_codes)
            ):
                pending_expert_codes = [
                    code for code in required_expert_codes if code in available_expert_codes
                ][:3]
                if pending_expert_codes:
                    step = step.model_copy(
                        update={
                            "action": "dispatch_experts",
                            "expert_codes": [AgentCode(code) for code in pending_expert_codes],
                            "rationale": (
                                "专业任务必须先取得对应专家的有效结论，运营大脑才能汇总或结束本轮。"
                            ),
                            "handoff_message": (
                                "我先把这项专业任务交给对应专家处理，完成后再为你汇总结论。"
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
                        "message": "运营大脑请求的下一步不符合运行时权限策略。",
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
                    str(item["code"]) for item in capabilities if item.get("kind") == "expert"
                }
                requested = [
                    code.value for code in step.expert_codes if code.value in allowed_codes
                ]
                requested_codes: list[str] = [str(code) for code in requested]
                expert_authorization = budget_guard.authorize_experts(
                    state,
                    requested_codes,
                    purpose=step.purpose or step.rationale,
                    evidence_refs=step.evidence_refs,
                )
                selected = expert_authorization.allowed_codes
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
                    next_state = cast(BrainRuntimeState, dict(expert_authorization.state))
                    next_state["status"] = transition.status
                    next_state["kernel_route"] = transition.route.value
                    next_state["selected_experts"] = selected
                    next_state["selected_expert_purpose"] = step.purpose or step.rationale
                    next_state["selected_expert_evidence_refs"] = step.evidence_refs
                    next_state["round_index"] = state.get("round_index", 1) + 1
                    return next_state
                if expert_authorization.blocked_reason is not None:
                    await self._record_event(
                        session,
                        task,
                        "brain.runtime.loop_blocked",
                        {
                            "message": "检测到重复调度或专家预算已耗尽，我先汇总已有结论。",
                            "reason": expert_authorization.blocked_reason,
                            "expert_codes": requested_codes,
                        },
                    )
                    blocked_state = cast(BrainRuntimeState, dict(expert_authorization.state))
                    blocked_state["status"] = "finish"
                    blocked_state["kernel_route"] = MainKernelRoute.FINISH.value
                    blocked_state["termination_reason"] = expert_authorization.blocked_reason
                    return blocked_state

            if step.action in {"call_tools", "request_permission"} and step.tool_calls:
                tool_authorization = budget_guard.authorize_tools(state, len(step.tool_calls))
                if tool_authorization.allowed_count == 0:
                    await self._record_event(
                        session,
                        task,
                        "brain.runtime.budget_exhausted",
                        {
                            "message": "工具调用已达到本轮安全预算，我先汇总已有结论。",
                            "reason": tool_authorization.blocked_reason,
                        },
                    )
                    blocked_state = cast(BrainRuntimeState, dict(tool_authorization.state))
                    blocked_state["status"] = "finish"
                    blocked_state["kernel_route"] = MainKernelRoute.FINISH.value
                    blocked_state["termination_reason"] = (
                        tool_authorization.blocked_reason or "tool_blocked"
                    )
                    return blocked_state
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.handoff",
                    {
                        "message": step.handoff_message,
                        "tool_codes": [call.tool_code for call in step.tool_calls],
                    },
                )
                next_state = cast(BrainRuntimeState, dict(tool_authorization.state))
                next_state["status"] = transition.status
                next_state["kernel_route"] = transition.route.value
                next_state["selected_tool_calls"] = [
                    call.model_dump(mode="json") for call in step.tool_calls
                ]
                next_state["round_index"] = state.get("round_index", 1) + 1
                return next_state

            if step.action == "respond":
                await self._stream_runtime_message(
                    session,
                    task,
                    step.handoff_message,
                    model="runtime-decision",
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
                await self._stream_runtime_message(
                    session,
                    task,
                    step.handoff_message,
                    model="runtime-decision",
                )
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
            summary_delivered = await self._stream_summary_turn(
                session,
                task,
                state.get("observations", []),
                required_expert_codes=state.get("required_expert_codes", []),
            )
            if not summary_delivered:
                task.status = BrainTaskStatus.FAILED
                task.progress = 0
                task.current_focus = "专家未完成，无法生成正式结论"
                await session.commit()
                return {
                    **state,
                    "status": "blocked",
                    "termination_reason": "no_completed_specialist_invocation",
                }
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

    async def _wait_for_measurement(
        self,
        state: BrainRuntimeState,
    ) -> BrainRuntimeState:
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            strategy = None
            if state.get("strategy_plan_id"):
                strategy = await session.get(StrategyPlan, state["strategy_plan_id"])
            if strategy is None:
                strategy = await session.scalar(
                    select(StrategyPlan)
                    .where(
                        StrategyPlan.org_id == task.org_id,
                        StrategyPlan.task_id == task.id,
                    )
                    .order_by(StrategyPlan.version.desc(), StrategyPlan.id.desc())
                    .limit(1)
                )
            if strategy is None:
                return {**state, "status": "completed"}
            reflection = await ai_coo_learning_service.ensure_pending_observation(
                session,
                task=task,
                strategy=strategy,
                run_id=state.get("agent_run_id"),
            )
            await self._record_event(
                session,
                task,
                "brain.runtime.waiting_for_measurement",
                {
                    "message": "本轮执行已完成，等待新的真实数据进入效果观测。",
                    "reflection_record_id": reflection.id,
                    "strategy_plan_id": strategy.id,
                },
            )
        return {
            **state,
            "reflection_record_id": reflection.id,
            "reflection_status": reflection.status,
            "status": "completed",
        }

    async def _performance_analysis(
        self,
        state: BrainRuntimeState,
    ) -> BrainRuntimeState:
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            reflection = await ai_coo_learning_service.refresh_observation(
                session,
                task=task,
            )
            candidate_ids = [
                str(item.get("key")) for item in reflection.experience_candidates if item.get("key")
            ]
            event_type = (
                "brain.runtime.performance_observed"
                if reflection.status == "observed"
                else "brain.runtime.observation_pending"
            )
            await self._record_event(
                session,
                task,
                event_type,
                {
                    "message": reflection.conclusion,
                    "reflection_record_id": reflection.id,
                    "evidence_refs": reflection.evidence_refs,
                    "observed_outcome": reflection.observed_outcome,
                },
            )
        return {
            **state,
            "reflection_record_id": reflection.id,
            "reflection_status": reflection.status,
            "experience_candidate_ids": candidate_ids,
            "next_strategy": reflection.next_strategy,
            "status": reflection.status,
        }

    async def _reflection(self, state: BrainRuntimeState) -> BrainRuntimeState:
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            reflection = await session.get(
                ReflectionRecord,
                state.get("reflection_record_id"),
            )
            if reflection is None:
                raise ValueError("效果观测记录不存在")
            await self._record_event(
                session,
                task,
                "brain.runtime.reflection_completed",
                {
                    "message": reflection.conclusion,
                    "diagnosis": reflection.diagnosis,
                    "evidence_refs": reflection.evidence_refs,
                },
            )
        return state

    async def _experience_verification(
        self,
        state: BrainRuntimeState,
    ) -> BrainRuntimeState:
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            candidate_ids = list(state.get("experience_candidate_ids") or [])
            await self._record_event(
                session,
                task,
                "brain.runtime.experience_candidates_ready",
                {
                    "message": (
                        "已形成有数据依据的经验候选，等待管理员核验。"
                        if candidate_ids
                        else "本轮没有形成可验证的运营经验。"
                    ),
                    "candidate_keys": candidate_ids,
                    "requires_human_verification": bool(candidate_ids),
                },
            )
        return state

    async def _next_strategy(self, state: BrainRuntimeState) -> BrainRuntimeState:
        async with _session_from_state(state) as session:
            task = await _load_task(session, state["task_id"])
            next_strategy = dict(state.get("next_strategy") or {})
            await self._record_event(
                session,
                task,
                "brain.runtime.next_strategy_ready",
                {
                    "message": "已根据真实结果形成下一轮策略建议。",
                    "next_strategy": next_strategy,
                },
            )
        return {**state, "status": "learning_completed"}

    @staticmethod
    def _route_after_performance_analysis(state: BrainRuntimeState) -> str:
        return "observed" if state.get("reflection_status") == "observed" else "pending"

    @staticmethod
    def _route_after_smart_permission(state: BrainRuntimeState) -> str:
        return "waiting" if state.get("pending_permissions") else "continue"

    @staticmethod
    def _route_after_tool_execution(state: BrainRuntimeState) -> str:
        return "waiting" if state.get("status") == "waiting_user" else "continue"

    @staticmethod
    def _route_after_critic(state: BrainRuntimeState) -> str:
        route = state.get("critic_route")
        return route if route in {"pass", "improve", "human"} else "human"

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
                {"message": "运营大脑已完成本轮专家调度，等待用户验收交付物。"},
            )
        return {**state, "status": "completed"}

    def _route_after_permission_gate(self, state: BrainRuntimeState) -> str:
        return "waiting" if state.get("pending_permissions") else "continue"

    def _subagent_lifecycle_event_type(self, invocation: AgentInvocation) -> str | None:
        if invocation.status == AgentInvocationStatus.DONE:
            return "brain.runtime.subagent_completed"
        if invocation.status in {
            AgentInvocationStatus.FAILED,
            AgentInvocationStatus.BLOCKED,
        }:
            return "brain.runtime.subagent_failed"
        return None

    async def _record_subagent_results(
        self,
        session: AsyncSession,
        task: BrainTask,
        *,
        exclude_invocation_ids: set[int],
    ) -> None:
        invocations = (
            await session.scalars(
                select(AgentInvocation)
                .where(
                    AgentInvocation.task_id == task.id,
                    AgentInvocation.id.not_in(exclude_invocation_ids),
                    AgentInvocation.status.in_(
                        [
                            AgentInvocationStatus.DONE,
                            AgentInvocationStatus.FAILED,
                            AgentInvocationStatus.BLOCKED,
                        ]
                    ),
                )
                .order_by(AgentInvocation.id)
            )
        ).all()
        for invocation in invocations:
            event_type = self._subagent_lifecycle_event_type(invocation)
            if event_type is None:
                continue
            agent_code = (
                invocation.agent_code.value
                if hasattr(invocation.agent_code, "value")
                else str(invocation.agent_code)
            )
            action = (
                "已完成本轮处理" if event_type == "brain.runtime.subagent_completed" else "处理失败"
            )
            await self._record_event(
                session,
                task,
                event_type,
                {
                    "message": f"{invocation.agent_name} {action}。",
                    "agent_code": agent_code,
                    "agent_name": invocation.agent_name,
                    "invocation_id": invocation.id,
                },
            )

    async def _stream_main_agent_turn(self, session: AsyncSession, task: BrainTask) -> None:
        if task.brief is None:
            return
        identity = _runtime_event_identity.get()
        await self._stream_runtime_message(
            session,
            task,
            _BOUNDED_WORKFLOW_ACKNOWLEDGEMENT,
            model="system",
            client_message_id=identity[3] if identity is not None else None,
            semantic_key=_main_agent_message_semantic("main-agent.acknowledgement"),
        )

    async def _stream_summary_turn(
        self,
        session: AsyncSession,
        task: BrainTask,
        observations: list[dict[str, Any]],
        *,
        required_expert_codes: list[str] | None = None,
    ) -> bool:
        if task.brief is None:
            return False
        identity = _runtime_event_identity.get()
        if identity is not None and await self._runtime_message_done_exists(
            session,
            identity[3],
            AgentCode.DECISION.value,
            semantic_key=_main_agent_message_semantic("main-agent.summary"),
        ):
            return True
        invocation_ids = {
            int(item["invocation_id"])
            for item in observations
            if item.get("invocation_id") is not None
        }
        expert_observations_present = any(
            item.get("invocation_id") is not None
            or item.get("kind") in {"expert", "expert_result"}
            or (
                item.get("agent_code") is not None
                and item.get("kind")
                not in {
                    "tool_result",
                    "tool_permission",
                    "permission_decision",
                    "user_decision",
                }
            )
            for item in observations
        )
        invocation_filters = [
            AgentInvocation.task_id == task.id,
            AgentInvocation.id.in_(invocation_ids),
            AgentInvocation.status == AgentInvocationStatus.DONE,
        ]
        latest_invocation_run_id = await session.scalar(
            select(AgentInvocation.run_id)
            .where(AgentInvocation.task_id == task.id)
            .order_by(AgentInvocation.id.desc())
            .limit(1)
        )
        if latest_invocation_run_id is None:
            invocation_filters.append(AgentInvocation.run_id.is_(None))
        else:
            invocation_filters.append(AgentInvocation.run_id == latest_invocation_run_id)
        completed_invocations = (
            (await session.scalars(select(AgentInvocation).where(*invocation_filters))).all()
            if invocation_ids
            else []
        )
        required_codes = set(required_expert_codes or [])
        if required_codes:
            completed_invocations = [
                row
                for row in completed_invocations
                if _agent_code_value(row.agent_code) in required_codes
            ]
        completed_by_id = {row.id: row for row in completed_invocations}
        completed_observations = [
            {
                "invocation_id": row.id,
                "agent_code": _agent_code_value(row.agent_code),
                "agent_name": row.agent_name,
                "summary": row.output_summary,
            }
            for item in observations
            if (row := completed_by_id.get(int(item.get("invocation_id") or 0))) is not None
        ]
        completed_codes = {_agent_code_value(row.agent_code) for row in completed_invocations}
        missing_required_codes = required_codes - completed_codes
        if missing_required_codes or (expert_observations_present and not completed_observations):
            await self._stream_runtime_message(
                session,
                task,
                _SPECIALIST_RESULT_BLOCKED_MESSAGE,
                model="system",
                client_message_id=identity[3] if identity is not None else None,
                semantic_key=_main_agent_message_semantic("main-agent.summary-blocked"),
            )
            await self._record_event(
                session,
                task,
                "brain.runtime.summary_blocked",
                {
                    "message": _SPECIALIST_RESULT_BLOCKED_MESSAGE,
                    "reason": "no_completed_specialist_invocation",
                    "missing_expert_codes": sorted(missing_required_codes),
                },
            )
            return False
        summary_observations = completed_observations or observations
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
                    "content": (f"原目标：{task.brief.goal}\n本轮可信成果：{summary_observations}"),
                },
            ],
        )
        return True

    async def _stream_conversation_turn(
        self,
        session: AsyncSession,
        task: BrainTask,
        *,
        client_message_id: str | None = None,
    ) -> None:
        if task.brief is None:
            return
        if await self._runtime_message_done_exists(
            session,
            client_message_id,
            AgentCode.DECISION.value,
            semantic_key=_main_agent_message_semantic("main-agent.conversation"),
        ):
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
                    "agent_name": OPERATIONS_BRAIN_DISPLAY_NAME,
                    "model": result.model,
                    "message": result.content,
                    "content": result.content,
                    "client_message_id": client_message_id,
                    "semantic_key": _main_agent_message_semantic("main-agent.conversation"),
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
                message_semantic = _runtime_message_semantic.get()
                await self._record_event(
                    session,
                    task,
                    "brain.runtime.message_done",
                    {
                        **base_payload,
                        "message": content,
                        "content": content,
                        "semantic_key": (
                            _main_agent_message_semantic(message_semantic)
                            if message_semantic
                            else message_id
                        ),
                    },
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

    async def _stream_runtime_message(
        self,
        session: AsyncSession,
        task: BrainTask,
        content: str,
        *,
        model: str,
        client_message_id: str | None = None,
        semantic_key: str | None = None,
    ) -> None:
        """Progressively deliver user-facing runtime copy, then persist its checkpoint."""

        message_semantic = semantic_key or _runtime_message_id(
            client_message_id,
            AgentCode.DECISION.value,
        )
        if await self._runtime_message_done_exists(
            session,
            client_message_id,
            AgentCode.DECISION.value,
            semantic_key=message_semantic,
        ):
            return

        message_id = _runtime_message_id(
            client_message_id,
            AgentCode.DECISION.value,
        )
        base_payload = {
            "task_id": task.id,
            "thread_id": task.thread_id or self.thread_id_for(task.id),
            "message_id": message_id,
            "agent_code": AgentCode.DECISION.value,
            "agent_name": OPERATIONS_BRAIN_DISPLAY_NAME,
            "model": model,
            "client_message_id": client_message_id,
            "semantic_key": message_semantic,
        }
        await publish_realtime_event(
            "brain.runtime.message_start",
            base_payload,
            content_item_id=task.content_item_id,
            project_id=task.brief.project_id if task.brief else None,
        )
        for delta in _realtime_text_chunks(content):
            await publish_realtime_event(
                "brain.runtime.message_delta",
                {**base_payload, "delta": delta},
                content_item_id=task.content_item_id,
                project_id=task.brief.project_id if task.brief else None,
            )
            await asyncio.sleep(0.018)
        await self._record_event(
            session,
            task,
            "brain.runtime.message_done",
            {**base_payload, "message": content, "content": content},
        )

    @staticmethod
    async def _runtime_identity_for_run(
        session: AsyncSession,
        task: BrainTask,
        agent_run_id: int | None,
    ) -> tuple[int, int, int, str] | None:
        account_ids = list(
            (
                await session.scalar(
                    select(TaskBrief.account_ids).where(TaskBrief.task_id == task.id)
                )
            )
            or []
        )
        if agent_run_id is None or len(account_ids) != 1:
            return None
        run = await session.get(AgentRun, agent_run_id)
        if (
            run is None
            or run.org_id != task.org_id
            or run.task_id != task.id
            or not run.client_message_id
        ):
            return None
        return task.org_id, int(account_ids[0]), run.id, run.client_message_id

    @staticmethod
    async def _runtime_message_done_exists(
        session: AsyncSession,
        client_message_id: str | None,
        agent_code: str,
        *,
        semantic_key: str | None = None,
    ) -> bool:
        identity = _runtime_event_identity.get()
        if identity is None or not client_message_id:
            return False
        org_id, account_id, run_id, _current_message_id = identity
        key = runtime_event_idempotency_key(
            org_id=org_id,
            account_id=account_id,
            run_id=run_id,
            client_message_id=client_message_id,
            event_type="brain.runtime.message_done",
            semantic_key=semantic_key or _runtime_message_id(client_message_id, agent_code),
        )
        return (
            await session.scalar(select(Event.id).where(Event.idempotency_key == key))
        ) is not None

    async def _record_event(
        self,
        session: AsyncSession,
        task: BrainTask,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        project_id = await session.scalar(
            select(TaskBrief.project_id).where(TaskBrief.task_id == task.id)
        )
        event_payload = {
            **payload,
            "task_id": task.id,
            "thread_id": task.thread_id or self.thread_id_for(task.id),
        }
        identity = _runtime_event_identity.get()
        if identity is not None:
            org_id, account_id, run_id, client_message_id = identity
            semantic_key = str(
                event_payload.get("semantic_key")
                or event_payload.get("message_id")
                or event_payload.get("invocation_id")
                or event_payload.get("tool_call_id")
                or event_type
            )
            event_row, created = await record_runtime_event_once(
                session,
                org_id=org_id,
                account_id=account_id,
                run_id=run_id,
                client_message_id=client_message_id,
                event_type=event_type,
                semantic_key=semantic_key,
                payload=event_payload,
                content_item_id=task.content_item_id,
                project_id=project_id,
            )
            if not created:
                return
        else:
            event_row = Event(
                type=event_type,
                content_item_id=task.content_item_id,
                project_id=project_id,
                payload=event_payload,
            )
            session.add(event_row)
        await session.commit()
        await session.refresh(event_row)
        await publish_realtime_event(
            event_type,
            event_payload,
            content_item_id=task.content_item_id,
            project_id=project_id,
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


def _is_runtime_mode(value: str | None) -> bool:
    return value in {"langgraph", "coo_v1"}


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
    if not _is_runtime_mode(task.runtime_mode):
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
    latest_ambiguous_id = max(
        (event.id for event in events if event.type == "brain.runtime.tool_ambiguous"),
        default=0,
    )
    latest_waiting_user_id = max(
        (event.id for event in events if event.type == "brain.runtime.clarification_requested"),
        default=0,
    )
    latest_waiting_decision_id = max(
        (event.id for event in events if event.type == "brain.runtime.decision_requested"),
        default=0,
    )
    latest_decision_selected_id = max(
        (event.id for event in events if event.type == "brain.runtime.decision_selected"),
        default=0,
    )
    pending = await _pending_permissions(session, task.id, task.org_id)
    if latest_ambiguous_id > latest_started_id:
        return "waiting_user"
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
    return list(
        (
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
    )


async def _runtime_observations(
    session: AsyncSession,
    task_id: int,
) -> list[dict[str, Any]]:
    latest_invocation_run_id = await session.scalar(
        select(AgentInvocation.run_id)
        .where(AgentInvocation.task_id == task_id)
        .order_by(AgentInvocation.id.desc())
        .limit(1)
    )
    invocation_run_filter = (
        AgentInvocation.run_id.is_(None)
        if latest_invocation_run_id is None
        else AgentInvocation.run_id == latest_invocation_run_id
    )
    rows = (
        await session.scalars(
            select(AgentInvocation)
            .where(
                AgentInvocation.task_id == task_id,
                invocation_run_filter,
            )
            .order_by(AgentInvocation.id)
        )
    ).all()
    observations = [
        {
            "kind": "expert",
            "invocation_id": row.id,
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
    message_semantic_token = _runtime_message_semantic.set(prompt_id)
    try:
        with bind_llm_call_context(context):
            return await gateway.chat(
                session,
                task.org_id,
                AgentCode.DECISION.value,
                [
                    {
                        "role": "system",
                        "content": with_operations_brain_public_identity(prompt.content),
                    },
                    *messages,
                ],
            )
    finally:
        _runtime_message_semantic.reset(message_semantic_token)


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


async def _main_tool_runtime_scope(
    session: AsyncSession,
    *,
    task: BrainTask,
    user: User,
    account_id: int | None,
    agent_run_id: int | None,
) -> RuntimeScope | None:
    """Bind V3 conversation tools to their immutable run provenance."""

    if agent_run_id is None:
        return None
    if account_id is None:
        raise RuntimeScopeConflict("main tool runtime has no selected account")
    run = await session.get(AgentRun, agent_run_id)
    if run is None or run.task_id != task.id:
        raise RuntimeScopeConflict("main tool runtime has incomplete provenance")
    if run.thread_id is None and run.turn_id is None:
        return None
    if run.thread_id is None or run.turn_id is None:
        raise RuntimeScopeConflict("main tool runtime has partial conversation provenance")
    scope = RuntimeScope(
        org_id=task.org_id,
        user_id=user.id,
        account_id=account_id,
        thread_id=run.thread_id,
        turn_id=run.turn_id,
        run_id=run.id,
        task_id=task.id,
    )
    await scope.validate(session)
    return scope


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
        code for code in state.get("required_expert_codes", []) if code != AgentCode.DECISION.value
    ]
    if required or task.plan is None:
        return list(dict.fromkeys(required))
    return list(
        dict.fromkeys(
            str(step.get("agent_code"))
            for step in task.plan.steps
            if step.get("agent_code") and str(step.get("agent_code")) != AgentCode.DECISION.value
        )
    )


def _intent_for_route(route: TurnRouteDecision) -> IntentDecision:
    legacy_intent = {
        TurnExecutionMode.ANSWER: "conversation",
        TurnExecutionMode.CLARIFY: "clarification",
        TurnExecutionMode.QUERY: "analysis",
        TurnExecutionMode.SKILL: "workflow",
        TurnExecutionMode.TASK: "workflow",
        TurnExecutionMode.ACTION: "action",
    }[route.mode]
    return IntentDecision(
        intent=legacy_intent,
        confidence=route.confidence,
        reason=route.reason,
        missing_field=route.missing_field,
        clarifying_question=route.clarifying_question,
        suggested_expert_codes=_expert_codes_for_route(route),
        requires_account_context=route.requires_account_context,
        route_decision=route,
    )


def _expert_codes_for_route(
    route: TurnRouteDecision,
    intent: IntentDecision | None = None,
) -> list[str]:
    if route.mode is TurnExecutionMode.SKILL:
        normalized = (route.skill_code or "").strip().lower().replace("-", "_")
        if (
            normalized
            in {
                "account_positioning",
                "account_positioning_diagnosis",
                "positioning",
                "positioning_diagnosis",
            }
            or "positioning" in normalized
        ):
            return [AgentCode.POSITIONING.value]
    if intent is None:
        return []
    return list(dict.fromkeys(code.value for code in intent.suggested_expert_codes))


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
        "00-decision": "运营大脑",
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


def _main_agent_message_semantic(prompt_id: str) -> str:
    return f"{AgentCode.DECISION.value}:{prompt_id}"


def _realtime_text_chunks(content: str, size: int = 6) -> Iterator[str]:
    bounded_size = max(size, (len(content) + 39) // 40)
    for offset in range(0, len(content), bounded_size):
        yield content[offset : offset + bounded_size]


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
