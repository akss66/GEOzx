"""ORM 模型包。

导入全部模型，使 `app.db.Base.metadata` 完整（Alembic autogenerate 依赖于此）。
"""

from app.models.brain import (
    AgentInvocation,
    AgentToolCall,
    AutomationPolicy,
    BrainTask,
    DeliverableAcceptance,
    OrchestrationPlan,
    TaskBrief,
)
from app.models.client import (
    AccountMembership,
    Client,
    ClientMembership,
    Notification,
    ProjectAccount,
    ProjectMembership,
)
from app.models.compliance import ComplianceCheck
from app.models.configuration import IntegrationConfig, ModelConfig, ModelProvider
from app.models.content import ContentItem, Deliverable
from app.models.distribution import MatrixDistributionItem, MatrixDistributionPlan
from app.models.feedback import OptimizationSuggestion
from app.models.identity import (
    AdminSecurityCredential,
    Org,
    User,
    UserDeletionPreviewReservation,
)
from app.models.knowledge import KnowledgeCitation, KnowledgeEntry, KnowledgeSuggestion
from app.models.llm import LLMCall
from app.models.material import MaterialAsset
from app.models.metrics import AccountReviewGoal, MetricSnapshot
from app.models.orchestration import AgentTask, Event, GateApproval
from app.models.platform import PlatformAccountAuth, PlatformIntegration
from app.models.workspace import Account, AccountGroup, Project

__all__ = [
    "Org",
    "User",
    "AdminSecurityCredential",
    "UserDeletionPreviewReservation",
    "Client",
    "ClientMembership",
    "ProjectMembership",
    "ProjectAccount",
    "AccountMembership",
    "Notification",
    "Project",
    "AccountGroup",
    "Account",
    "ContentItem",
    "Deliverable",
    "MatrixDistributionPlan",
    "MatrixDistributionItem",
    "BrainTask",
    "TaskBrief",
    "OrchestrationPlan",
    "AgentInvocation",
    "AgentToolCall",
    "DeliverableAcceptance",
    "AutomationPolicy",
    "AgentTask",
    "Event",
    "GateApproval",
    "KnowledgeEntry",
    "KnowledgeSuggestion",
    "KnowledgeCitation",
    "ModelConfig",
    "ModelProvider",
    "IntegrationConfig",
    "LLMCall",
    "ComplianceCheck",
    "AccountReviewGoal",
    "MetricSnapshot",
    "MaterialAsset",
    "OptimizationSuggestion",
    "PlatformIntegration",
    "PlatformAccountAuth",
]
