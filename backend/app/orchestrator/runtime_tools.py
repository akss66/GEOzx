"""Production-safe tools exposed to the main-Agent runtime.

The model never selects organization, project, or account identifiers. Those
values come from the authenticated runtime context and are checked again by
each handler before data is read.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime, timedelta
from typing import Any, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.workspace_access import require_account_access
from app.models import ContentItem, DataImportBatch, User
from app.models.enums import ImportBatchStatus, UserRole
from app.services.account_data_view import (
    ACCOUNT_METRICS,
    CONTENT_METRICS,
    AccountDataMetric,
    AccountDataView,
    AccountDataViewService,
)
from app.services.publishing import publish_approved_artifact
from app.tools import ToolAdapter, ToolExecutionContext, ToolSpec

ParamsT = TypeVar("ParamsT", bound=BaseModel)


class AccountProfileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountMetricsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: int = Field(default=30, ge=1, le=90)


class EngagementContextParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: int = Field(default=30, ge=1, le=90)
    content_item_ids: list[int] = Field(default_factory=list, max_length=50)
    response_scope: str = Field(pattern="^(all|questions|negative_feedback)$")


class DeterministicConfirmActionParams(BaseModel):
    """Empty payload for the CI-only approval boundary tool."""

    model_config = ConfigDict(extra="forbid")


class PublishPackagePrepareParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_item_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=300)


class ContentPublishParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved_publish_artifact_id: int = Field(gt=0)
    source_artifact_version: int = Field(gt=0)
    scheduled_at: datetime | None = None
    visibility: str = Field(pattern="^(public|friends|private)$")
    allow_comment: bool = True


async def _deterministic_confirm_action(
    _params: DeterministicConfirmActionParams,
    context: ToolExecutionContext,
) -> dict[str, Any]:
    return {
        "approved": context.approved,
        "account_id": context.account_id,
        "status": "test_action_completed",
    }


async def _publish_package_prepare(
    params: PublishPackagePrepareParams,
    context: ToolExecutionContext,
) -> dict[str, Any]:
    if context.account_id is None:
        raise PermissionError("selected account is required")
    account = await require_account_access(context.session, context.user, context.account_id)
    content = await context.session.get(ContentItem, params.content_item_id)
    if content is None or content.account_id != account.id:
        raise PermissionError("content item does not belong to the selected account")
    return {
        "account_id": account.id,
        "content_item_id": content.id,
        "status": "prepared",
        "publish_package": {
            "platform": account.platform.value,
            "account_id": account.id,
            "content_type": "video",
            "title": params.title,
            "body": "",
            "topics": [],
            "scheduled_at": None,
            "material_ids": [],
            "cover_material_id": None,
            "visibility": "public",
            "allow_comment": True,
            "execution_mode": "manual_checklist",
            "manual_steps": ["确认标题、正文、话题和素材", "确认发布时间与可见范围"],
        },
    }


async def _content_publish(
    params: ContentPublishParams,
    context: ToolExecutionContext,
) -> dict[str, Any]:
    if context.account_id is None:
        raise PermissionError("selected account is required")
    return await publish_approved_artifact(
        context.session,
        context.user,
        account_id=context.account_id,
        artifact_id=params.approved_publish_artifact_id,
        artifact_version=params.source_artifact_version,
        scheduled_at=params.scheduled_at,
        visibility=params.visibility,
        allow_comment=params.allow_comment,
    )


async def _account_profile(
    _params: AccountProfileParams,
    context: ToolExecutionContext,
) -> dict[str, Any]:
    if context.account_id is None:
        raise PermissionError("selected account is required")
    account = await require_account_access(
        context.session,
        context.user,
        context.account_id,
    )
    return {
        "account_id": account.id,
        "nickname": account.nickname,
        "platform": account.platform.value,
        "status": account.status.value,
        "auth_status": account.auth_status,
        "data_sync_status": account.data_sync_status,
    }


async def _account_metrics_summary(
    params: AccountMetricsParams,
    context: ToolExecutionContext,
) -> dict[str, Any]:
    data_context = await _account_data_context(params, context)
    metrics = data_context["metrics"]
    return {
        "account_id": data_context["account_id"],
        "period_days": params.days,
        "snapshot_count": data_context["content_snapshot_count"],
        "play": _legacy_value(metrics, "play", 0),
        "exposure": _legacy_value(metrics, "exposure", 0),
        "follower_delta": _legacy_value(metrics, "follower_delta", 0),
        "average_completion_rate": _legacy_value(metrics, "completion_rate", 0.0),
        "average_completion_rate_5s": _legacy_value(metrics, "completion_rate_5s"),
        "average_bounce_rate_2s": _legacy_value(metrics, "bounce_rate_2s"),
        "profile_visit_count": _legacy_value(metrics, "profile_visit_count", 0),
        "content_formats": data_context["content_formats"],
        "review_statuses": data_context["review_statuses"],
        "average_like_rate": _legacy_value(metrics, "like_rate", 0.0),
        "average_comment_rate": _legacy_value(metrics, "comment_rate", 0.0),
        "average_share_rate": _legacy_value(metrics, "share_rate", 0.0),
        "coverage": data_context["coverage"],
        "freshness": data_context["freshness"],
        "conflict_count": data_context["conflict_count"],
    }


async def _account_engagement_context(
    params: EngagementContextParams,
    context: ToolExecutionContext,
) -> dict[str, Any]:
    if context.account_id is None:
        raise PermissionError("selected account is required")
    unique_content_ids = list(dict.fromkeys(params.content_item_ids))
    if unique_content_ids:
        owned_ids = set(
            await context.session.scalars(
                select(ContentItem.id).where(
                    ContentItem.account_id == context.account_id,
                    ContentItem.id.in_(unique_content_ids),
                )
            )
        )
        if owned_ids != set(unique_content_ids):
            raise PermissionError("content items do not belong to the selected account")
    data_context = await _account_data_context(
        AccountMetricsParams(days=params.days),
        context,
    )
    metric_names = (
        "engagement_rate",
        "comment_count",
        "comment_rate",
        "like_count",
        "share_count",
    )
    metrics = {
        name: data_context["metrics"].get(name, {"value": None})
        for name in metric_names
    }
    # Current official/export inputs contain aggregate interaction metrics but
    # no comment bodies.  Keep this explicit so the expert cannot invent FAQs
    # or sentiment from counts alone.
    return {
        "account_id": context.account_id,
        "period": data_context["period"],
        "response_scope": params.response_scope,
        "content_item_ids": unique_content_ids,
        "metrics": metrics,
        "comment_samples": [],
        "data_sufficiency": "aggregate_only",
        "sources": data_context["sources"],
    }


async def _account_data_context(
    params: AccountMetricsParams,
    context: ToolExecutionContext,
) -> dict[str, Any]:
    if context.account_id is None:
        raise PermissionError("selected account is required")
    account = await require_account_access(
        context.session,
        context.user,
        context.account_id,
    )
    period_end = date.today()
    period_start = period_end - timedelta(days=params.days - 1)
    view = await AccountDataViewService(context.session).load(
        account,
        period_start,
        period_end,
    )
    pending_imports = await _pending_imports(
        context.session,
        org_id=account.org_id,
        account_id=account.id,
    )
    data_period = _observed_data_period(view)
    has_available_data = bool(
        view.source_summary
        or view.content_snapshots
        or view.account_snapshots
        or view.audience
        or view.benchmarks
    )
    data_status = (
        "available"
        if has_available_data
        else ("pending_import" if pending_imports else "empty")
    )
    metric_context = _aggregate_data_metrics(view.content_snapshots, view.account_snapshots)
    return {
        "account_id": account.id,
        "data_status": data_status,
        "query_window": {
            "days": params.days,
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
        },
        "data_period": data_period,
        "pending_imports": pending_imports,
        "period": {
            "days": params.days,
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
        },
        "coverage": view.coverage,
        "freshness": {
            "latest_observed_at": _iso(view.freshness.latest_observed_at),
            "latest_confirmed_at": _iso(view.freshness.latest_confirmed_at),
            "days_since_observed": view.freshness.days_since_observed,
            "days_since_confirmed": view.freshness.days_since_confirmed,
        },
        "conflict_count": len(view.conflicts),
        "sources": [
            {
                "batch_id": item.batch_id,
                "source_kind": item.source_kind,
                "data_domains": item.data_domains,
                "confirmed_at": _iso(item.confirmed_at),
                "period_start": _iso(item.period_start),
                "period_end": _iso(item.period_end),
            }
            for item in view.source_summary
        ],
        "metrics": metric_context,
        "content_snapshot_count": len(view.content_snapshots),
        "account_snapshot_count": len(view.account_snapshots),
        "content_formats": _dimension_counts(view.content_snapshots, "content_format"),
        "review_statuses": _dimension_counts(view.content_snapshots, "review_status"),
        "audience_dimension_count": len(view.audience),
        "benchmark_count": len(view.benchmarks),
    }


async def _pending_imports(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int,
) -> list[dict[str, Any]]:
    batches = list(
        await session.scalars(
            select(DataImportBatch)
            .where(
                DataImportBatch.org_id == org_id,
                DataImportBatch.account_id == account_id,
                DataImportBatch.committed_at.is_(None),
                DataImportBatch.revoked_at.is_(None),
                DataImportBatch.status.notin_(
                    [ImportBatchStatus.COMMITTED, ImportBatchStatus.REVOKED]
                ),
            )
            .order_by(DataImportBatch.id.desc())
            .limit(5)
        )
    )
    return [
        {
            "batch_id": batch.id,
            "status": batch.status.value,
            "template_code": batch.template_code,
            "row_count": batch.row_count,
            "period_start": _iso(batch.period_start),
            "period_end": _iso(batch.period_end),
        }
        for batch in batches
    ]


def _observed_data_period(view: AccountDataView) -> dict[str, str] | None:
    observed_dates = [
        item.stat_date
        for collection in (
            view.content_snapshots,
            view.account_snapshots,
            view.audience,
            view.benchmarks,
        )
        for item in collection
    ]
    for source in view.source_summary:
        if source.period_start is not None:
            observed_dates.append(source.period_start)
        if source.period_end is not None:
            observed_dates.append(source.period_end)
    if not observed_dates:
        return None
    return {
        "start": min(observed_dates).isoformat(),
        "end": max(observed_dates).isoformat(),
    }


_SUM_METRICS = {
    "play",
    "exposure",
    "follower_delta",
    "unfollow_count",
    "like_count",
    "comment_count",
    "share_count",
    "favorite_count",
    "profile_visit_count",
}
_LATEST_METRICS = {"follower_count"}


def _aggregate_data_metrics(content_snapshots, account_snapshots) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for metric_name in dict.fromkeys((*CONTENT_METRICS, *ACCOUNT_METRICS)):
        primary_snapshots, fallback_snapshots = (
            (account_snapshots, content_snapshots)
            if metric_name in ACCOUNT_METRICS
            else (content_snapshots, account_snapshots)
        )
        metrics = [
            item.metrics[metric_name]
            for item in primary_snapshots
            if metric_name in item.metrics and item.metrics[metric_name].value is not None
        ]
        if not metrics:
            metrics = [
                item.metrics[metric_name]
                for item in fallback_snapshots
                if metric_name in item.metrics and item.metrics[metric_name].value is not None
            ]
        result[metric_name] = _metric_context(metric_name, metrics)
    return result


def _metric_context(metric_name: str, metrics: list[AccountDataMetric]) -> dict[str, Any]:
    values = [float(item.value) for item in metrics if item.value is not None]
    value: int | float | None
    if not values:
        value = None
    elif metric_name in _LATEST_METRICS:
        latest_metric = max(
            metrics,
            key=lambda item: max(
                (observation.observed_at for observation in item.observations),
                default=date.min,
            ),
        )
        value = latest_metric.value
    elif metric_name in _SUM_METRICS:
        total = float(sum(values))
        value = int(total) if total.is_integer() else total
    else:
        value = sum(values) / len(values)

    observations = [observation for item in metrics for observation in item.observations]
    source_values = sorted({_enum_value(item.source) for item in observations})
    latest_observed_at = max(
        (item.observed_at for item in observations),
        default=None,
    )
    latest_confirmed_at = max(
        (item.confirmed_at for item in observations if item.confirmed_at is not None),
        default=None,
    )
    evidence_refs = []
    seen_refs: set[tuple[str, int]] = set()
    for observation in observations:
        key = (observation.evidence_kind, observation.evidence_id)
        if key in seen_refs:
            continue
        seen_refs.add(key)
        evidence_refs.append({"kind": key[0], "id": key[1]})
    return {
        "value": value,
        "source": (
            source_values[0] if len(source_values) == 1 else ("mixed" if source_values else None)
        ),
        "sources": source_values,
        "observed_at": _iso(latest_observed_at),
        "confirmed_at": _iso(latest_confirmed_at),
        "evidence_refs": evidence_refs,
    }


def _legacy_value(metrics: dict[str, dict[str, Any]], name: str, default=None):
    value = metrics.get(name, {}).get("value")
    return default if value is None else value


def _dimension_counts(rows, field_name: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = getattr(row, field_name, None)
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return counts


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _tool_handler(
    params_model: type[ParamsT],
    handler: Callable[[ParamsT, ToolExecutionContext], Awaitable[Mapping[str, Any]]],
) -> Callable[[BaseModel, ToolExecutionContext], Awaitable[Mapping[str, Any]]]:
    async def invoke(
        params: BaseModel,
        context: ToolExecutionContext,
    ) -> Mapping[str, Any]:
        if not isinstance(params, params_model):
            raise TypeError(f"expected {params_model.__name__} params")
        return await handler(cast(ParamsT, params), context)

    return invoke


_RUNTIME_TOOL_SPECS = (
    ToolSpec(
        name="account.profile",
        handler=_tool_handler(AccountProfileParams, _account_profile),
        side_effect_level="read",
        params_model=AccountProfileParams,
        allowed_roles=frozenset({UserRole.ADMIN, UserRole.USER}),
        scope="account",
    ),
    ToolSpec(
        name="account.data_context",
        handler=_tool_handler(AccountMetricsParams, _account_data_context),
        side_effect_level="read",
        params_model=AccountMetricsParams,
        allowed_roles=frozenset({UserRole.ADMIN, UserRole.USER}),
        scope="account",
    ),
    ToolSpec(
        name="account.metrics_summary",
        handler=_tool_handler(AccountMetricsParams, _account_metrics_summary),
        side_effect_level="read",
        params_model=AccountMetricsParams,
        allowed_roles=frozenset({UserRole.ADMIN, UserRole.USER}),
        scope="account",
    ),
    ToolSpec(
        name="account.engagement_context",
        handler=_tool_handler(EngagementContextParams, _account_engagement_context),
        side_effect_level="read",
        params_model=EngagementContextParams,
        allowed_roles=frozenset({UserRole.ADMIN, UserRole.USER}),
        scope="account",
    ),
    ToolSpec(
        name="publish_package_prepare",
        handler=_tool_handler(PublishPackagePrepareParams, _publish_package_prepare),
        side_effect_level="idempotent_write",
        params_model=PublishPackagePrepareParams,
        allowed_roles=frozenset({UserRole.ADMIN, UserRole.USER}),
        scope="account",
        execution_phase="prepare",
    ),
    ToolSpec(
        name="platform.content_publish",
        handler=_tool_handler(ContentPublishParams, _content_publish),
        side_effect_level="idempotent_write",
        params_model=ContentPublishParams,
        allowed_roles=frozenset({UserRole.ADMIN, UserRole.USER}),
        scope="account",
        execution_phase="side_effect",
        timeout_seconds=30.0,
    ),
)


def _runtime_tool_specs() -> tuple[ToolSpec, ...]:
    if not (settings.environment == "test" and settings.llm_deterministic_test_provider_enabled):
        return _RUNTIME_TOOL_SPECS
    return (
        *_RUNTIME_TOOL_SPECS,
        ToolSpec(
            name="test.confirm_action",
            handler=_tool_handler(
                DeterministicConfirmActionParams,
                _deterministic_confirm_action,
            ),
            side_effect_level="idempotent_write",
            params_model=DeterministicConfirmActionParams,
            allowed_roles=frozenset({UserRole.ADMIN, UserRole.USER}),
            permission_mode="confirm",
            scope="account",
        ),
    )


def build_runtime_tool_adapter() -> ToolAdapter:
    return ToolAdapter(list(_runtime_tool_specs()))


def runtime_tool_phase(tool_code: str) -> str:
    for spec in _runtime_tool_specs():
        if spec.name == tool_code:
            return spec.resolved_execution_phase
    raise KeyError(tool_code)


def runtime_tool_capabilities(user: User) -> list[dict[str, Any]]:
    capabilities: list[dict[str, Any]] = []
    for spec in _runtime_tool_specs():
        if user.role not in spec.allowed_roles or spec.permission_mode == "disabled":
            continue
        capabilities.append(
            {
                "kind": "tool",
                "code": spec.name,
                "name": spec.name,
                "description": _tool_description(spec.name),
                "permission_mode": spec.permission_mode,
                "execution_phase": spec.resolved_execution_phase,
                "scope": spec.scope,
                "parameters": spec.params_model.model_json_schema(),
            }
        )
    return sorted(capabilities, key=lambda item: str(item["code"]))


def _tool_description(code: str) -> str:
    if code == "test.confirm_action":
        return "CI-only controlled action used to verify approval persistence."
    return {
        "account.profile": "读取当前已选账号的公开概况和接入状态",
        "account.data_context": "读取当前账号统一数据视图、指标证据、覆盖度、时效与冲突",
        "account.metrics_summary": "汇总当前已选账号最近 1-90 天的运营指标",
        "account.engagement_context": (
            "读取当前账号互动指标和可核验评论样本；没有评论正文时明确返回数据不足"
        ),
        "publish_package_prepare": "为当前账号内容生成可审计、可审批的发布准备包",
        "platform.content_publish": (
            "将已审批且版本未变化的发布包交给官方平台通道，并返回真实回执状态"
        ),
    }[code]
