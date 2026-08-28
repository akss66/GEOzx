"""Pure rollout alert evaluation for WeChat Official Account production gates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class WechatRolloutAlert:
    code: str
    severity: str
    message: str
    context: dict[str, int | float | str]


def evaluate_wechat_rollout_alerts(
    *,
    now: datetime,
    component_ticket_created_at: datetime | None,
    consecutive_component_refresh_failures: int,
    consecutive_authorizer_refresh_failures: int,
    draft_sync_failures_last_5m: int,
    draft_sync_attempts_last_5m: int,
    conflicting_idempotency_reuses: int,
    scope_denial_anomalies: int,
) -> list[WechatRolloutAlert]:
    alerts: list[WechatRolloutAlert] = []
    if component_ticket_created_at is not None:
        age_minutes = max(
            0,
            int((now - component_ticket_created_at).total_seconds() // 60),
        )
        if age_minutes > 20:
            alerts.append(
                WechatRolloutAlert(
                    code="WECHAT_COMPONENT_TICKET_STALE",
                    severity="critical",
                    message="WeChat component ticket is older than 20 minutes.",
                    context={"ticket_age_minutes": age_minutes},
                )
            )
    if consecutive_component_refresh_failures >= 3:
        alerts.append(
            WechatRolloutAlert(
                code="WECHAT_COMPONENT_REFRESH_FAILURES_REPEATED",
                severity="critical",
                message="Component access-token refresh failures are repeating.",
                context={
                    "failure_count": consecutive_component_refresh_failures,
                    "token_kind": "component",
                },
            )
        )
    if consecutive_authorizer_refresh_failures >= 3:
        alerts.append(
            WechatRolloutAlert(
                code="WECHAT_AUTHORIZER_REFRESH_FAILURES_REPEATED",
                severity="critical",
                message="Authorizer access-token refresh failures are repeating.",
                context={
                    "failure_count": consecutive_authorizer_refresh_failures,
                    "token_kind": "authorizer",
                },
            )
        )
    if draft_sync_attempts_last_5m > 0:
        failure_rate = draft_sync_failures_last_5m / draft_sync_attempts_last_5m
        if failure_rate > 0.05:
            alerts.append(
                WechatRolloutAlert(
                    code="WECHAT_DRAFT_SYNC_FAILURE_RATE_HIGH",
                    severity="critical",
                    message="Draft sync failure rate exceeded five percent in five minutes.",
                    context={
                        "failure_count": draft_sync_failures_last_5m,
                        "attempt_count": draft_sync_attempts_last_5m,
                        "failure_rate": round(failure_rate, 4),
                        "window_minutes": 5,
                    },
                )
            )
    if conflicting_idempotency_reuses > 0:
        alerts.append(
            WechatRolloutAlert(
                code="WECHAT_DRAFT_SYNC_IDEMPOTENCY_CONFLICT",
                severity="high",
                message="One idempotency key was reused with a different request digest.",
                context={"conflict_count": conflicting_idempotency_reuses},
            )
        )
    if scope_denial_anomalies > 0:
        alerts.append(
            WechatRolloutAlert(
                code="WECHAT_SCOPE_DENIAL_ANOMALY",
                severity="high",
                message="Scope mismatch or cross-organization denial anomalies were observed.",
                context={"anomaly_count": scope_denial_anomalies},
            )
        )
    return alerts
