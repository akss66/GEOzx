"""Official Douyin capability bundles used by the internal operations product.

Application scopes are recorded after an administrator confirms that the
capability was approved in the Douyin console. User scopes come from the
account OAuth token and are therefore treated separately.
"""

from dataclasses import dataclass
from typing import Literal

DouyinCapabilityStatus = Literal[
    "ready",
    "needs_app_permission",
    "needs_account_authorization",
]


@dataclass(frozen=True)
class DouyinCapability:
    key: str
    label: str
    description: str
    app_scopes: tuple[str, ...]
    user_scopes: tuple[str, ...]


DOUYIN_CAPABILITIES: tuple[DouyinCapability, ...] = (
    DouyinCapability(
        key="profile",
        label="账号资料",
        description="读取账号昵称、头像与开放平台身份，作为账号矩阵基础。",
        app_scopes=("user_info",),
        user_scopes=("user_info",),
    ),
    DouyinCapability(
        key="h5_publish",
        label="H5 发布",
        description="由用户主动从网站应用唤起抖音并确认发布内容。",
        app_scopes=("h5.share", "open.get.ticket"),
        user_scopes=(),
    ),
    DouyinCapability(
        key="posting_feedback",
        label="投流回收",
        description="创建投流任务、绑定作品并查询基础信息，形成发布复盘闭环。",
        app_scopes=(
            "task.posting.create",
            "posting.behavior",
            "task.posting.user_verification",
        ),
        user_scopes=("posting.behavior",),
    ),
)

DOUYIN_CAPABILITY_BY_KEY = {item.key: item for item in DOUYIN_CAPABILITIES}


def diagnose_douyin_capabilities(
    *, app_scopes: list[str], account_scopes: list[str]
) -> list[dict[str, object]]:
    configured = set(app_scopes)
    granted = set(account_scopes)
    results: list[dict[str, object]] = []
    for capability in DOUYIN_CAPABILITIES:
        missing_app = [scope for scope in capability.app_scopes if scope not in configured]
        missing_user = [scope for scope in capability.user_scopes if scope not in granted]
        status: DouyinCapabilityStatus
        if missing_app:
            status = "needs_app_permission"
        elif missing_user:
            status = "needs_account_authorization"
        else:
            status = "ready"
        results.append(
            {
                "key": capability.key,
                "label": capability.label,
                "description": capability.description,
                "app_scopes": list(capability.app_scopes),
                "user_scopes": list(capability.user_scopes),
                "missing_app_scopes": missing_app,
                "missing_user_scopes": missing_user,
                "status": status,
            }
        )
    return results
