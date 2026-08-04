"""Deterministic child-Skill DAG construction and recovery helpers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class CompositeSkillRuntime:
    """Build a resumable plan; it never produces child expert conclusions."""

    _NODES = (
        ("topic_planning", ()),
        ("script_generation", ("topic_planning",)),
        ("visual_brief_generation", ("script_generation",)),
        ("content_calendar_planning", ("visual_brief_generation",)),
        ("publishing_preparation", ("content_calendar_planning",)),
    )

    def build(
        self,
        *,
        account_id: int,
        cycle_days: int,
        topic_count: int | None = None,
        script_duration_seconds: int | None = None,
        source_artifacts: list[dict[str, Any]],
        previous_graph: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        prior = {
            str(item.get("skill_code")): deepcopy(item)
            for item in (previous_graph or [])
            if isinstance(item, dict)
        }
        nodes: list[dict[str, Any]] = []
        for index, (skill_code, dependencies) in enumerate(self._NODES, start=1):
            previous = prior.get(skill_code, {})
            status = str(previous.get("status") or "pending")
            if status not in {"pending", "running", "completed", "failed", "blocked"}:
                status = "pending"
            nodes.append(
                {
                    "node_id": f"cycle-{index}",
                    "skill_code": skill_code,
                    "status": status,
                    "depends_on": list(dependencies),
                    "artifact_id": previous.get("artifact_id"),
                    "error_code": previous.get("error_code"),
                    "input": self._child_input(
                        skill_code=skill_code,
                        cycle_days=cycle_days,
                        topic_count=topic_count,
                        script_duration_seconds=script_duration_seconds,
                    ),
                }
            )
        return {
            "artifact_type": "operation_execution_plan",
            "account_id": account_id,
            "cycle_days": cycle_days,
            "source_artifacts": deepcopy(source_artifacts),
            "child_skill_graph": nodes,
            "dependencies": [
                {"skill_code": code, "depends_on": list(dependencies)}
                for code, dependencies in self._NODES
                if dependencies
            ],
            "approval_points": [
                {
                    "after_skill": "publishing_preparation",
                    "kind": "publish_package_approval",
                    "required": True,
                }
            ],
            "participating_experts": [],
        }

    @staticmethod
    def _child_input(
        *,
        skill_code: str,
        cycle_days: int,
        topic_count: int | None,
        script_duration_seconds: int | None,
    ) -> dict[str, int]:
        if skill_code == "topic_planning":
            return {
                "days": cycle_days,
                **({"topic_count": topic_count} if topic_count is not None else {}),
            }
        if skill_code == "script_generation" and script_duration_seconds is not None:
            return {"duration_seconds": script_duration_seconds}
        return {}


composite_skill_runtime = CompositeSkillRuntime()

__all__ = ["CompositeSkillRuntime", "composite_skill_runtime"]
