"""Agent 运行时基类。

每个 Agent = system prompt + 绑定模型（经网关按 ModelConfig 路由）+ 输入/输出 schema。
- `BaseAgent`：抽象基类，`run(session, org_id, ctx)` 产出经 schema 校验的交付物 payload。
- `LLMAgent`：调 LLMGateway 的通用实现——组装 messages → chat → 抽取 JSON → 校验 → 失败重试。
真实创作 Agent（M1 E2）继承 LLMAgent，仅声明 code/output_type/prompt 文件名。
"""

import json
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.gateway import LLMCallContext, LLMGateway, bind_llm_call_context, gateway
from app.models.enums import DeliverableType
from app.orchestrator.agent_kernel import (
    KernelAction,
    SpecialistKernelDecision,
)
from app.prompts import prompt_registry
from app.schemas.brain import RuntimeToolCall
from app.schemas.deliverable import DeliverablePayload, validate_payload
from app.services.agent_management import get_business_config


class AgentContext(BaseModel):
    """Agent 执行上下文：上游交付物、知识库切片、已采纳优化建议。"""

    content_item_id: int
    task_id: int | None = None
    invocation_id: int | None = None
    trace_id: str | None = None
    project_id: int | None = None
    account_id: int | None = None
    request: str | None = None
    upstream: dict[str, Any] = Field(default_factory=dict)
    knowledge: dict[str, list[dict]] = Field(default_factory=dict)
    optimization_suggestions: list[dict] = Field(default_factory=list)
    budget: dict = Field(default_factory=dict)


class BaseAgent(ABC):
    """所有 Agent 的基类。"""

    code: str
    output_type: DeliverableType

    @abstractmethod
    async def run(
        self, session: AsyncSession, org_id: int | None, ctx: AgentContext
    ) -> DeliverablePayload:
        """执行一次工作，产出经 schema 校验的交付物 payload。"""
        ...


def extract_json(text: str) -> dict:
    """Parse one complete JSON object without guessing embedded fragments."""
    try:
        value = json.loads(text.strip())
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"模型输出无法解析为 JSON：{text[:200]}") from exc
    if not isinstance(value, dict):
        raise ValueError("模型输出必须是唯一的 JSON 对象")
    return value


