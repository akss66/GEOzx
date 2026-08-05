"""Deterministic routing for explicit and high-confidence business requests."""

from __future__ import annotations

import re

from app.orchestrator.skills.public_catalog import PUBLIC_SKILL_POLICIES
from app.orchestrator.skills.registry import SkillRegistry
from app.schemas.conversation import TurnExecutionMode, TurnRouteDecision

_TRAILING_PUNCTUATION = "。！？!?，,；;：:~～"
_GREETING_MESSAGES = frozenset({"你好", "您好", "嗨", "哈喽", "hello", "hi"})
_IDENTITY_MESSAGES = frozenset({"你是谁", "你是什么", "你是干什么的"})
_CAPABILITY_MESSAGES = frozenset({"你能做什么", "你会做什么", "你有什么能力", "你有哪些能力"})
_QUESTION_PREFIXES = (
    "你能",
    "你会",
    "为什么",
    "为何",
    "怎么",
    "如何",
    "能否",
    "可否",
    "是否",
    "请问",
)
_NEGATION_TERMS = (
    "不要",
    "不做",
    "别",
    "无需",
    "不用",
    "不需要",
    "不必",
    "取消",
    "先不",
    "暂不",
    "停止",
)
_QUERY_VERBS = ("查询", "查一下", "查看", "看看", "查", "看一下")
_QUERY_TARGETS = (
    "数据",
    "播放量",
    "点赞",
    "评论",
    "转发",
    "粉丝",
    "曝光",
    "互动",
    "完播",
    "转化",
    "涨粉",
    "gmv",
)
_DATA_AVAILABILITY_PATTERNS = (
    "账号有数据吗",
    "账号有没有数据",
    "当前账号有数据吗",
    "现在账号有数据吗",
    "现在有数据了吗",
    "已经有数据了吗",
    "数据更新到哪一天",
    "数据更新到哪天",
    "有哪些指标",
    "有什么指标",
)
_METRIC_QUESTION_MARKERS = ("多少", "怎么样", "如何", "高吗", "低吗", "趋势")
_OPERATION_TERMS = ("体检", "诊断", "分析", "优化", "策划", "生成", "发布", "执行", "制定")
_ACCOUNT_INSPECTION_CODE = "account_inspection"
_ACCOUNT_DATA_ANALYSIS_CODE = "account_data_analysis"
_ANALYSIS_MARKERS = (
    "分析",
    "表现怎么样",
    "表现如何",
    "下降",
    "上升",
    "变化",
    "趋势",
    "异常",
    "最差",
    "最好",
    "哪个指标",
    "够不够判断",
)
_ANALYSIS_CONTEXT = (
    "账号",
    "账户",
    "数据",
    "指标",
    "表现",
    "作品",
    "现状",
    "留存",
    *_QUERY_TARGETS,
)
_UNSUPPORTED_BENCHMARK_TERMS = ("行业平均", "行业基准", "行业水平", "大盘平均")
_ALLOWED_NEGATED_OUTPUTS = (
    "不要生成长期策略",
    "不生成长期策略",
    "不要生成策略",
    "不生成策略",
    "无需生成策略",
    "不用生成策略",
    "不要生成30天策略",
    "不生成30天策略",
)
_ANALYSIS_DAY_PATTERN = re.compile(r"(?:最近|近|过去)?(?P<value>\d{1,3})天")
_ANALYSIS_TOP_N_PATTERN = re.compile(r"(?:最差|最好).{0,8}?(?P<value>\d{1,2})条")
_METRIC_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("play", ("播放量", "播放")),
    ("exposure", ("曝光量", "曝光")),
    ("follower_delta", ("净增粉丝", "新增粉丝", "涨粉")),
    ("follower_count", ("总粉丝量", "总粉丝", "粉丝量", "粉丝数")),
    ("like_count", ("作品点赞", "点赞量", "点赞")),
    ("comment_count", ("作品评论", "评论量", "评论")),
    ("share_count", ("作品分享", "分享量", "转发量", "分享", "转发")),
    ("completion_rate", ("完播率", "完播")),
    ("cover_click_rate", ("封面点击率", "封面点击")),
    ("profile_visit_count", ("主页访问量", "主页访问")),
    ("engagement_rate", ("互动率", "互动")),
    ("unfollow_count", ("取关粉丝", "取关")),
    ("retention_rate", ("留存率", "留存")),
)
_FRESH_OPERATION_REQUESTS = frozenset({"结合最近数据和对标内容，规划并制作下周抖音内容"})
_MIGRATED_OPERATION_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("operation_iteration", ("运营迭代", "下一周期", "下周运营")),
    ("engagement_review", ("互动复盘", "评论复盘", "评论分析", "用户反馈")),
    ("performance_review", ("数据复盘", "运营复盘", "表现复盘", "复盘")),
    ("account_positioning", ("账号定位", "账户定位", "人设定位", "定位诊断")),
    ("visual_brief_generation", ("视觉brief", "视觉方案", "封面方案", "分镜")),
    ("content_calendar_planning", ("内容排期", "发布排期", "内容日历")),
    ("topic_planning", ("选题", "内容方向")),
    ("script_generation", ("脚本", "口播稿")),
    ("publishing_preparation", ("发布准备", "发布检查", "发布清单")),
)


