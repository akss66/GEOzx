"""全局枚举：状态机、平台、质量门、交付物类型等。

集中定义，供模型与 schema 共用。用 StrEnum，成员 value 为稳定小写字符串（入库即此值，
由 base.pg_enum 的 values_callable 保证）。新增成员向后兼容。
"""

import enum


class UserRole(enum.StrEnum):
    """v1 两级角色（可扩展）。admin=系统配置/账号/质量门策略/用户管理；user=日常使用。"""

    ADMIN = "admin"
    USER = "user"


class WorkspaceRole(enum.StrEnum):
    LEAD = "lead"
    OPERATOR = "operator"
    EDITOR = "editor"
    REVIEWER = "reviewer"


class ClientStatus(enum.StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class Platform(enum.StrEnum):
    DOUYIN = "douyin"
    XIAOHONGSHU = "xiaohongshu"
    SHIPINHAO = "shipinhao"
    WECHAT_OFFICIAL_ACCOUNT = "wechat_official_account"


class ProjectStatus(enum.StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class AccountStatus(enum.StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    BANNED = "banned"


class GroupDimension(enum.StrEnum):
    """账号分组维度：赛道 / 人设 / 平台。"""

    TRACK = "track"
    PERSONA = "persona"
    PLATFORM = "platform"


class ContentStage(enum.StrEnum):
    """内容在主链路 + 并行链路上的阶段（对应 8 个 Agent）。"""

    POSITIONING = "positioning"
    CONTENT_DIRECTION = "content_direction"
    ART_DIRECTION = "art_direction"
    VIDEO_CREATION = "video_creation"
    EDITING = "editing"
    OPERATION = "operation"
    ADVERTISING = "advertising"
    CUSTOMER_SERVICE = "customer_service"


class ContentStatus(enum.StrEnum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class DeliverableType(enum.StrEnum):
    """交付物类型（多态 payload 按此分派 Pydantic schema）。源自 SPEC 5.2。"""

    POSITIONING_STRATEGY = "positioning_strategy"
    TOPIC_PLAN = "topic_plan"
    PUBLISH_CALENDAR = "publish_calendar"
    PUBLISH_PACKAGE = "publish_package"
    VIDEO_SCRIPT = "video_script"
    ART_PROMPT = "art_prompt"
    VIDEO_ASSET = "video_asset"
    EDITED_VIDEO = "edited_video"
    REVIEW_REPORT = "review_report"
    AD_PLAN = "ad_plan"
    CS_RECORD = "cs_record"
    WECHAT_ARTICLE = "wechat_article"
    WECHAT_IMAGE_PLAN = "wechat_image_plan"
    WECHAT_RENDERED_ARTICLE = "wechat_rendered_article"


class ArticleImageSlotStatus(enum.StrEnum):
    PLANNED = "planned"
    GENERATING = "generating"
    READY = "ready"
    SELECTED = "selected"
    FAILED = "failed"


class DeliverableStatus(enum.StrEnum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class AgentTaskStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"


class GateType(enum.StrEnum):
    """6 道质量门（SPEC 5.5）。3/5/6 默认强制人工。"""

    POSITIONING_REVIEW = "positioning_review"
    TOPIC_REVIEW = "topic_review"
    SCRIPT_COMPLIANCE = "script_compliance"
    FINAL_VIDEO_REVIEW = "final_video_review"
    PRE_PUBLISH_REVIEW = "pre_publish_review"
    LARGE_AD_SPEND = "large_ad_spend"


class GateStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    AUTO_PASSED = "auto_passed"


class KnowledgeCategory(enum.StrEnum):
    """共享知识库分类：爆款库 / 用户画像 / 提示词库 / 话术库。"""

    HOT_CONTENT = "hot_content"
    USER_PERSONA = "user_persona"
    PROMPT_LIBRARY = "prompt_library"
    SCRIPT_LIBRARY = "script_library"


class ComplianceRisk(enum.StrEnum):
    """合规预检风险等级。pass=无风险；warn=疑似需人工确认；block=高危建议打回。"""

    PASS = "pass"
    WARN = "warn"
    BLOCK = "block"


class MetricSource(enum.StrEnum):
    """数据指标来源。douyin=抖音回流(E8)；manual=手动录入；demo=演示数据。"""

    DOUYIN = "douyin"
    XIAOHONGSHU = "xiaohongshu"
    SHIPINHAO = "shipinhao"
    MANUAL = "manual"
    DEMO = "demo"


class DataSourceKind(enum.StrEnum):
    OFFICIAL_API = "official_api"
    PLATFORM_EXPORT = "platform_export"
    SCREENSHOT_VERIFIED = "screenshot_verified"
    MANUAL_ENTRY = "manual_entry"


class ImportBatchStatus(enum.StrEnum):
    UPLOADED = "uploaded"
    PREVIEW_READY = "preview_ready"
    COMMITTED = "committed"
    REVOKED = "revoked"
    FAILED = "failed"


class ImportJobStatus(enum.StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"


class ImportFileStatus(enum.StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIALLY_COMPLETED = "partially_completed"
    FAILED = "failed"


class ImportRowStatus(enum.StrEnum):
    READY = "ready"
    INVALID = "invalid"
    NEEDS_RESOLUTION = "needs_resolution"
    COMMITTED = "committed"
    REVOKED = "revoked"


class ContentIdentityConfidence(enum.StrEnum):
    CONFIRMED = "confirmed"
    PROVISIONAL = "provisional"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


class ConflictStatus(enum.StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    IGNORED = "ignored"


class OptimizationSuggestionStatus(enum.StrEnum):
    """闭环优化建议状态。suggested=新建议；accepted=已采纳；verified=已验证。"""

    SUGGESTED = "suggested"
    ACCEPTED = "accepted"
    VERIFIED = "verified"


class MaterialStatus(enum.StrEnum):
    """素材生成状态。queued=已入队；generating=生成中；ready=已就绪(落库)；failed=失败。"""

    QUEUED = "queued"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class AgentCode(enum.StrEnum):
    DECISION = "00-decision"
    POSITIONING = "01-positioning"
    CONTENT_DIRECTOR = "02-content-director"
    ART_DIRECTOR = "03-art-director"
    VIDEO_CREATOR = "04-video-creator"
    EDITOR = "05-editor"
    OPERATOR = "06-operator"
    ADVERTISER = "07-advertiser"
    CUSTOMER_SERVICE = "08-customer-service"


class AgentGroup(enum.StrEnum):
    CONTROL = "control"
    STRATEGY = "strategy"
    CREATIVE = "creative"
    OPERATION = "operation"
    GROWTH = "growth"
    FEEDBACK = "feedback"


class BrainTaskType(enum.StrEnum):
    CONTENT_CREATION = "content_creation"
    ACCOUNT_DIAGNOSIS = "account_diagnosis"
    REVIEW_OPTIMIZATION = "review_optimization"
    MATRIX_DISTRIBUTION = "matrix_distribution"


class BrainTaskStatus(enum.StrEnum):
    DRAFT = "draft"
    PENDING_CONFIRMATION = "pending_confirmation"
    RUNNING = "running"
    PENDING_ACCEPTANCE = "pending_acceptance"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentInvocationStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"


class DeliverableAcceptanceStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    RERUN_REQUESTED = "rerun_requested"


class RerunScope(enum.StrEnum):
    CURRENT_AGENT = "current_agent"
    UPSTREAM = "upstream"
    DOWNSTREAM = "downstream"
    FULL_CHAIN = "full_chain"


class AutomationLevel(enum.StrEnum):
    MANUAL = "manual"
    CONFIRM = "confirm"
    AUTO = "auto"
