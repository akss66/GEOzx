"""07 投流专家。"""

from app.agents.base import LLMAgent
from app.models.enums import DeliverableType


class AdvertisingAgent(LLMAgent):
    code = "07-advertiser"
    output_type = DeliverableType.AD_PLAN
    prompt_name = "07-advertiser"
