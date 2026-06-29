"""LLM 网关相关 schema。"""

from pydantic import BaseModel


class PingRequest(BaseModel):
    prompt: str
    agent_code: str = "test"


class UsageOut(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class PingResponse(BaseModel):
    model: str
    content: str
    usage: UsageOut
    cost_usd: float
