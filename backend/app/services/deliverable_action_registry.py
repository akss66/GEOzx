"""Single source of truth for executable deliverable actions."""

from dataclasses import dataclass

from pydantic import BaseModel

from app.models.enums import DeliverableType, WorkspaceRole
from app.schemas.artifacts import ArtifactStatus, DeliverableActionCode
from app.schemas.deliverable import get_schema
from app.schemas.deliverable_actions import (
    AddToScheduleActionRequest,
    CreateShootTaskActionRequest,
    GenerateNextIterationActionRequest,
    RequestRevisionActionRequest,
)


@dataclass(frozen=True)
class DeliverableActionDefinition:
    code: DeliverableActionCode
    label: str
    requires_confirmation: bool
    artifact_types: frozenset[str] | None
    deliverable_types: frozenset[DeliverableType]
    statuses: frozenset[ArtifactStatus]
    roles: frozenset[WorkspaceRole]
    request_model: type[BaseModel]
    requires_thread: bool = False


ACTIONABLE_STATUSES: frozenset[ArtifactStatus] = frozenset(
    {"ready_for_review", "accepted"}
)
REVISION_DELIVERABLE_TYPES = frozenset(
    item for item in DeliverableType if get_schema(item) is not None
)
REVISION_ARTIFACT_TYPES = frozenset(
    {
        "account_inspection_report",
        "account_positioning",
        "positioning_strategy",
        "topic_plan",
        "video_script",
        "visual_brief",
        "art_prompt",
        "video_asset",
        "edited_video",
        "content_calendar",
        "publish_calendar",
        "publish_package",
        "platform_publish_receipt",
        "review_report",
        "engagement_review",
        "ad_plan",
        "cs_record",
        "operation_execution_plan",
    }
)

SERVER_ACTIONS: dict[str, DeliverableActionDefinition] = {
    "create_shoot_task": DeliverableActionDefinition(
        code="create_shoot_task",
        label="创建拍摄任务",
        requires_confirmation=True,
        artifact_types=frozenset({"video_script"}),
        deliverable_types=frozenset({DeliverableType.VIDEO_SCRIPT}),
        statuses=ACTIONABLE_STATUSES,
        roles=frozenset(
            {WorkspaceRole.LEAD, WorkspaceRole.OPERATOR, WorkspaceRole.EDITOR}
        ),
        request_model=CreateShootTaskActionRequest,
    ),
    "add_to_schedule": DeliverableActionDefinition(
        code="add_to_schedule",
        label="加入内容排期",
        requires_confirmation=True,
        artifact_types=frozenset({"publish_calendar", "content_calendar"}),
        deliverable_types=frozenset({DeliverableType.PUBLISH_CALENDAR}),
        statuses=ACTIONABLE_STATUSES,
        roles=frozenset({WorkspaceRole.LEAD, WorkspaceRole.OPERATOR}),
        request_model=AddToScheduleActionRequest,
    ),
    "request_revision": DeliverableActionDefinition(
        code="request_revision",
        label="保存修改版本",
        requires_confirmation=False,
        artifact_types=REVISION_ARTIFACT_TYPES,
        deliverable_types=REVISION_DELIVERABLE_TYPES,
        statuses=ACTIONABLE_STATUSES,
        roles=frozenset(
            {WorkspaceRole.LEAD, WorkspaceRole.OPERATOR, WorkspaceRole.EDITOR}
        ),
        request_model=RequestRevisionActionRequest,
    ),
    "generate_next_iteration": DeliverableActionDefinition(
        code="generate_next_iteration",
        label="生成下一轮优化方案",
        requires_confirmation=False,
        artifact_types=frozenset(
            {"review_report", "account_inspection_report", "engagement_review"}
        ),
        deliverable_types=frozenset({DeliverableType.REVIEW_REPORT}),
        statuses=frozenset({"accepted"}),
        roles=frozenset(
            {WorkspaceRole.LEAD, WorkspaceRole.OPERATOR, WorkspaceRole.EDITOR}
        ),
        request_model=GenerateNextIterationActionRequest,
        requires_thread=True,
    ),
}


def server_action_for(
    action_code: str,
    *,
    artifact_type: str,
    deliverable_type: DeliverableType,
    artifact_status: ArtifactStatus,
) -> DeliverableActionDefinition | None:
    definition = SERVER_ACTIONS.get(action_code)
    if (
        definition is None
        or (
            definition.artifact_types is not None
            and artifact_type not in definition.artifact_types
        )
        or deliverable_type not in definition.deliverable_types
        or artifact_status not in definition.statuses
    ):
        return None
    return definition
