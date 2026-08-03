from dataclasses import replace

import pytest

from app.orchestrator.skill_tool_plan import SkillToolPlanError, build_skill_tool_plan
from app.orchestrator.skills.operating_tasks import PUBLISHING_PREPARATION_SKILL


def test_builds_prepare_phase_for_publish_package_tool() -> None:
    plan = build_skill_tool_plan(PUBLISHING_PREPARATION_SKILL)

    assert [(step.tool_code, step.phase) for step in plan] == [
        ("publish_package_prepare", "prepare")
    ]


def test_rejects_unregistered_declared_tool() -> None:
    definition = replace(
        PUBLISHING_PREPARATION_SKILL,
        code="invalid_tool_skill",
        tool_codes=("missing.tool",),
    )

    with pytest.raises(SkillToolPlanError, match="SKILL_TOOL_UNREGISTERED:missing.tool"):
        build_skill_tool_plan(definition)
