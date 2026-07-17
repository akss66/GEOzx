"""08 客服反馈专家。"""

from app.agents.base import LLMAgent
from app.models.enums import DeliverableType


class CustomerServiceAgent(LLMAgent):
    code = "08-customer-service"
    output_type = DeliverableType.CS_RECORD
    prompt_name = "08-customer-service"
