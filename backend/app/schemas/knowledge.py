"""共享知识库 schema：爆款库 / 用户画像 / 提示词库 / 话术库条目。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import KnowledgeCategory


class CreateKnowledgeRequest(BaseModel):
    category: KnowledgeCategory
    title: str = Field(min_length=1, max_length=300)
    payload: dict = Field(default_factory=dict)
    tags: list[str] | None = None


class UpdateKnowledgeRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    payload: dict | None = None
    tags: list[str] | None = None


class KnowledgeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category: KnowledgeCategory
    title: str
    payload: dict
    tags: list[str] | None
    created_at: datetime
