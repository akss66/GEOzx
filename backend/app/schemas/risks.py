"""风险队列 schema：跨模块统一风险行。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

RiskCategory = Literal["quality_gate", "account_auth", "model_failure", "data_sync"]
RiskSeverity = Literal["low", "medium", "high"]


class RiskQueueItem(BaseModel):
    id: str
    category: RiskCategory
    severity: RiskSeverity
    title: str
    description: str
    source: str
    status: str
    created_at: datetime