class LLMAgent(BaseAgent):
    """调 LLMGateway 的通用 Agent：system prompt + 上游输入 → JSON → schema 校验。

    子类只需类属性声明 `code` / `output_type` / `prompt_name`（prompt 文件名，不含扩展名）。
    校验失败会带着错误信息重试一次（让模型自我纠正），仍失败则抛出。
    """

    prompt_name: str
    max_retries: int = 1

    def __init__(self, llm: LLMGateway | None = None) -> None:
        self._llm = llm or gateway

    def build_user_message(self, ctx: AgentContext) -> str:
        """把上游交付物与知识库切片组织成给模型的输入。子类可覆盖定制。"""
        parts: list[str] = []
        if ctx.request:
            parts.append("【用户本次要求】\n" + ctx.request)
        if ctx.upstream:
            dumped = json.dumps(ctx.upstream, ensure_ascii=False, indent=2)
            parts.append("【上游交付物】\n" + dumped)
        if ctx.knowledge:
            dumped = json.dumps(ctx.knowledge, ensure_ascii=False, indent=2)
            parts.append("【知识库参考】\n" + dumped)
        if ctx.optimization_suggestions:
            dumped = json.dumps(ctx.optimization_suggestions, ensure_ascii=False, indent=2)
            parts.append("【已采纳优化建议】\n" + dumped)
        if not parts:
            parts.append("（无上游输入，按 system prompt 直接产出。）")
        parts.append("请严格按要求输出唯一的 JSON 对象，不要附加解释文字。")
        return "\n\n".join(parts)

    async def kernel_decide(
        self,
        session: AsyncSession,
        org_id: int | None,
        ctx: AgentContext,
        *,
        available_tools: list[dict[str, Any]],
        observations: list[dict[str, Any]],
    ) -> SpecialistKernelDecision:
        """Return one bounded specialist decision for the shared Agent Kernel."""
        loaded_prompt = prompt_registry.load(f"expert.{self.prompt_name}")
        system_prompt = await self._managed_system_prompt(
            session,
            org_id,
            loaded_prompt.content,
        )
        protocol = {
            "role": "bounded_specialist",
            "allowed_actions": ["call_tools", "finish", "blocked"],
            "rules": [
                "Use only the tools listed in available_tools.",
                "Never dispatch another expert.",
                "Never ask or message the user directly.",
                "Finish only when the typed deliverable is supported by available evidence.",
                "Return exactly one JSON object and no surrounding prose.",
            ],
            "response_contract": {
                "action": "call_tools | finish | blocked",
                "rationale": "short internal execution rationale",
                "tool_calls": [
                    {
                        "tool_code": "tool code from available_tools",
                        "arguments": {},
                        "purpose": "why this evidence is needed",
                    }
                ],
                "deliverable": "the normal expert deliverable object when action=finish",
                "blocked_reason": "required when action=blocked",
            },
        }
        system_prompt = (
            f"{system_prompt}\n\n[AGENT KERNEL PROTOCOL]\n"
            f"{json.dumps(protocol, ensure_ascii=False, indent=2)}"
        )
        kernel_input = {
            "available_tools": available_tools,
            "tool_observations": observations,
        }
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"{self.build_user_message(ctx)}\n\n"
                    "[KERNEL INPUT]\n"
                    f"{json.dumps(kernel_input, ensure_ascii=False, indent=2)}"
                ),
            },
        ]

        last_err: str | None = None
        for _attempt in range(self.max_retries + 1):
            if last_err is not None:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Previous kernel decision was invalid: {last_err}\n"
                            "Return one corrected JSON object."
                        ),
                    }
                )
            with bind_llm_call_context(self._call_context(ctx, org_id, loaded_prompt)):
                result, _cost = await self._llm.chat(
                    session,
                    org_id,
                    self.code,
                    messages,
                )
            try:
                data = extract_json(result.content)
                return self._parse_kernel_decision(
                    data,
                    ctx=ctx,
                    observation_count=len(observations),
                )
            except (KeyError, TypeError, ValueError, ValidationError) as exc:
                last_err = str(exc)
                messages.append({"role": "assistant", "content": result.content})

        raise ValueError(
            f"[{self.code}] kernel decision failed after "
            f"{self.max_retries + 1} attempts: {last_err}"
        )

    def _parse_kernel_decision(
        self,
        data: dict[str, Any],
        *,
        ctx: AgentContext,
        observation_count: int,
    ) -> SpecialistKernelDecision:
        # Compatibility: an old expert may still return its deliverable directly.
        if "action" not in data:
            return SpecialistKernelDecision(
                action=KernelAction.FINISH,
                rationale="Legacy direct deliverable.",
                deliverable=validate_payload(self.output_type, data),
            )

        action = KernelAction(data["action"])
        rationale = str(data.get("rationale") or "").strip()
        if not rationale:
            raise ValueError("kernel decision requires rationale")
        if action == KernelAction.CALL_TOOLS:
            raw_calls = data.get("tool_calls")
            if not isinstance(raw_calls, list) or not raw_calls:
                raise ValueError("call_tools requires at least one tool call")
            calls = tuple(
                RuntimeToolCall.model_validate(
                    {
                        "tool_code": raw["tool_code"],
                        "arguments": raw.get("arguments") or {},
                        "purpose": raw["purpose"],
                        "idempotency_key": self._tool_idempotency_key(
                            ctx,
                            observation_count=observation_count,
                            call_index=index,
                            tool_code=str(raw["tool_code"]),
                        ),
                    }
                )
                for index, raw in enumerate(raw_calls)
            )
            return SpecialistKernelDecision(
                action=action,
                rationale=rationale,
                tool_calls=calls,
            )
        if action == KernelAction.FINISH:
            deliverable = data.get("deliverable")
            if not isinstance(deliverable, dict):
                raise ValueError("finish requires a deliverable object")
            return SpecialistKernelDecision(
                action=action,
                rationale=rationale,
                deliverable=validate_payload(self.output_type, deliverable),
            )
        if action == KernelAction.BLOCKED:
            reason = str(data.get("blocked_reason") or rationale).strip()
            return SpecialistKernelDecision(
                action=action,
                rationale=rationale,
                blocked_reason=reason,
            )
        raise ValueError(f"unsupported specialist action: {action.value}")

    def _tool_idempotency_key(
        self,
        ctx: AgentContext,
        *,
        observation_count: int,
        call_index: int,
        tool_code: str,
    ) -> str:
        invocation = ctx.invocation_id or ctx.task_id or ctx.content_item_id
        safe_code = tool_code.replace(".", "-")[:48]
        return (
            f"expert:{invocation}:observation:{observation_count}:"
            f"call:{call_index}:{safe_code}"
        )[:160]

    async def _managed_system_prompt(
        self,
        session: AsyncSession,
        org_id: int | None,
        base_prompt: str,
    ) -> str:
        system_prompt = base_prompt
        if org_id is not None:
            management = await get_business_config(session, org_id, self.code)
            prompt_addition = management["system_prompt"].strip()
            if prompt_addition:
                system_prompt = (
                    f"{system_prompt}\n\n[ORGANIZATION SPECIALIST INSTRUCTIONS]\n"
                    f"{prompt_addition}"
                )
        return system_prompt

    def _call_context(self, ctx: AgentContext, org_id: int | None, loaded_prompt):
        scope = {
            key: value
            for key, value in {
                "org_id": org_id,
                "project_id": ctx.project_id,
                "account_id": ctx.account_id,
            }.items()
            if value is not None
        }
        return LLMCallContext(
            task_id=ctx.task_id,
            invocation_id=ctx.invocation_id,
            trace_id=ctx.trace_id,
            prompt_id=loaded_prompt.spec.id,
            prompt_version=loaded_prompt.spec.version,
            prompt_hash=loaded_prompt.content_hash,
            prompt_schema_version=loaded_prompt.spec.schema_version,
            scope=scope,
            budget=dict(ctx.budget),
            response_format={"type": "json_object"},
        )

    async def run(
        self, session: AsyncSession, org_id: int | None, ctx: AgentContext
    ) -> DeliverablePayload:
        loaded_prompt = prompt_registry.load(f"expert.{self.prompt_name}")
        system_prompt = loaded_prompt.content
        if org_id is not None:
            management = await get_business_config(session, org_id, self.code)
            prompt_addition = management["system_prompt"].strip()
            if prompt_addition:
                system_prompt = (
                    f"{system_prompt}\n\n【本组织专家补充指令】\n{prompt_addition}"
                )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": self.build_user_message(ctx)},
        ]

        last_err: str | None = None
        for _attempt in range(self.max_retries + 1):
            if last_err is not None:
                retry_msg = f"上次输出校验失败：{last_err}\n请修正后重新输出唯一 JSON 对象。"
                messages.append({"role": "user", "content": retry_msg})
            scope = {
                key: value
                for key, value in {
                    "org_id": org_id,
                    "project_id": ctx.project_id,
                    "account_id": ctx.account_id,
                }.items()
                if value is not None
            }
            call_context = LLMCallContext(
                task_id=ctx.task_id,
                invocation_id=ctx.invocation_id,
                trace_id=ctx.trace_id,
                prompt_id=loaded_prompt.spec.id,
                prompt_version=loaded_prompt.spec.version,
                prompt_hash=loaded_prompt.content_hash,
                prompt_schema_version=loaded_prompt.spec.schema_version,
                scope=scope,
                budget=dict(ctx.budget),
                response_format={"type": "json_object"},
            )
            with bind_llm_call_context(call_context):
                result, _cost = await self._llm.chat(
                    session,
                    org_id,
                    self.code,
                    messages,
                )
            try:
                data = extract_json(result.content)
                return validate_payload(self.output_type, data)
            except (ValueError, ValidationError) as exc:
                last_err = str(exc)
                messages.append({"role": "assistant", "content": result.content})

        raise ValueError(f"[{self.code}] 输出经 {self.max_retries + 1} 次仍未通过校验：{last_err}")
