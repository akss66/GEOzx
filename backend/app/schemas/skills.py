from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

_SKILL_CODE_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")


def validate_skill_version(version: object) -> int:
    if type(version) is not int or version < 1:
        raise ValueError("Skill version must be a positive integer")
    return version


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
    approval_policy: Literal[
        "none",
        "before_tools",
        "before_finish",
        "explicit_before_external_write",
    ]
    artifact_type: str | None
    expert_stages: tuple[tuple[str, ...], ...] = ()
    critic_policy: Literal["none", "required"] = "none"
    checkpoint_graph_key: str | None = None
    checkpoint_graph_version: str | None = None

    def __post_init__(self) -> None:
        if not _SKILL_CODE_PATTERN.fullmatch(self.code):
            raise ValueError("Skill code must be a stable snake_case string")
        validate_skill_version(self.version)

        object.__setattr__(self, "supported_platforms", frozenset(self.supported_platforms))
        object.__setattr__(self, "expert_codes", tuple(self.expert_codes))
        object.__setattr__(self, "tool_codes", tuple(self.tool_codes))
        stages = tuple(tuple(stage) for stage in self.expert_stages)
        if not stages and self.expert_codes:
            stages = (tuple(self.expert_codes),)
        flattened = tuple(code for stage in stages for code in stage)
        if flattened != self.expert_codes:
            raise ValueError("expert_stages must flatten to expert_codes in definition order")
        object.__setattr__(self, "expert_stages", stages)
        from app.orchestrator.checkpoint_graph_contracts import get_checkpoint_graph_contract

        contract = get_checkpoint_graph_contract(self.code, self.version)
        if contract is not None:
            object.__setattr__(self, "checkpoint_graph_key", contract.skill_code)
            object.__setattr__(self, "checkpoint_graph_version", contract.graph_version)


class SkillCatalogItem(BaseModel):
    """Stable external representation of a business skill."""

    model_config = ConfigDict(frozen=True)

    code: str
    version: int
    name: str
    description: str
    supported_platforms: list[str]
    expert_codes: list[str]
    expert_stages: list[list[str]]
    tool_codes: list[str]
    critic_policy: Literal["none", "required"]
    risk_level: Literal["low", "medium", "high"]
    approval_policy: Literal[
        "none",
        "before_tools",
        "before_finish",
        "explicit_before_external_write",
    ]
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
            expert_stages=[list(stage) for stage in definition.expert_stages],
            tool_codes=list(definition.tool_codes),
            critic_policy=definition.critic_policy,
            risk_level=definition.risk_level,
            approval_policy=definition.approval_policy,
            artifact_type=definition.artifact_type,
        )


PublicSkillCategory = Literal["quick_operations", "context", "expert_help"]
SkillCatalogSurface = Literal["composer", "artifact_center", "expert_panel"]
CapabilityAvailability = Literal[
    "available",
    "needs_input",
    "needs_connection",
    "coming_soon",
]
CapabilityRequiredContext = Literal[
    "account",
    "account_data",
    "platform_connection",
    "confirmed_artifact",
]


class PublicSkillCatalogItem(BaseModel):
    """Strict business-only Skill metadata safe for user-facing surfaces."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    version: int
    name: str
    description: str
    category: PublicSkillCategory
    icon: str
    requires_account: bool
    availability: CapabilityAvailability
    reason: str | None = None
    required_context: list[CapabilityRequiredContext]
    is_available: bool
    unavailable_reason: str | None = None


class PublicSkillCatalogOut(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    data: list[PublicSkillCatalogItem]