def route_deterministic_request(
    message: str,
    *,
    platform: str,
    registry: SkillRegistry,
    has_account: bool,
) -> TurnRouteDecision | None:
    """Route only unambiguous, safe natural-language requests without a model."""

    normalized = _normalize_message(message)
    if not normalized:
        return None

    if normalized in _GREETING_MESSAGES:
        return _answer_route("deterministic_greeting")
    if normalized in _IDENTITY_MESSAGES:
        return _answer_route("deterministic_identity_question")
    if normalized in _CAPABILITY_MESSAGES:
        return _answer_route("deterministic_capability_question")

    if _contains_blocking_negation(normalized):
        return None
    if _is_capability_question(normalized):
        return _answer_route("deterministic_capability_question")
    if _is_data_availability_query(normalized):
        return _query_route("deterministic_data_availability_query")
    if _has_only_data_query_intent(normalized) and not _is_only_data_query(normalized):
        return None
    if _is_unsupported_benchmark_analysis(normalized):
        return _unsupported_benchmark_route(
            platform=platform,
            registry=registry,
            has_account=has_account,
        )
    if _is_account_inspection(normalized):
        if _is_question(normalized):
            return None
        return _account_inspection_route(
            platform=platform,
            registry=registry,
            has_account=has_account,
        )
    if _is_account_data_analysis(normalized):
        return _published_skill_route(
            skill_code=_ACCOUNT_DATA_ANALYSIS_CODE,
            platform=platform,
            registry=registry,
            has_account=has_account,
        )
    if _is_metric_lookup(normalized):
        return _query_route("deterministic_metric_query")
    if _is_question(normalized):
        return None
    if _has_only_data_query_intent(normalized):
        if _is_only_data_query(normalized):
            return _query_route("deterministic_only_data_query")
        return None
    if _is_data_query(normalized):
        return _query_route("deterministic_data_query")
    if normalized in _FRESH_OPERATION_REQUESTS:
        return _published_skill_route(
            skill_code="operation_iteration",
            platform=platform,
            registry=registry,
            has_account=has_account,
        )
    alias_route = _route_public_skill_alias(
        normalized,
        platform=platform,
        registry=registry,
        has_account=has_account,
    )
    if alias_route is not None:
        return alias_route
    migrated = route_migrated_operation_request(
        normalized,
        platform=platform,
        registry=registry,
        has_account=has_account,
    )
    if migrated is not None:
        return migrated
    return None


def route_migrated_operation_request(
    message: str,
    *,
    platform: str,
    registry: SkillRegistry,
    has_account: bool,
) -> TurnRouteDecision | None:
    """Keep migrated operations out of the legacy all-purpose task graph."""

    normalized = _normalize_message(message)
    if not normalized or _contains_any(normalized, _NEGATION_TERMS) or _is_question(normalized):
        return None
    if any(term in normalized for term in ("直接发布", "立即发布", "现在发布", "发出去")):
        return _artifact_clarification(
            skill_code="content_publishing",
            missing_field="approved_publish_artifact_id",
            question="请选择一个已审批的发布包后再执行发布。",
            platform=platform,
            registry=registry,
            has_account=has_account,
        )
    for skill_code, patterns in _MIGRATED_OPERATION_PATTERNS:
        if not any(pattern in normalized for pattern in patterns):
            continue
        if skill_code in {
            "visual_brief_generation",
            "content_calendar_planning",
        }:
            return _artifact_clarification(
                skill_code=skill_code,
                missing_field="source_artifact_ids",
                question="请先选择要继续加工的已确认成果。",
                platform=platform,
                registry=registry,
                has_account=has_account,
            )
        if skill_code == "operation_iteration":
            return _artifact_clarification(
                skill_code=skill_code,
                missing_field="confirmed_review_artifact_id",
                question="请先选择一份已确认的复盘报告，用它编排下一运营周期。",
                platform=platform,
                registry=registry,
                has_account=has_account,
            )
        return _published_skill_route(
            skill_code=skill_code,
            platform=platform,
            registry=registry,
            has_account=has_account,
        )
    return None


