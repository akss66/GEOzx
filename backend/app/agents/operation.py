"""06 账号运营专家（真实 LLMAgent）。

system prompt: Prompt Registry `experts/06-operation/v1.md`
输出: ReviewReportPayload ｜ 输入: 上游成片 + 数据。
数据源（真实指标）在 M1 E6/E8 接入；闭环反馈广播在 E10。
"""

from app.agents.base import LLMAgent
from app.models.enums import DeliverableType


class OperationAgent(LLMAgent):
    code = "06-operation"
    output_type = DeliverableType.REVIEW_REPORT
    prompt_name = "06-operation"
