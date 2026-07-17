"""Server-side intent and next-step intelligence for the operations brain."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import extract_json
from app.llm.gateway import gateway, reset_stream_observer, set_stream_observer
from app.models.enums import AgentCode
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
            result, _cost = await gateway.chat(
                session,
                org_id,
                AgentCode.DECISION.value,
                [
                    {"role": "system", "content": _intent_system_prompt()},
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
        allowed = [str(item["code"]) for item in capabilities]
        try:
            token = set_stream_observer(None)
            try:
                result, _cost = await gateway.chat(
                    session,
                    org_id,
                    AgentCode.DECISION.value,
                    [
                        {"role": "system", "content": _next_step_system_prompt(capabilities)},
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
            finally:
                reset_stream_observer(token)
            step = RuntimeNextStep.model_validate(extract_json(result.content))
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            raise IntelligenceUnavailable("主 Agent 暂时无法决定可靠的下一步") from exc
        except Exception as exc:  # noqa: BLE001 - provider failures become a safe domain error
            raise IntelligenceUnavailable("主 Agent 暂时不可用，请稍后重试") from exc

        filtered = [code for code in step.expert_codes if code.value in allowed]
        return step.model_copy(update={"expert_codes": filtered})

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
            result, _cost = await gateway.chat(
                session,
                org_id,
                AgentCode.DECISION.value,
                [
                    {"role": "system", "content": _decision_revision_system_prompt()},
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


def _intent_system_prompt() -> str:
    return """你是同舟行的主 Agent 意图路由器。
判断用户是在普通交流、需要补充信息、低风险分析、正式工作流，还是请求外部动作。

只输出 JSON：
{"intent":"workflow","confidence":0.9,"reason":"一句原因","missing_field":null,"clarifying_question":null,"suggested_expert_codes":["01-positioning"],"requires_account_context":true}

可用专家 code：01-positioning、02-content-director、03-art-director、04-video-creator、
05-editor、06-operator、07-advertiser、08-customer-service。

规则：
1. 问候、解释和简单问答使用 conversation，不调用专家。
2. 缺少一个会改变执行方向的关键信息时使用 clarification，只问一个问题，不调用专家。
3. 只有确有必要时才选择专家，不固定调用完整链路。
4. 涉及账号数据、定位、内容表现、发布或复盘时 requires_account_context=true。
5. 不得发明专家 code。"""


def _sanitize_capabilities(
    available: list[str | dict[str, Any]],
) -> list[dict[str, Any]]:
    capabilities: list[dict[str, Any]] = []
    for item in available:
        capability = {"kind": "expert", "code": item} if isinstance(item, str) else dict(item)
        code = str(capability.get("code") or "")
        if code not in _AVAILABLE_EXPERTS:
            continue
        capability["code"] = code
        capabilities.append(capability)
    return capabilities


def _next_step_system_prompt(available_experts: list[dict[str, Any]]) -> str:
    catalog = json.dumps(available_experts, ensure_ascii=False, default=str)
    return f"""你是同舟行运营大脑的主 Agent。你已经收到一轮专家结论，现在决定唯一下一步。

可选 action：respond、ask_user、dispatch_experts、request_decision、request_permission、finish。
当前能力注册表：{catalog or '[]'}。

只输出 JSON：
{{"action":"finish","expert_codes":[],"rationale":"一句原因","handoff_message":"给用户看的自然语言过渡","decision_request":null}}

规则：
1. 先观察已有结论，足够回答时直接 finish。
2. 只有新的专家能补足明确缺口时才 dispatch_experts，单轮最多 3 位。
3. 存在 2-4 个合理且互斥的业务方向时使用 request_decision，并提供完整 decision_request。
4. 不得重复调用已经完成且没有新输入的专家。
5. 不输出思维链、模型名或技术日志。"""


def _decision_revision_system_prompt() -> str:
    return """你是同舟行运营大脑的主 Agent。
用户正在修改一组业务方案，请根据原任务、原方案和修改意见重新生成可选择的方案。

只输出 DecisionRequest JSON：
{"id":"model-generated","title":"要用户决定的问题","summary":"为什么现在需要选择","choices":[{"id":"choice-a","title":"方案名称","description":"方案做法","benefit":"主要收益","tradeoff":"主要代价","recommended":true},{"id":"choice-b","title":"方案名称","description":"方案做法","benefit":"主要收益","tradeoff":"主要代价","recommended":false}],"allow_custom_input":true,"status":"pending"}

规则：
1. 提供 2-4 个互斥、可执行且差异明确的方案。
2. 用户要求换一批时，不得原样重复旧方案。
3. 最多标记一个推荐方案，推荐依据必须体现在 summary 或方案描述中。
4. 不输出思维链、Markdown 或 JSON 以外的文字。"""


brain_intelligence = BrainIntelligence()
