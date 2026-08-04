"""Single source of truth for executable deliverable actions."""

from dataclasses import dataclass

from app.models.enums import WorkspaceRole
from app.schemas.artifacts import ArtifactStatus, DeliverableActionCode


@dataclass(frozen=True)
class DeliverableActionDefinition:
    code: DeliverableActionCode
    label: str
    requires_confirmation: bool
    artifact_types: frozenset[str]
    statuses: frozenset[ArtifactStatus]
    roles: frozenset[WorkspaceRole]


ACTIONABLE_STATUSES = frozenset({"ready_for_review", "accepted"})

SERVER_ACTIONS: dict[str, DeliverableActionDefinition] = {
    "create_shoot_task": DeliverableActionDefinition(
        code="create_shoot_task",
        label="创建拍摄任务",
        requires_confirmation=True,
        artifact_types=frozenset({"video_script"}),
        statuses=ACTIONABLE_STATUSES,
        roles=frozenset(
            {WorkspaceRole.LEAD, WorkspaceRole.OPERATOR, WorkspaceRole.EDITOR}
        ),
    ),
    "add_to_schedule": DeliverableActionDefinition(
        code="add_to_schedule",
        label="加入内容排期",
        requires_confirmation=True,
        artifact_types=frozenset({"publish_calendar", "content_calendar"}),
        statuses=ACTIONABLE_STATUSES,
        roles=frozenset({WorkspaceRole.LEAD, WorkspaceRole.OPERATOR}),
    ),
}


def server_action_for(
    action_code: str,
    *,
    artifact_type: str,
    artifact_status: ArtifactStatus,
) -> DeliverableActionDefinition | None:
    definition = SERVER_ACTIONS.get(action_code)
    if (
        definition is None
        or artifact_type not in definition.artifact_types
        or artifact_status not in definition.statuses
    ):
        return None
    return definition
