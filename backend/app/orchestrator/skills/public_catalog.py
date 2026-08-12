"""Publication and availability policy for user-selectable business Skills."""

from dataclasses import dataclass

from app.models.enums import UserRole
from app.schemas.skills import (
    CapabilityRequiredContext,
    PublicSkillCategory,
    SkillCatalogSurface,
)


@dataclass(frozen=True)
class PublicSkillPolicy:
    code: str
    category: PublicSkillCategory
    icon: str
    requires_account: bool
    surfaces: frozenset[SkillCatalogSurface]
    required_context: tuple[CapabilityRequiredContext, ...] = ("account",)
    enabled: bool = True
    allowed_roles: frozenset[UserRole] = frozenset({UserRole.ADMIN, UserRole.USER})
    internal_disabled_reason: str | None = None
    aliases: tuple[str, ...] = ()


PUBLIC_SKILL_POLICIES: dict[str, PublicSkillPolicy] = {
    "account_data_analysis": PublicSkillPolicy(
        code="account_data_analysis",
        category="quick_operations",
        icon="line-chart",
        requires_account=True,
        surfaces=frozenset({"composer"}),
        aliases=("账号数据分析", "数据分析", "趋势分析", "作品表现分析"),
    ),
    "account_positioning": PublicSkillPolicy(
        code="account_positioning",
        category="quick_operations",
        icon="compass",
        requires_account=True,
        surfaces=frozenset({"composer"}),
        aliases=("账号定位", "人设定位", "定位诊断"),
    ),
    "visual_brief_generation": PublicSkillPolicy(
        code="visual_brief_generation",
        category="quick_operations",
        icon="picture",
        requires_account=True,
        surfaces=frozenset({"composer"}),
        required_context=("account", "confirmed_artifact"),
        aliases=("视觉brief", "视觉方案", "封面方案", "分镜"),
    ),
    "content_calendar_planning": PublicSkillPolicy(
        code="content_calendar_planning",
        category="quick_operations",
        icon="calendar",
        requires_account=True,
        surfaces=frozenset({"composer"}),
        required_context=("account", "confirmed_artifact"),
        aliases=("内容排期", "发布排期", "内容日历"),
    ),
    "content_publishing": PublicSkillPolicy(
        code="content_publishing",
        category="quick_operations",
        icon="send",
        requires_account=True,
        surfaces=frozenset({"composer"}),
        required_context=("account", "confirmed_artifact", "platform_connection"),
        aliases=("内容发布", "立即发布", "直接发布"),
    ),
    "engagement_review": PublicSkillPolicy(
        code="engagement_review",
        category="quick_operations",
        icon="message-circle",
        requires_account=True,
        surfaces=frozenset({"composer"}),
        required_context=("account", "account_data"),
        aliases=("互动复盘", "评论分析", "用户反馈"),
    ),
    "operation_iteration": PublicSkillPolicy(
        code="operation_iteration",
        category="quick_operations",
        icon="repeat",
        requires_account=True,
        surfaces=frozenset({"composer", "artifact_center"}),
        required_context=("account", "confirmed_artifact"),
        aliases=("运营迭代", "下一周期", "下周运营"),
    ),
    "account_inspection": PublicSkillPolicy(
        code="account_inspection",
        category="quick_operations",
        icon="activity",
        requires_account=True,
        surfaces=frozenset({"composer"}),
        aliases=("一键账号体检", "账号体检", "账号诊断"),
    ),
    "topic_planning": PublicSkillPolicy(
        code="topic_planning",
        category="quick_operations",
        icon="calendar",
        requires_account=True,
        surfaces=frozenset({"composer"}),
        aliases=("选题策划", "选题规划", "内容方向"),
    ),
    "script_generation": PublicSkillPolicy(
        code="script_generation",
        category="quick_operations",
        icon="file-text",
        requires_account=True,
        surfaces=frozenset({"composer"}),
        aliases=("脚本生成", "口播脚本", "视频脚本"),
    ),
    "publishing_preparation": PublicSkillPolicy(
        code="publishing_preparation",
        category="quick_operations",
        icon="send",
        requires_account=True,
        surfaces=frozenset({"composer"}),
        aliases=("发布准备", "发布检查", "发布清单"),
    ),
    "performance_review": PublicSkillPolicy(
        code="performance_review",
        category="quick_operations",
        icon="line-chart",
        requires_account=True,
        surfaces=frozenset({"composer"}),
        aliases=("数据复盘", "运营复盘", "表现复盘"),
    ),
    "wechat_article_production": PublicSkillPolicy(
        code="wechat_article_production",
        category="quick_operations",
        icon="file-text",
        requires_account=True,
        surfaces=frozenset({"composer", "artifact_center"}),
        aliases=("微信公众号文章", "公众号文章", "公众号推文", "微信长文"),
    ),
}


__all__ = ["PUBLIC_SKILL_POLICIES", "PublicSkillPolicy"]
