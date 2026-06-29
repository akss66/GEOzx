"""DummyAgent：产出固定的、经 schema 校验的占位交付物。

用于 T7 验证编排链路骨架；M1 用真实创作 Agent（绑定 prompt+模型）替换。
"""

from app.agents.base import AgentContext, BaseAgent
from app.models.enums import DeliverableType
from app.schemas.deliverable import DeliverablePayload, validate_payload


class DummyAgent(BaseAgent):
    def __init__(self, code: str, output_type: DeliverableType, payload: dict) -> None:
        self.code = code
        self.output_type = output_type
        self._payload = payload

    async def run(self, ctx: AgentContext) -> DeliverablePayload:
        # 经对应 type 的 Pydantic schema 校验，确保结构正确
        return validate_payload(self.output_type, self._payload)
