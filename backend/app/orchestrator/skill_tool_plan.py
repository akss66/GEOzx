"""Resolve every declared Skill tool into an explicit execution phase."""

from dataclasses import dataclass
from typing import Literal

from app.orchestrator.runtime_tools import runtime_tool_phase
from app.schemas.skills import SkillDefinition

ToolPhase = Literal["read", "prepare", "side_effect"]


class SkillToolPlanError(RuntimeError):
    """Raised when a Skill declares a tool that the runtime cannot execute."""


@dataclass(frozen=True)
class SkillToolStep:
    tool_code: str
    phase: ToolPhase


def build_skill_tool_plan(definition: SkillDefinition) -> tuple[SkillToolStep, ...]:
    steps: list[SkillToolStep] = []
    for tool_code in definition.tool_codes:
        try:
            phase = runtime_tool_phase(tool_code)
        except KeyError as exc:
            raise SkillToolPlanError(f"SKILL_TOOL_UNREGISTERED:{tool_code}") from exc
        steps.append(SkillToolStep(tool_code=tool_code, phase=phase))
    return tuple(steps)


__all__ = ["SkillToolPlanError", "SkillToolStep", "build_skill_tool_plan"]
