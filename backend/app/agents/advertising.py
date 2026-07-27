"""07 投流专家，使用 Prompt Registry `experts/07-advertiser/v1.md`。"""

from app.agents.base import LLMAgent
from app.models.enums import DeliverableType


class AdvertisingAgent(LLMAgent):
    code = "07-advertiser"
    output_type = DeliverableType.AD_PLAN
    prompt_name = "07-advertiser"
