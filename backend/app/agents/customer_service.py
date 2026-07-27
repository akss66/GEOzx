"""08 客服反馈专家，使用 Prompt Registry `experts/08-customer-service/v1.md`。"""

from app.agents.base import LLMAgent
from app.models.enums import DeliverableType


class CustomerServiceAgent(LLMAgent):
    code = "08-customer-service"
    output_type = DeliverableType.CS_RECORD
    prompt_name = "08-customer-service"