def _published_skill_route(
    *,
    skill_code: str,
    platform: str,
    registry: SkillRegistry,
    has_account: bool,
) -> TurnRouteDecision | None:
    try:
        skill = registry.get(skill_code)
    except KeyError:
        return None
    if platform not in skill.supported_platforms:
        return None
    if not has_account:
        return TurnRouteDecision(
            mode=TurnExecutionMode.CLARIFY,
            intent=skill_code,
            confidence=1,
            reason="migrated_operation_requires_account",
            skill_code=skill_code,
            requires_account_context=True,
            requires_operation_task=False,
            missing_field="account_id",
            clarifying_question="请先选择需要操作的账号。",
        )
    return TurnRouteDecision(
        mode=TurnExecutionMode.SKILL,
        intent=skill_code,
        confidence=1,
        reason="migrated_operation_uses_typed_skill",
        skill_code=skill_code,
        requires_account_context=True,
        requires_operation_task=True,
    )


def _artifact_clarification(
    *,
    skill_code: str,
    missing_field: str,
    question: str,
    platform: str,
    registry: SkillRegistry,
    has_account: bool,
) -> TurnRouteDecision | None:
    route = _published_skill_route(
        skill_code=skill_code,
        platform=platform,
        registry=registry,
        has_account=has_account,
    )
    if route is None or route.mode is TurnExecutionMode.CLARIFY:
        return route
    return TurnRouteDecision(
        mode=TurnExecutionMode.CLARIFY,
        intent=skill_code,
        confidence=1,
        reason="migrated_operation_requires_confirmed_artifact",
        skill_code=skill_code,
        requires_account_context=True,
        requires_operation_task=False,
        missing_field=missing_field,
        clarifying_question=question,
    )


def _normalize_message(message: str) -> str:
    return "".join(message.strip().lower().split()).rstrip(_TRAILING_PUNCTUATION)


def _contains_any(message: str, terms: tuple[str, ...]) -> bool:
    return any(term in message for term in terms)


def _contains_blocking_negation(message: str) -> bool:
    remaining = message
    for phrase in _ALLOWED_NEGATED_OUTPUTS:
        remaining = remaining.replace(phrase, "")
    return _contains_any(remaining, _NEGATION_TERMS)


def _is_question(message: str) -> bool:
    return message.startswith(_QUESTION_PREFIXES) or message.endswith(("吗", "么", "呢"))


def _is_capability_question(message: str) -> bool:
    return (
        message.endswith(("吗", "么"))
        and message.startswith(("你能查询", "你会查询", "你支持查询", "是否支持查询"))
        and _contains_any(message, _QUERY_TARGETS)
    )


def _is_data_availability_query(message: str) -> bool:
    compact = message.replace("我的", "").replace("我现在的", "现在")
    return any(pattern in compact for pattern in _DATA_AVAILABILITY_PATTERNS)


def _is_account_data_analysis(message: str) -> bool:
    if any(marker in message for marker in ("体检", "复盘", "顺便")):
        return False
    if any(phrase in message for phrase in ("评论分析", "互动分析", "用户反馈", "评论互动")):
        return False
    return _contains_any(message, _ANALYSIS_MARKERS) and _contains_any(
        message,
        _ANALYSIS_CONTEXT,
    )


def _is_unsupported_benchmark_analysis(message: str) -> bool:
    return _contains_any(message, _UNSUPPORTED_BENCHMARK_TERMS) and (
        _is_account_data_analysis(message) or _contains_any(message, _QUERY_TARGETS)
    )


