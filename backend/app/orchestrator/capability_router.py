"""Deterministic routing for explicit and high-confidence business requests."""

from __future__ import annotations

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
_QUERY_VERBS = ("查询", "查一下", "查看", "查", "看一下")
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
_OPERATION_TERMS = ("体检", "诊断", "分析", "优化", "策划", "生成", "发布", "执行", "制定")
_ACCOUNT_INSPECTION_CODE = "account_inspection"


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

    if _contains_any(normalized, _NEGATION_TERMS):
        return None
    if _is_question(normalized):
        return None
    if _has_only_data_query_intent(normalized):
        if _is_only_data_query(normalized):
            return _query_route("deterministic_only_data_query")
        return None
    if _is_data_query(normalized):
        return _query_route("deterministic_data_query")
    if _is_account_inspection(normalized):
        return _account_inspection_route(
            platform=platform,
            registry=registry,
            has_account=has_account,
        )
    return None


def _normalize_message(message: str) -> str:
    return "".join(message.strip().lower().split()).rstrip(_TRAILING_PUNCTUATION)


def _contains_any(message: str, terms: tuple[str, ...]) -> bool:
    return any(term in message for term in terms)


def _is_question(message: str) -> bool:
    return message.startswith(_QUESTION_PREFIXES) or message.endswith(("吗", "么", "呢"))


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
