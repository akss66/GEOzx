"""Runtime capability catalog exposed to the main Agent control plane.

Experts are the first capability kind. MCP servers and tools can be added here
later without changing the LangGraph decide/dispatch/observe loop.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AgentCode
from app.services.agent_management import available_tools, get_business_config

_EXPERT_CODES = (
    AgentCode.POSITIONING,
    AgentCode.CONTENT_DIRECTOR,
    AgentCode.ART_DIRECTOR,
    AgentCode.VIDEO_CREATOR,
    AgentCode.EDITOR,
    AgentCode.OPERATOR,
    AgentCode.ADVERTISER,
    AgentCode.CUSTOMER_SERVICE,
)

_EXPERT_META = {
    AgentCode.POSITIONING: ("账号定位专家", "分析账号定位、目标人群和平台匹配度"),
    AgentCode.CONTENT_DIRECTOR: ("编导文案专家", "生成内容策略、选题、脚本和表达结构"),
    AgentCode.ART_DIRECTOR: ("美术提示词专家", "定义视觉风格、画面要求和生成提示词"),
    AgentCode.VIDEO_CREATOR: ("视频创作专家", "规划视频素材、镜头和生成路径"),
    AgentCode.EDITOR: ("剪辑专家", "设计剪辑节奏、字幕和平台成片规格"),
    AgentCode.OPERATOR: ("账号运营专家", "处理发布准备、运营复盘和增长建议"),
    AgentCode.ADVERTISER: ("投放专家", "评估投放策略、预算边界和增长动作"),
    AgentCode.CUSTOMER_SERVICE: ("客服反馈专家", "分析评论反馈、服务线索和用户问题"),
}


async def runtime_capabilities(
    session: AsyncSession,
    org_id: int,
) -> list[dict[str, Any]]:
    """Return enabled, organization-scoped capabilities for main-Agent routing."""

    capabilities: list[dict[str, Any]] = []
    for code in _EXPERT_CODES:
        name, default_description = _EXPERT_META[code]
        config = await get_business_config(
            session,
            org_id,
            code,
            responsibility=default_description,
        )
        if not config["enabled"]:
            continue
        permissions = dict(config.get("tool_permissions") or {})
        tools = [
            {
                "code": tool["code"],
                "name": tool["name"],
                "permission_mode": permissions.get(tool["code"], "auto"),
            }
            for tool in available_tools(code)
        ]
        capabilities.append(
            {
                "kind": "expert",
                "code": code.value,
                "name": name,
                "description": config.get("responsibility") or default_description,
                "tools": tools,
                "delegation": "main_agent_only",
            }
        )
    return capabilities
