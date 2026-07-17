"""Agent 运行时基类。

每个 Agent = system prompt + 绑定模型（经网关按 ModelConfig 路由）+ 输入/输出 schema。
- `BaseAgent`：抽象基类，`run(session, org_id, ctx)` 产出经 schema 校验的交付物 payload。
- `LLMAgent`：调 LLMGateway 的通用实现——组装 messages → chat → 抽取 JSON → 校验 → 失败重试。
真实创作 Agent（M1 E2）继承 LLMAgent，仅声明 code/output_type/prompt 文件名。
"""

import json
import re
from abc import ABC, abstractmethod

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.prompts import load_prompt
from app.llm.gateway import LLMGateway, gateway
from app.models.enums import DeliverableType
from app.schemas.deliverable import DeliverablePayload, validate_payload
from app.services.agent_management import get_business_config


class AgentContext(BaseModel):
    """Agent 执行上下文：上游交付物、知识库切片、已采纳优化建议。"""

    content_item_id: int
    request: str | None = None
    upstream: dict[str, dict] = {}
    knowledge: dict[str, list[dict]] = {}
    optimization_suggestions: list[dict] = []


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


# 从模型输出中抽取 JSON：优先 ```json 围栏，其次首个 {...} 块，最后整体解析。
_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_BRACE_RE = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str) -> dict:
    """容错抽取 JSON 对象。失败抛 ValueError。"""
    for pattern in (_FENCE_RE, _BRACE_RE):
        m = pattern.search(text)
        if m:
            try:
                return json.loads(m.group(1) if pattern is _FENCE_RE else m.group(0))
            except json.JSONDecodeError:
                continue
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"模型输出无法解析为 JSON：{text[:200]}") from exc


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

    async def run(
        self, session: AsyncSession, org_id: int | None, ctx: AgentContext
    ) -> DeliverablePayload:
        system_prompt = load_prompt(self.prompt_name)
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
            result, _cost = await self._llm.chat(session, org_id, self.code, messages)
            try:
                data = extract_json(result.content)
                return validate_payload(self.output_type, data)
            except (ValueError, ValidationError) as exc:
                last_err = str(exc)
                messages.append({"role": "assistant", "content": result.content})

        raise ValueError(f"[{self.code}] 输出经 {self.max_retries + 1} 次仍未通过校验：{last_err}")