def _unsupported_benchmark_route(
    *,
    platform: str,
    registry: SkillRegistry,
    has_account: bool,
) -> TurnRouteDecision | None:
    route = _published_skill_route(
        skill_code=_ACCOUNT_DATA_ANALYSIS_CODE,
        platform=platform,
        registry=registry,
        has_account=has_account,
    )
    if route is None or route.mode is TurnExecutionMode.CLARIFY:
        return route
    return TurnRouteDecision(
        mode=TurnExecutionMode.CLARIFY,
        intent=_ACCOUNT_DATA_ANALYSIS_CODE,
        confidence=1,
        reason="industry_benchmark_data_is_not_available",
        skill_code=_ACCOUNT_DATA_ANALYSIS_CODE,
        requires_account_context=True,
        requires_operation_task=False,
        missing_field="benchmark_data",
        clarifying_question=(
            "当前没有已确认的行业基准数据。你可以改为分析当前账号自身趋势，"
            "或先导入可核验的行业对标数据。"
        ),
    )


def extract_account_analysis_input(message: str) -> dict[str, object]:
    """Extract bounded, deterministic input for the account analysis Skill."""

    normalized = _normalize_message(message)
    if not _is_account_data_analysis(normalized):
        return {}
    day_match = _ANALYSIS_DAY_PATTERN.search(normalized)
    has_metric = any(alias in normalized for _, aliases in _METRIC_ALIASES for alias in aliases)
    has_comparison_shape = any(
        marker in normalized
        for marker in ("表现怎么样", "表现如何", "下降", "上升", "变化", "最差", "最好", "哪个指标")
    )
    if day_match is None and not has_metric and not has_comparison_shape:
        return {}
    result: dict[str, object] = {
        "question": message.strip(),
        "comparison": "auto",
    }
    if day_match is not None:
        days = int(day_match.group("value"))
        if 1 <= days <= 90:
            result["days"] = days
    metrics = [
        metric_code
        for metric_code, aliases in _METRIC_ALIASES
        if any(alias in normalized for alias in aliases)
    ]
    if metrics:
        result["requested_metrics"] = metrics
    top_n_match = _ANALYSIS_TOP_N_PATTERN.search(normalized)
    if top_n_match is not None:
        top_n = int(top_n_match.group("value"))
        if 1 <= top_n <= 20:
            result["top_n"] = top_n
    if "最差" in normalized:
        result["ranking_mode"] = "bottom"
        result["analysis_focus"] = "content_ranking"
    elif "最好" in normalized:
        result["ranking_mode"] = "top"
        result["analysis_focus"] = "content_ranking"
    elif "从什么时候开始" in normalized or "什么时候开始" in normalized:
        result["analysis_focus"] = "change_onset"
    elif "哪个指标" in normalized:
        result["analysis_focus"] = "metric_comparison"
    return result


def _is_metric_lookup(message: str) -> bool:
    if not _contains_any(message, _QUERY_TARGETS):
        return False
    has_lookup_shape = _contains_any(message, _QUERY_VERBS) or any(
        marker in message for marker in _METRIC_QUESTION_MARKERS
    )
    has_scope = any(
        marker in message
        for marker in ("当前账号", "这个账号", "最近", "近7天", "近30天", "本周", "本月")
    )
    return has_lookup_shape and has_scope and not _has_positive_operation(message)


def _route_public_skill_alias(
    message: str,
    *,
    platform: str,
    registry: SkillRegistry,
    has_account: bool,
) -> TurnRouteDecision | None:
    matches = [
        policy.code
        for policy in PUBLIC_SKILL_POLICIES.values()
        if policy.enabled and any(alias in message for alias in policy.aliases)
    ]
    if len(matches) != 1:
        return None
    code = matches[0]
    if code in {"visual_brief_generation", "content_calendar_planning"}:
        return _artifact_clarification(
            skill_code=code,
            missing_field="source_artifact_ids",
            question="请先选择要继续加工的已确认成果。",
            platform=platform,
            registry=registry,
            has_account=has_account,
        )
    if code == "operation_iteration":
        return _artifact_clarification(
            skill_code=code,
            missing_field="confirmed_review_artifact_id",
            question="请先选择一份已确认的复盘报告。",
            platform=platform,
            registry=registry,
            has_account=has_account,
        )
    if code == "content_publishing":
        return _artifact_clarification(
            skill_code=code,
            missing_field="approved_publish_artifact_id",
            question="请选择一份已审批的发布包后再执行发布。",
            platform=platform,
            registry=registry,
            has_account=has_account,
        )
    return _published_skill_route(
        skill_code=code,
        platform=platform,
        registry=registry,
        has_account=has_account,
    )


