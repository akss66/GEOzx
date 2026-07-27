"""Canonical specialist registry used by direct and orchestrated execution."""

from dataclasses import dataclass

from app.agents.advertising import AdvertisingAgent
from app.agents.art import ArtAgent
from app.agents.base import BaseAgent
from app.agents.content import ContentAgent
from app.agents.customer_service import CustomerServiceAgent
from app.agents.editing import EditingAgent
from app.agents.operation import OperationAgent
from app.agents.positioning import PositioningAgent
from app.agents.video import VideoAgent
from app.models.enums import (
    AgentCode,
    BrainTaskType,
    ContentStage,
    DeliverableType,
)


@dataclass(frozen=True)
class AgentSpec:
    name: str
    runner: type[BaseAgent]
    deliverable_type: DeliverableType
    deliverable_title: str
    stage: ContentStage
    task_type: BrainTaskType


AGENT_SPECS: dict[AgentCode, AgentSpec] = {
    AgentCode.POSITIONING: AgentSpec(
        "账号定位专家",
        PositioningAgent,
        DeliverableType.POSITIONING_STRATEGY,
        "账号定位方案",
        ContentStage.POSITIONING,
        BrainTaskType.ACCOUNT_DIAGNOSIS,
    ),
    AgentCode.CONTENT_DIRECTOR: AgentSpec(
        "编导文案专家",
        ContentAgent,
        DeliverableType.VIDEO_SCRIPT,
        "视频脚本",
        ContentStage.CONTENT_DIRECTION,
        BrainTaskType.CONTENT_CREATION,
    ),
    AgentCode.ART_DIRECTOR: AgentSpec(
        "美术提示词专家",
        ArtAgent,
        DeliverableType.ART_PROMPT,
        "视觉提示方案",
        ContentStage.ART_DIRECTION,
        BrainTaskType.CONTENT_CREATION,
    ),
    AgentCode.VIDEO_CREATOR: AgentSpec(
        "视频创作专家",
        VideoAgent,
        DeliverableType.VIDEO_ASSET,
        "视频素材方案",
        ContentStage.VIDEO_CREATION,
        BrainTaskType.CONTENT_CREATION,
    ),
    AgentCode.EDITOR: AgentSpec(
        "剪辑专家",
        EditingAgent,
        DeliverableType.EDITED_VIDEO,
        "剪辑成片方案",
        ContentStage.EDITING,
        BrainTaskType.CONTENT_CREATION,
    ),
    AgentCode.OPERATOR: AgentSpec(
        "账号运营专家",
        OperationAgent,
        DeliverableType.REVIEW_REPORT,
        "运营复盘报告",
        ContentStage.OPERATION,
        BrainTaskType.REVIEW_OPTIMIZATION,
    ),
    AgentCode.ADVERTISER: AgentSpec(
        "投流专家",
        AdvertisingAgent,
        DeliverableType.AD_PLAN,
        "投流方案",
        ContentStage.ADVERTISING,
        BrainTaskType.REVIEW_OPTIMIZATION,
    ),
    AgentCode.CUSTOMER_SERVICE: AgentSpec(
        "客服反馈专家",
        CustomerServiceAgent,
        DeliverableType.CS_RECORD,
        "用户反馈报告",
        ContentStage.CUSTOMER_SERVICE,
        BrainTaskType.REVIEW_OPTIMIZATION,
    ),
}


def get_agent_spec(code: AgentCode) -> AgentSpec:
    try:
        return AGENT_SPECS[code]
    except KeyError as exc:
        raise ValueError(f"Unsupported specialist: {code.value}") from exc
