"""Agent 运行时基类。

每个 Agent = system prompt（M1 接入）+ 绑定模型 + 工具集 + 输入/输出 schema。
T7 仅定义基类与上下文；真实创作 Agent 在 M1 落地。
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel

from app.models.enums import DeliverableType
from app.schemas.deliverable import DeliverablePayload


class AgentContext(BaseModel):
    """Agent 执行上下文：上游交付物（按 type→payload）+ 知识库切片（M1 接入）。"""

    content_item_id: int
    upstream: dict[str, dict] = {}
    knowledge: dict[str, list[dict]] = {}


class BaseAgent(ABC):
    """所有 Agent 的基类。"""

    code: str
    output_type: DeliverableType

    @abstractmethod
    async def run(self, ctx: AgentContext) -> DeliverablePayload:
        """执行一次工作，产出经 schema 校验的交付物 payload。"""
        ...
