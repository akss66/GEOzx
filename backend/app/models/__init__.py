"""ORM 模型包。

导入全部模型，使 `app.db.Base.metadata` 完整（Alembic autogenerate 依赖于此）。
"""

from app.models.account_data import (
    AccountMetricSnapshot,
    AudienceProfileItem,
    AudienceProfileSnapshot,
    BenchmarkSnapshot,
    DataArtifact,
    DataConflict,
    DataImportBatch,
    DataImportRow,
    PlatformContentRecord,
)
from app.models.agent_runtime import AgentRun
from app.models.ai_coo import (
    AgentQualityScore,
    DecisionTrace,
    ExperienceMemory,
    ReflectionRecord,
    StrategyPlan,
)
from app.models.brain import (
    AgentInvocation,
    AgentToolCall,
    AutomationPolicy,
    BrainTask,
    DeliverableAcceptance,
    OrchestrationPlan,
    TaskBrief,
    ToolExecutionAttempt,
)
from app.models.client import (
    AccountClient,
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
from app.models.conversation import ConversationThread, ConversationTurn
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
from app.models.memory import RuntimeMemory
from app.models.metrics import AccountReviewGoal, MetricSnapshot
from app.models.orchestration import AgentTask, Event, GateApproval
from app.models.platform import PlatformAccountAuth, PlatformIntegration
from app.models.publishing import PlatformPublishJob
from app.models.skill_runtime import SkillRun
from app.models.workspace import Account, AccountGroup, Project

__all__ = [
    "DataImportBatch",
    "DataArtifact",
    "DataImportRow",
    "PlatformContentRecord",
    "AccountMetricSnapshot",
    "AudienceProfileSnapshot",
    "AudienceProfileItem",
    "BenchmarkSnapshot",
    "DataConflict",
    "Org",
    "User",
    "AdminSecurityCredential",
    "UserDeletionPreviewReservation",
    "Client",
    "ClientMembership",
    "AccountClient",
    "ProjectMembership",
    "ProjectAccount",
    "AccountMembership",
    "Notification",
    "Project",
    "AccountGroup",
    "Account",
    "ContentItem",
    "Deliverable",
    "ConversationThread",
    "ConversationTurn",
    "MatrixDistributionPlan",
    "MatrixDistributionItem",
    "BrainTask",
    "AgentRun",
    "SkillRun",
    "StrategyPlan",
    "DecisionTrace",
    "ExperienceMemory",
    "ReflectionRecord",
    "AgentQualityScore",
    "TaskBrief",
    "OrchestrationPlan",
    "AgentInvocation",
    "AgentToolCall",
    "ToolExecutionAttempt",
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
    "RuntimeMemory",
    "OptimizationSuggestion",
    "PlatformIntegration",
    "PlatformAccountAuth",
    "PlatformPublishJob",
]
