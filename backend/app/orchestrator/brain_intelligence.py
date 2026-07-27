"""Server-side intent and next-step intelligence for the operations brain."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import extract_json
from app.llm.gateway import (
    LLMCallContext,
    bind_llm_call_context,
    gateway,
    reset_stream_observer,
    set_stream_observer,
)
from app.models.enums import AgentCode
from app.prompts import prompt_registry
from app.prompts.manifest import LoadedPrompt
from app.schemas.brain import DecisionRequest, IntentDecision, RuntimeNextStep


class IntelligenceUnavailable(RuntimeError):
    """Raised when a formal routing decision cannot be produced safely."""


_CASUAL_MESSAGES = {
    "你好",
    "您好",
    "嗨",
    "hi",
    "hello",
    "在吗",
    "谢谢",
    "感谢",
}

_AVAILABLE_EXPERTS = [
    AgentCode.POSITIONING.value,
    AgentCode.CONTENT_DIRECTOR.value,
    AgentCode.ART_DIRECTOR.value,
    AgentCode.VIDEO_CREATOR.value,
    AgentCode.EDITOR.value,
    AgentCode.OPERATOR.value,
    AgentCode.ADVERTISER.value,
    AgentCode.CUSTOMER_SERVICE.value,
]


class BrainIntelligence:
    async def classify(
        self,
        session: AsyncSession | None,
        org_id: int,
        message: str,
        *,
        has_account: bool,
    ) -> IntentDecision:
        normalized = _normalize_casual_message(message)
        if normalized in _CASUAL_MESSAGES:
            return IntentDecision(
                intent="conversation",
                confidence=1,
                reason="用户正在进行普通交流。",
                suggested_expert_codes=[],
                requires_account_context=False,
            )

        try:
            prompt = prompt_registry.load("main-agent.intent")
            result, _cost = await _structured_chat(
                session,
                org_id,
                prompt,
                [
                    {"role": "system", "content": prompt.content},
                    {
                        "role": "user",
                        "content": (
                            f"当前是否已选择账号：{'是' if has_account else '否'}\n"
                            f"用户消息：{message}"
                        ),
                    },
                ],
            )
            decision = IntentDecision.model_validate(extract_json(result.content))
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            raise IntelligenceUnavailable("主 Agent 暂时无法可靠理解这条需求") from exc
        except Exception as exc:  # noqa: BLE001 - provider failures become a safe domain error
            raise IntelligenceUnavailable("主 Agent 暂时不可用，请稍后重试") from exc

        decision = _sanitize_intent_decision(decision)
        if decision.requires_account_context and not has_account:
            return IntentDecision(
                intent="clarification",
                confidence=decision.confidence,
                reason="该任务需要明确账号上下文。",
                missing_field="account_id",
                clarifying_question="请先从顶部选择要处理的抖音账号。",
                suggested_expert_codes=[],
                requires_account_context=True,
            )
        return decision

    async def decide_next(
        self,
        session: AsyncSession | None,
        org_id: int,
        goal: str,
        observations: list[dict[str, Any]],
        available_experts: list[str | dict[str, Any]],
        round_index: int,
    ) -> RuntimeNextStep:
        capabilities = _sanitize_capabilities(available_experts)
        allowed_experts = {
            str(item["code"]) for item in capabilities if item.get("kind") == "expert"
        }
        allowed_tools = {
            str(item["code"]) for item in capabilities if item.get("kind") == "tool"
        }
        try:
            prompt = prompt_registry.render(
                "main-agent.next-step",
                variables={
                    "capability_catalog": json.dumps(
                        capabilities,
                        ensure_ascii=False,
                        default=str,
                    )
                },
            )
            result, _cost = await _structured_chat(
                session,
                org_id,
                prompt,
                [
                    {"role": "system", "content": prompt.content},
                    {
                        "role": "user",
                        "content": (
                            f"目标：{goal}\n"
                            f"当前轮次：{round_index}\n"
                            f"专家观察：{observations}"
                        ),
                    },
                ],
            )
            step = RuntimeNextStep.model_validate(extract_json(result.content))
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            raise IntelligenceUnavailable("主 Agent 暂时无法决定可靠的下一步") from exc
        except Exception as exc:  # noqa: BLE001 - provider failures become a safe domain error
            raise IntelligenceUnavailable("主 Agent 暂时不可用，请稍后重试") from exc

        filtered_experts = [
            code for code in step.expert_codes if code.value in allowed_experts
        ]
        filtered_tools = [
            request for request in step.tool_calls if request.tool_code in allowed_tools
        ]
        return step.model_copy(
            update={"expert_codes": filtered_experts, "tool_calls": filtered_tools}
        )

    async def revise_decision(
        self,
        session: AsyncSession | None,
        org_id: int,
        goal: str,
        decision: DecisionRequest,
        comment: str,
        *,
        request_new_options: bool,
    ) -> DecisionRequest:
        """Regenerate a user-facing decision without falling back to fixed options."""

        try:
            prompt = prompt_registry.load("main-agent.decision-revision")
            result, _cost = await _structured_chat(
                session,
                org_id,
                prompt,
                [
                    {"role": "system", "content": prompt.content},
                    {
                        "role": "user",
                        "content": (
                            f"任务目标：{goal}\n"
                            f"当前方案：{decision.model_dump(mode='json')}\n"
                            f"用户修改意见：{comment}\n"
                            f"是否明确要求换一批：{'是' if request_new_options else '否'}"
                        ),
                    },
                ],
            )
            revised = DecisionRequest.model_validate(extract_json(result.content))
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            raise IntelligenceUnavailable("主 Agent 暂时无法可靠地重整方案") from exc
        except Exception as exc:  # noqa: BLE001 - provider failures become a safe domain error
            raise IntelligenceUnavailable("主 Agent 暂时不可用，请稍后重试") from exc

        return revised.model_copy(update={"status": "pending"})


def _normalize_casual_message(message: str) -> str:
    return re.sub(r"[\s，。！？!?、,.]+", "", message).lower()


def _sanitize_intent_decision(decision: IntentDecision) -> IntentDecision:
    experts = [
        code
        for code in decision.suggested_expert_codes
        if code.value in _AVAILABLE_EXPERTS and code != AgentCode.DECISION
    ]
    if decision.intent in {"conversation", "clarification"}:
        experts = []
    question = decision.clarifying_question
    if decision.intent == "clarification" and not question:
        question = "这次你最希望优先解决哪个运营问题？"
    return decision.model_copy(
        update={"suggested_expert_codes": experts, "clarifying_question": question}
    )


async def _structured_chat(
    session: AsyncSession | None,
    org_id: int,
    prompt: LoadedPrompt,
    messages: list[dict],
):
    call_context = LLMCallContext(
        prompt_id=prompt.spec.id,
        prompt_version=prompt.spec.version,
        prompt_hash=prompt.content_hash,
        prompt_schema_version=prompt.spec.schema_version,
        scope={"org_id": org_id},
        response_format={"type": "json_object"},
    )
    token = set_stream_observer(None)
    try:
        with bind_llm_call_context(call_context):
            return await gateway.chat(
                session,
                org_id,
                AgentCode.DECISION.value,
                messages,
            )
    finally:
        reset_stream_observer(token)


def _sanitize_capabilities(
    available: list[str | dict[str, Any]],
) -> list[dict[str, Any]]:
    capabilities: list[dict[str, Any]] = []
    for item in available:
        capability = {"kind": "expert", "code": item} if isinstance(item, str) else dict(item)
        kind = str(capability.get("kind") or "expert")
        code = str(capability.get("code") or "")
        if kind == "expert" and code not in _AVAILABLE_EXPERTS:
            continue
        if kind not in {"expert", "tool", "mcp_server", "mcp_tool"} or not code:
            continue
        capability["kind"] = kind
        capability["code"] = code
        capabilities.append(capability)
    return capabilities


brain_intelligence = BrainIntelligence()