def _answer_route(reason: str) -> TurnRouteDecision:
    return TurnRouteDecision(
        mode=TurnExecutionMode.ANSWER,
        intent="general_question",
        confidence=1,
        reason=reason,
    )


def _query_route(reason: str) -> TurnRouteDecision:
    return TurnRouteDecision(
        mode=TurnExecutionMode.QUERY,
        intent="account_data_query",
        confidence=1,
        reason=reason,
        skill_code="account_data_query",
        requires_account_context=True,
        requires_operation_task=False,
    )


def _is_only_data_query(message: str) -> bool:
    return _has_only_data_query_intent(message) and not _has_positive_operation(message)


def _has_only_data_query_intent(message: str) -> bool:
    return any(
        marker in message for marker in ("只查询", "仅查询", "只查", "仅查", "只看")
    ) and _contains_any(message, _QUERY_TARGETS)


def _is_data_query(message: str) -> bool:
    return (
        _contains_any(message, _QUERY_VERBS)
        and _contains_any(message, _QUERY_TARGETS)
        and not _contains_any(message, _OPERATION_TERMS)
    )


def _is_account_inspection(message: str) -> bool:
    return "体检" in message and any(
        account_term in message for account_term in ("账号", "账户", "当前号")
    )


def _has_positive_operation(message: str) -> bool:
    for operation in _OPERATION_TERMS:
        position = message.find(operation)
        prefix = message[max(0, position - 8) : position] if position >= 0 else ""
        if (
            position >= 0
            and not prefix.endswith("不")
            and not _contains_any(prefix, _NEGATION_TERMS)
        ):
            return True
    return False


def _account_inspection_route(
    *, platform: str, registry: SkillRegistry, has_account: bool
) -> TurnRouteDecision | None:
    try:
        skill = registry.get(_ACCOUNT_INSPECTION_CODE)
    except KeyError:
        return None
    if platform not in skill.supported_platforms:
        return None
    if not has_account:
        return TurnRouteDecision(
            mode=TurnExecutionMode.CLARIFY,
            intent="account_inspection",
            confidence=1,
            reason="deterministic_account_inspection_requires_account_context",
            skill_code=skill.code,
            requires_account_context=True,
            requires_operation_task=False,
            missing_field="account_id",
            clarifying_question="请先选择需要操作的账号。",
        )
    return TurnRouteDecision(
        mode=TurnExecutionMode.SKILL,
        intent="account_inspection",
        confidence=1,
        reason="deterministic_account_inspection",
        skill_code=skill.code,
        requires_account_context=True,
        requires_operation_task=True,
    )


class SkillUnavailable(ValueError):
    """A requested Skill cannot be executed in the current route context."""

    def __init__(self, *, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(reason)


def route_explicit_request(
    requested_skill_code: str | None,
    *,
    platform: str,
    registry: SkillRegistry,
    has_account: bool,
) -> TurnRouteDecision | None:
    """Route an explicit Skill selection or defer classification when absent."""

    if requested_skill_code is None:
        return None

    try:
        skill = registry.get(requested_skill_code)
    except KeyError as error:
        raise SkillUnavailable(
            code="unknown_skill",
            reason="requested_skill_not_registered",
        ) from error

    if platform not in skill.supported_platforms:
        raise SkillUnavailable(
            code="unsupported_platform",
            reason="requested_skill_platform_incompatible",
        )

    if not has_account:
        return TurnRouteDecision(
            mode=TurnExecutionMode.CLARIFY,
            intent="explicit_skill",
            confidence=1,
            reason="explicit_skill_requires_account_context",
            skill_code=requested_skill_code,
            requires_account_context=True,
            requires_operation_task=False,
            missing_field="account_id",
            clarifying_question="请先选择需要操作的账号。",
        )

    return TurnRouteDecision(
        mode=TurnExecutionMode.SKILL,
        intent="explicit_skill",
        confidence=1,
        reason="explicit_skill_request",
        skill_code=requested_skill_code,
        requires_account_context=True,
        requires_operation_task=True,
    )
