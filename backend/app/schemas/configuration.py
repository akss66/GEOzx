"""模型配置 schema：per-Agent 首选/兜底模型。"""

from pydantic import BaseModel, ConfigDict, Field


class ModelConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_code: str
    primary_model: str
    fallback_model: str | None


class UpdateModelConfigRequest(BaseModel):
    primary_model: str | None = Field(default=None, min_length=1, max_length=128)
    fallback_model: str | None = Field(default=None, max_length=128)
