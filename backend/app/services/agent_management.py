"""Organization-scoped expert management policies.

Business owners configure responsibilities, prompt additions, tool permissions and
quality gates here. Provider/model credentials remain in the model infrastructure.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, ModelConfig
from app.models.enums import AgentCode, AutomationLevel

TOOL_CATALOG: dict[str, dict[str, str]] = {
    "task_planner": {
        "name": "任务规划",
        "description": "理解目标并生成可追踪的专家执行计划。",
    },
    "expert_dispatch": {
        "name": "专家调度",
        "description": "按目标选择必要专家并组织依赖关系。",
    },
    "acceptance_review": {
        "name": "成果验收判断",
        "description": "汇总专家成果并决定是否需要重跑。",
    },
    "account_context": {
        "name": "账号上下文",
        "description": "读取当前明确选择的项目、平台与账号信息。",
    },
    "profile_snapshot": {
        "name": "账号画像快照",
        "description": "读取账号定位与历史内容画像。",
    },
    "brief_builder": {
        "name": "内容任务整理",
        "description": "把运营目标整理为结构化内容任务。",
    },
    "compliance_precheck": {
        "name": "合规预检",
        "description": "在脚本进入后续制作前检查平台与内容风险。",
    },
    "style_prompt_builder": {
        "name": "视觉提示生成",
        "description": "把脚本要求整理为结构化视觉提示。",
    },
    "material_validator": {
        "name": "素材检查",
        "description": "检查素材完整性、格式与可制作条件。",
    },
    "publish_package_prepare": {
        "name": "发布包准备",
        "description": "整理标题、正文、话题、素材与发布设置。",
    },
    "review_metrics": {
        "name": "运营数据读取",
        "description": "读取当前账号作品指标并形成复盘输入。",
    },
    "knowledge_search": {
        "name": "知识库检索",
        "description": "读取当前客户与项目范围内的已授权知识。",
    },
}

QUALITY_GATE_CATALOG: dict[str, dict[str, Any]] = {
    "positioning_review": {
        "name": "定位确认",
        "description": "账号定位写入正式工作流前由人工确认。",
        "forced": False,
    },
    "topic_review": {
        "name": "选题确认",
        "description": "选题与账号方向进入脚本前由人工确认。",
        "forced": False,
    },
    "script_compliance": {
        "name": "脚本合规",
        "description": "脚本通过合规检查后才能进入制作。",
        "forced": True,
    },
    "final_video_review": {
        "name": "成片确认",
        "description": "成片进入发布准备前由人工确认。",
        "forced": False,
    },
    "pre_publish_review": {
        "name": "发布前确认",
        "description": "任何平台发布动作前必须人工确认。",
        "forced": True,
    },
    "large_ad_spend": {
        "name": "大额投放确认",
        "description": "高预算投放动作必须人工确认。",
        "forced": True,
    },
}

_TOOLS_BY_AGENT: dict[str, list[str]] = {
    AgentCode.DECISION.value: ["task_planner", "expert_dispatch", "acceptance_review"],
    AgentCode.POSITIONING.value: ["account_context", "profile_snapshot", "knowledge_search"],
    AgentCode.CONTENT_DIRECTOR.value: [
        "account_context",
        "brief_builder",
        "compliance_precheck",
        "knowledge_search",
    ],
    AgentCode.ART_DIRECTOR.value: ["account_context", "style_prompt_builder", "knowledge_search"],
    AgentCode.VIDEO_CREATOR.value: ["account_context", "material_validator", "knowledge_search"],
    AgentCode.EDITOR.value: ["account_context", "material_validator", "knowledge_search"],
    AgentCode.OPERATOR.value: [
        "account_context",
        "publish_package_prepare",
        "review_metrics",
        "knowledge_search",
    ],
    AgentCode.ADVERTISER.value: ["account_context", "review_metrics", "knowledge_search"],
    AgentCode.CUSTOMER_SERVICE.value: ["account_context", "review_metrics", "knowledge_search"],
}

_GATES_BY_AGENT: dict[str, list[str]] = {
    AgentCode.DECISION.value: [],
    AgentCode.POSITIONING.value: ["positioning_review"],
    AgentCode.CONTENT_DIRECTOR.value: ["topic_review", "script_compliance"],
    AgentCode.ART_DIRECTOR.value: [],
    AgentCode.VIDEO_CREATOR.value: ["final_video_review"],
    AgentCode.EDITOR.value: ["final_video_review"],
    AgentCode.OPERATOR.value: ["pre_publish_review"],
    AgentCode.ADVERTISER.value: ["large_ad_spend"],
    AgentCode.CUSTOMER_SERVICE.value: [],
}

_DEFAULT_PERMISSION_BY_TOOL = {
    "brief_builder": "auto",
    "compliance_precheck": "confirm",
    "material_validator": "confirm",
    "publish_package_prepare": "confirm",
}


def default_business_config(code: AgentCode | str, responsibility: str = "") -> dict[str, Any]:
    value = code.value if isinstance(code, AgentCode) else code
    tools = _TOOLS_BY_AGENT.get(value, [])
    return {
        "enabled": True,
        "responsibility": responsibility,
        "system_prompt": "",
        "tool_permissions": {
            tool: _DEFAULT_PERMISSION_BY_TOOL.get(tool, "auto") for tool in tools
        },
        "quality_gates": list(_GATES_BY_AGENT.get(value, [])),
    }


async def get_business_config(
    session: AsyncSession | None,
    org_id: int,
    code: AgentCode | str,
    *,
    responsibility: str = "",
) -> dict[str, Any]:
    value = code.value if isinstance(code, AgentCode) else code
    config = default_business_config(value, responsibility)
    if session is None:
        return config
    row = await session.scalar(
        select(ModelConfig).where(ModelConfig.org_id == org_id, ModelConfig.agent_code == value)
    )
    stored = ((row.params or {}).get("business_config") if row is not None else None) or {}
    if isinstance(stored, dict):
        for key in ("enabled", "responsibility", "system_prompt", "quality_gates"):
            if key in stored:
                config[key] = deepcopy(stored[key])
        permissions = stored.get("tool_permissions")
        if isinstance(permissions, dict):
            config["tool_permissions"] = deepcopy(permissions)
    return config


async def require_agent_enabled(
    session: AsyncSession, org_id: int, code: AgentCode | str
) -> None:
    config = await get_business_config(session, org_id, code)
    if not config["enabled"]:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该专家已停用")


def validate_business_config(
    code: AgentCode | str,
    *,
    tool_permissions: dict[str, str],
    quality_gates: list[str],
) -> None:
    value = code.value if isinstance(code, AgentCode) else code
    allowed_tools = set(_TOOLS_BY_AGENT.get(value, []))
    allowed_gates = set(_GATES_BY_AGENT.get(value, []))
    unknown_tools = set(tool_permissions) - allowed_tools
    unknown_gates = set(quality_gates) - allowed_gates
    missing_forced_gates = {
        gate
        for gate in allowed_gates
        if QUALITY_GATE_CATALOG[gate]["forced"] and gate not in quality_gates
    }
    if unknown_tools or unknown_gates or missing_forced_gates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "unknown_tools": sorted(unknown_tools),
                "unknown_quality_gates": sorted(unknown_gates),
                "missing_forced_quality_gates": sorted(missing_forced_gates),
            },
        )


async def save_business_config(
    session: AsyncSession,
    *,
    org_id: int,
    user_id: int,
    code: AgentCode,
    enabled: bool,
    responsibility: str,
    system_prompt: str,
    tool_permissions: dict[str, str],
    quality_gates: list[str],
) -> ModelConfig:
    validate_business_config(
        code,
        tool_permissions=tool_permissions,
        quality_gates=quality_gates,
    )
    row = await session.scalar(
        select(ModelConfig).where(
            ModelConfig.org_id == org_id,
            ModelConfig.agent_code == code.value,
        )
    )
    if row is None:
        row = ModelConfig(org_id=org_id, agent_code=code.value, primary_model="deepseek-chat")
        session.add(row)
    params = dict(row.params or {})
    params["business_config"] = {
        "enabled": enabled,
        "responsibility": responsibility.strip(),
        "system_prompt": system_prompt.strip(),
        "tool_permissions": dict(tool_permissions),
        "quality_gates": list(dict.fromkeys(quality_gates)),
    }
    params.setdefault("automation_level", AutomationLevel.CONFIRM.value)
    row.params = params
    session.add(
        Event(
            type="expert.management.updated",
            payload={
                "org_id": org_id,
                "agent_code": code.value,
                "updated_by": user_id,
                "enabled": enabled,
                "tool_permissions": dict(tool_permissions),
                "quality_gates": list(dict.fromkeys(quality_gates)),
            },
        )
    )
    await session.commit()
    await session.refresh(row)
    return row


def available_tools(code: AgentCode | str) -> list[dict[str, str]]:
    value = code.value if isinstance(code, AgentCode) else code
    return [
        {"code": tool, **TOOL_CATALOG[tool]}
        for tool in _TOOLS_BY_AGENT.get(value, [])
    ]


def available_quality_gates(code: AgentCode | str) -> list[dict[str, Any]]:
    value = code.value if isinstance(code, AgentCode) else code
    return [
        {"code": gate, **QUALITY_GATE_CATALOG[gate]}
        for gate in _GATES_BY_AGENT.get(value, [])
    ]


def quality_gate_labels(codes: list[str]) -> list[str]:
    return [QUALITY_GATE_CATALOG[code]["name"] for code in codes if code in QUALITY_GATE_CATALOG]


async def apply_management_policies(
    session: AsyncSession,
    org_id: int,
    steps: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Filter disabled experts and attach executable tool/gate policies to plan steps."""

    managed_steps: list[dict[str, Any]] = []
    quality_gates: list[str] = []
    for raw_step in steps:
        code = str(raw_step.get("agent_code") or "")
        config = await get_business_config(session, org_id, code)
        if not config["enabled"]:
            continue
        step = dict(raw_step)
        permissions = config["tool_permissions"]
        tool_codes = [
            tool
            for tool in step.get("tool_codes", [])
            if permissions.get(tool, "auto") != "disabled"
        ]
        step["tool_codes"] = tool_codes
        step["tool_permissions"] = {
            tool: permissions.get(tool, "auto") for tool in tool_codes
        }
        step["human_gate"] = any(
            mode in {"confirm", "manual"} for mode in step["tool_permissions"].values()
        )
        step["quality_gates"] = list(config["quality_gates"])
        quality_gates.extend(config["quality_gates"])
        managed_steps.append(step)
    valid_step_ids = {str(step.get("id")) for step in managed_steps}
    for step in managed_steps:
        step["depends_on"] = [
            dependency
            for dependency in step.get("depends_on", [])
            if dependency in valid_step_ids
        ]
    return managed_steps, list(dict.fromkeys(quality_gates))
