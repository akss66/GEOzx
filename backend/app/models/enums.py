"""全局枚举：状态机、平台、质量门、交付物类型等。

集中定义，供模型与 schema 共用。用 StrEnum，成员 value 为稳定小写字符串（入库即此值，
由 base.pg_enum 的 values_callable 保证）。新增成员向后兼容。
"""

import enum


class UserRole(enum.StrEnum):
    """v1 两级角色（可扩展）。admin=系统配置/账号/质量门策略/用户管理；user=日常使用。"""

    ADMIN = "admin"
    USER = "user"


class Platform(enum.StrEnum):
    DOUYIN = "douyin"
    XIAOHONGSHU = "xiaohongshu"
    SHIPINHAO = "shipinhao"


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
    VIDEO_SCRIPT = "video_script"
    ART_PROMPT = "art_prompt"
    VIDEO_ASSET = "video_asset"
    EDITED_VIDEO = "edited_video"
    REVIEW_REPORT = "review_report"
    AD_PLAN = "ad_plan"
    CS_RECORD = "cs_record"


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


class MaterialStatus(enum.StrEnum):
    """素材生成状态。queued=已入队；generating=生成中；ready=已就绪(落库)；failed=失败。"""

    QUEUED = "queued"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"
