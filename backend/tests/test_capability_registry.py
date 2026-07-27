import pytest

from app.integrations.douyin_capabilities import DOUYIN_CAPABILITIES
from app.orchestrator.capability_registry import runtime_capabilities


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
    }
    assert "01-positioning" in expert_codes


def test_registry_does_not_advertise_retired_data_scopes() -> None:
    capabilities = {item.key: item for item in DOUYIN_CAPABILITIES}

    assert "audience_insights" not in capabilities
    scopes = {scope for item in capabilities.values() for scope in item.user_scopes}
    assert "fans.data.bind" not in scopes
