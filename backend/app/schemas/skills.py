from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

_SKILL_CODE_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")


@dataclass(frozen=True)
class SkillDefinition:
    code: str
    version: int
    name: str
    description: str
    supported_platforms: frozenset[str]
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    expert_codes: tuple[str, ...]
    tool_codes: tuple[str, ...]
    risk_level: Literal["low", "medium", "high"]
    approval_policy: Literal["none", "before_tools", "before_finish"]
    artifact_type: str | None

    def __post_init__(self) -> None:
        if not _SKILL_CODE_PATTERN.fullmatch(self.code):
            raise ValueError("Skill code must be a stable snake_case string")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("Skill version must be a positive integer")

        object.__setattr__(self, "supported_platforms", frozenset(self.supported_platforms))
        object.__setattr__(self, "expert_codes", tuple(self.expert_codes))
        object.__setattr__(self, "tool_codes", tuple(self.tool_codes))


class SkillCatalogItem(BaseModel):
    """Stable external representation of a business skill."""

    model_config = ConfigDict(frozen=True)

    code: str
    version: int
    name: str
    description: str
    supported_platforms: list[str]
    expert_codes: list[str]
    tool_codes: list[str]
    risk_level: Literal["low", "medium", "high"]
    approval_policy: Literal["none", "before_tools", "before_finish"]
    artifact_type: str | None

    @classmethod
    def from_definition(cls, definition: SkillDefinition) -> SkillCatalogItem:
        return cls(
            code=definition.code,
            version=definition.version,
            name=definition.name,
            description=definition.description,
            supported_platforms=sorted(definition.supported_platforms),
            expert_codes=list(definition.expert_codes),
            tool_codes=list(definition.tool_codes),
            risk_level=definition.risk_level,
            approval_policy=definition.approval_policy,
            artifact_type=definition.artifact_type,
        )
