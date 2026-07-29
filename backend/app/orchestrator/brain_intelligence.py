"""Server-side intent and next-step intelligence for the operations brain."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
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
from app.orchestrator.agent_identity import with_operations_brain_public_identity
from app.orchestrator.capability_router import SkillUnavailable, route_explicit_request
from app.orchestrator.skills.registry import SkillRegistry
from app.prompts import prompt_registry
from app.prompts.manifest import LoadedPrompt
from app.schemas.ai_coo import CriticEvaluation, OperatingStrategyDraft
from app.schemas.brain import DecisionRequest, IntentDecision, RuntimeNextStep
from app.schemas.conversation import TurnExecutionMode, TurnRouteDecision


class IntelligenceUnavailable(RuntimeError):
    """Raised when a formal routing decision cannot be produced safely."""


@dataclass(frozen=True)
class CriticModelReview:
    evaluation: CriticEvaluation
    prompt: LoadedPrompt
    model: str


@dataclass(frozen=True)
class OperatingStrategyModelPlan:
    draft: OperatingStrategyDraft
    prompt: LoadedPrompt
    model: str


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
    async def classify_turn(
        self,
        session: AsyncSession | None,
        org_id: int,
        message: str,
        *,
        has_account: bool,
        platform: str,
        requested_skill_code: str | None = None,
        registry: SkillRegistry | None = None,
    ) -> TurnRouteDecision:
        """Choose the execution mode for one user turn without executing it."""

        normalized = _normalize_casual_message(message)
        if normalized in _CASUAL_MESSAGES:
            return TurnRouteDecision(
                mode=TurnExecutionMode.ANSWER,
                intent="conversation",
                confidence=1,
                reason="用户正在进行普通交流。",
            )

        if requested_skill_code is not None:
            if registry is None:
                raise SkillUnavailable(
                    code="skill_registry_unavailable",
                    reason="explicit_skill_registry_required",
                )
            route = route_explicit_request(
                requested_skill_code,
                platform=platform,
                registry=registry,
                has_account=has_account,
            )
            if route is None:
                raise RuntimeError("explicit skill routing returned no decision")
            return route

        try:
            prompt = prompt_registry.load("main-agent.intent")
            result, _cost = await _structured_chat(
                session,
                org_id,
                prompt,
                [
                    {
                        "role": "system",
                        "content": with_operations_brain_public_identity(prompt.content),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"当前是否已选择账号：{'是' if has_account else '否'}\n"
                            f"当前平台：{platform}\n"
                            f"用户消息：{message}"
                        ),
                    },
                ],
            )
            decision = TurnRouteDecision.model_validate(extract_json(result.content))
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            raise IntelligenceUnavailable("运营大脑暂时无法可靠理解这条需求") from exc
        except Exception as exc:  # noqa: BLE001 - provider failures become a safe domain error
            raise IntelligenceUnavailable("运营大脑暂时不可用，请稍后重试") from exc

        if decision.requires_account_context and not has_account:
            return TurnRouteDecision(
                mode=TurnExecutionMode.CLARIFY,
                intent=decision.intent,
                confidence=decision.confidence,
                reason="该请求需要明确账号上下文。",
                requires_account_context=True,
                missing_field="account_id",
                clarifying_question="请先选择需要查看或操作的账号。",
            )
        return decision

    async def classify(
        self,
        session: AsyncSession | None,
        org_id: int,
        message: str,
        *,
        has_account: bool,
        platform: str = "douyin",
    ) -> IntentDecision:
        decision = await self.classify_turn(
            session,
            org_id,
            message,
            has_account=has_account,
            platform=platform,
        )
        legacy_intent = {
            TurnExecutionMode.ANSWER: "conversation",
            TurnExecutionMode.CLARIFY: "clarification",
            TurnExecutionMode.QUERY: "analysis",
            TurnExecutionMode.SKILL: "workflow",
            TurnExecutionMode.TASK: "workflow",
            TurnExecutionMode.ACTION: "action",
        }[decision.mode]
        return IntentDecision(
            intent=legacy_intent,
            confidence=decision.confidence,
            reason=decision.reason,
            missing_field=decision.missing_field,
            clarifying_question=decision.clarifying_question,
            suggested_expert_codes=_experts_for_route(decision),
            requires_account_context=decision.requires_account_context,
            route_decision=decision,
        )

    async def decide_next(
        self,
        session: AsyncSession | None,
        org_id: int,
        goal: str,
        observations: list[dict[str, Any]],
        available_experts: Sequence[str | dict[str, Any]],
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
            messages = [
                {
                    "role": "system",
                    "content": with_operations_brain_public_identity(prompt.content),
                },
                {
                    "role": "user",
                    "content": (
                        f"目标：{goal}\n"
                        f"当前轮次：{round_index}\n"
                        f"专家观察：{observations}"
                    ),
                },
            ]
            result, _cost = await _structured_chat(
                session,
                org_id,
                prompt,
                messages,
            )
        except Exception as exc:  # noqa: BLE001 - provider failures become a safe domain error
            raise IntelligenceUnavailable("运营大脑暂时不可用，请稍后重试") from exc

        try:
            step = RuntimeNextStep.model_validate(extract_json(result.content))
        except (ValidationError, ValueError, TypeError, KeyError) as first_exc:
            repair_instruction = (
                "上一次输出未通过 runtime-next-step/v1 结构化校验："
                f"{str(first_exc)[:600]}。"
                "请严格遵循系统消息中的字段、枚举和条件约束，"
                "仅输出修正后的唯一 JSON 对象，不要解释，不要使用 Markdown。"
            )
            try:
                repaired_result, _cost = await _structured_chat(
                    session,
                    org_id,
                    prompt,
                    [
                        *messages,
                        {
                            "role": "assistant",
                            "content": str(result.content)[:8000],
                        },
                        {"role": "user", "content": repair_instruction},
                    ],
                )
                step = RuntimeNextStep.model_validate(
                    extract_json(repaired_result.content)
                )
            except (ValidationError, ValueError, TypeError, KeyError) as exc:
                raise IntelligenceUnavailable(
                    "运营大脑暂时无法决定可靠的下一步"
                ) from exc
            except Exception as exc:  # noqa: BLE001 - provider failures become a safe domain error
                raise IntelligenceUnavailable("运营大脑暂时不可用，请稍后重试") from exc

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
                    {
                        "role": "system",
                        "content": with_operations_brain_public_identity(prompt.content),
                    },
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
            raise IntelligenceUnavailable("运营大脑暂时无法可靠地重整方案") from exc
        except Exception as exc:  # noqa: BLE001 - provider failures become a safe domain error
            raise IntelligenceUnavailable("运营大脑暂时不可用，请稍后重试") from exc

        return revised.model_copy(update={"status": "pending"})

    async def review_expert_output(
        self,
        session: AsyncSession,
        org_id: int,
        *,
        goal: str,
        expert_code: str,
        expert_name: str,
        deliverable: dict[str, Any],
        situation: dict[str, Any],
        strategy: dict[str, Any],
        evidence_refs: list[dict[str, Any]],
        iteration: int,
    ) -> CriticModelReview:
        """Evaluate one specialist deliverable without allowing model-owned gates."""

        prompt = prompt_registry.load("main-agent.critic")
        payload = {
            "goal": goal,
            "expert": {"code": expert_code, "name": expert_name},
            "iteration": iteration,
            "situation": situation,
            "strategy": strategy,
            "evidence_refs": evidence_refs,
            "deliverable": deliverable,
        }
        try:
            primary_model, _fallback_model = await gateway.resolve_models(
                session,
                org_id,
                AgentCode.DECISION.value,
            )
            result, _cost = await _structured_chat(
                session,
                org_id,
                prompt,
                [
                    {
                        "role": "system",
                        "content": with_operations_brain_public_identity(prompt.content),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False, default=str),
                    },
                ],
            )
            evaluation = CriticEvaluation.model_validate(extract_json(result.content))
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            raise IntelligenceUnavailable("质量审核结果不符合结构化契约") from exc
        except Exception as exc:  # noqa: BLE001 - provider failures require human takeover
            raise IntelligenceUnavailable("质量审核模型暂时不可用") from exc
        return CriticModelReview(
            evaluation=evaluation,
            prompt=prompt,
            model=primary_model,
        )

    async def plan_operating_strategy(
        self,
        session: AsyncSession,
        org_id: int,
        *,
        goal: str,
        situation: dict[str, Any],
        evidence_refs: list[dict[str, Any]],
        suggested_expert_codes: list[str],
        memory_context: dict[str, Any],
    ) -> OperatingStrategyModelPlan:
        """Build a strategy draft; persistence code enforces every evidence reference."""

        prompt = prompt_registry.load("main-agent.strategy-planning")
        payload = {
            "goal": goal,
            "situation": situation,
            "evidence_refs": [
                {
                    **item,
                    "evidence_id": (
                        f"{item.get('source_type')}:{item.get('source_id')}:"
                        f"{item.get('metric')}"
                    ),
                }
                for item in evidence_refs
            ],
            "suggested_expert_codes": suggested_expert_codes,
            "memory_context": memory_context,
        }
        try:
            result, _cost = await _structured_chat(
                session,
                org_id,
                prompt,
                [
                    {
                        "role": "system",
                        "content": with_operations_brain_public_identity(prompt.content),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False, default=str),
                    },
                ],
            )
            return OperatingStrategyModelPlan(
                draft=OperatingStrategyDraft.model_validate(
                    extract_json(result.content)
                ),
                prompt=prompt,
                model=result.model,
            )
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            raise IntelligenceUnavailable("运营策略结果不符合结构化契约") from exc
        except Exception as exc:  # noqa: BLE001 - provider failures use safe fallback
            raise IntelligenceUnavailable("运营策略模型暂时不可用") from exc


def _normalize_casual_message(message: str) -> str:
    return re.sub(r"[\s，。！？!?、,.]+", "", message).lower()


async def _structured_chat(
    session: AsyncSession | None,
    org_id: int,
    prompt: LoadedPrompt,
    messages: list[dict],
):
    if session is None:
        raise RuntimeError("brain intelligence requires an active database session")
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
    available: Sequence[str | dict[str, Any]],
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


def _experts_for_route(decision: TurnRouteDecision) -> list[AgentCode]:
    if decision.mode is not TurnExecutionMode.SKILL:
        return []
    normalized = (decision.skill_code or "").strip().lower().replace("_", "-")
    if normalized in {
        "account-positioning",
        "account-positioning-diagnosis",
        "positioning",
        "positioning-diagnosis",
    } or "positioning" in normalized:
        return [AgentCode.POSITIONING]
    return []
