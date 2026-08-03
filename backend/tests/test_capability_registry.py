import pytest

from app.integrations.douyin_capabilities import DOUYIN_CAPABILITIES
from app.models import Account
from app.models.enums import Platform
from app.orchestrator.capability_registry import (
    resolve_capability_availability,
    runtime_capabilities,
)


@pytest.mark.asyncio
async def test_runtime_capabilities_include_role_scoped_tools(session, admin) -> None:
    capabilities = await runtime_capabilities(session, admin)

    tool_codes = {
        str(item["code"])
        for item in capabilities
        if item.get("kind") == "tool"
    }
    expert_codes = {
        str(item["code"])
        for item in capabilities
        if item.get("kind") == "expert"
    }

    assert tool_codes == {
        "account.data_context",
        "account.metrics_summary",
        "account.profile",
        "publish_package_prepare",
    }
    assert "01-positioning" in expert_codes


def test_registry_does_not_advertise_retired_data_scopes() -> None:
    capabilities = {item.key: item for item in DOUYIN_CAPABILITIES}

    assert "audience_insights" not in capabilities
    scopes = {scope for item in capabilities.values() for scope in item.user_scopes}
    assert "fans.data.bind" not in scopes


def test_capability_availability_is_derived_from_declared_context() -> None:
    assert resolve_capability_availability(
        enabled=True,
        role_allowed=True,
        required_context=("account",),
        account=None,
    ) == ("needs_input", "请选择账号后再使用该能力")

    disconnected = Account(
        org_id=1,
        platform=Platform.DOUYIN,
        nickname="disconnected",
        auth={"auth_status": "unauthorized"},
    )
    assert resolve_capability_availability(
        enabled=True,
        role_allowed=True,
        required_context=("account", "platform_connection"),
        account=disconnected,
    ) == ("needs_connection", "当前账号尚未完成平台连接")
