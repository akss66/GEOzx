from __future__ import annotations

from collections.abc import Iterable

from app.schemas.skills import SkillCatalogItem, SkillDefinition, validate_skill_version


class SkillRegistry:
    def __init__(self, definitions: Iterable[SkillDefinition]) -> None:
        self._definitions: dict[tuple[str, int], SkillDefinition] = {}
        for definition in definitions:
            key = (definition.code, definition.version)
            if key in self._definitions:
                raise ValueError(f"duplicate Skill definition: {key}")
            self._definitions[key] = definition

    def get(self, code: str, version: int | None = None) -> SkillDefinition:
        if version is not None:
            validate_skill_version(version)
            try:
                return self._definitions[(code, version)]
            except KeyError as error:
                raise KeyError(code) from error

        matches = [
            definition
            for (definition_code, _), definition in self._definitions.items()
            if definition_code == code
        ]
        if not matches:
            raise KeyError(code)
        return max(matches, key=lambda definition: definition.version)

    def list_for(self, platform: str) -> list[SkillCatalogItem]:
        latest_by_code = {
            definition.code: self.get(definition.code)
            for definition in self._definitions.values()
        }
        compatible = [
            SkillCatalogItem.from_definition(definition)
            for definition in latest_by_code.values()
            if platform in definition.supported_platforms
        ]
        return sorted(compatible, key=lambda item: (item.name, item.code))


from app.orchestrator.skills.account_inspection import (  # noqa: E402
    ACCOUNT_INSPECTION_SKILL,
)
from app.orchestrator.skills.account_positioning import (  # noqa: E402
    ACCOUNT_POSITIONING_SKILL,
)
from app.orchestrator.skills.operating_tasks import (  # noqa: E402
    PERFORMANCE_REVIEW_SKILL,
    PUBLISHING_PREPARATION_SKILL,
    SCRIPT_GENERATION_SKILL,
    TOPIC_PLANNING_SKILL,
)

skill_registry = SkillRegistry(
    [
        ACCOUNT_INSPECTION_SKILL,
        ACCOUNT_POSITIONING_SKILL,
        TOPIC_PLANNING_SKILL,
        SCRIPT_GENERATION_SKILL,
        PUBLISHING_PREPARATION_SKILL,
        PERFORMANCE_REVIEW_SKILL,
    ]
)

__all__ = ["SkillRegistry", "skill_registry"]
