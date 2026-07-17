"""目标驱动的主 Agent 计划器。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import extract_json
from app.llm.gateway import gateway
from app.models.enums import AgentCode, BrainTaskType
from app.services.agent_management import apply_management_policies


@dataclass(frozen=True)
class PlanningDecision:
    steps: list[dict[str, Any]]
    summary: str
    source: str
    quality_gates: list[str]


_CATALOG: dict[str, dict[str, Any]] = {
    AgentCode.POSITIONING.value: {
        "id": "step-positioning",
        "agent_code": AgentCode.POSITIONING.value,
        "agent_name": "账号定位专家",
        "phase": "定位校准",
        "intent": "分析账号定位、目标人群、内容支柱与当前目标的匹配度。",
        "expected_output": "定位策略",
        "risk_level": "low",
        "execution_kind": "account_diagnosis",
        "human_gate": False,
        "tool_codes": ["account_context", "profile_snapshot"],
    },
    AgentCode.CONTENT_DIRECTOR.value: {
        "id": "step-script",
        "agent_code": AgentCode.CONTENT_DIRECTOR.value,
        "agent_name": "编导文案专家",
        "phase": "内容策划",
        "intent": "根据目标产出选题、结构、脚本或平台表达方案。",
        "expected_output": "脚本包",
        "risk_level": "medium",
        "execution_kind": "content_generation",
        "human_gate": True,
        "tool_codes": ["brief_builder", "compliance_precheck"],
    },
    AgentCode.ART_DIRECTOR.value: {
        "id": "step-art",
        "agent_code": AgentCode.ART_DIRECTOR.value,
        "agent_name": "美术指导提示词专家",
        "phase": "视觉设计",
        "intent": "把内容方向转成视觉风格、画面要求和结构化提示词。",
        "expected_output": "视觉风格书与提示词",
        "risk_level": "low",
        "execution_kind": "creative_generation",
        "human_gate": False,
        "tool_codes": ["style_prompt_builder"],
    },
    AgentCode.VIDEO_CREATOR.value: {
        "id": "step-video",
        "agent_code": AgentCode.VIDEO_CREATOR.value,
        "agent_name": "视频创作专家",
        "phase": "视频制作",
        "intent": "根据脚本和视觉要求准备镜头、素材或视频生成任务。",
        "expected_output": "视频素材",
        "risk_level": "medium",
        "execution_kind": "asset_preparation",
        "human_gate": True,
        "tool_codes": ["material_validator"],
    },
    AgentCode.EDITOR.value: {
        "id": "step-editing",
        "agent_code": AgentCode.EDITOR.value,
        "agent_name": "剪辑专家",
        "phase": "成片剪辑",
        "intent": "处理素材、字幕、节奏和平台比例，形成成片交付要求。",
        "expected_output": "成片",
        "risk_level": "medium",
        "execution_kind": "asset_preparation",
        "human_gate": True,
        "tool_codes": ["material_validator"],
    },
    AgentCode.OPERATOR.value: {
        "id": "step-operation",
        "agent_code": AgentCode.OPERATOR.value,
        "agent_name": "账号运营专家",
        "phase": "运营推进",
        "intent": "完成发布准备、数据复盘或下一轮运营动作建议。",
        "expected_output": "发布计划与复盘建议",
        "risk_level": "medium",
        "execution_kind": "publish_readiness",
        "human_gate": True,
        "tool_codes": ["publish_package_prepare", "review_metrics"],
    },
}

_ORDER = list(_CATALOG)


class BrainPlanner:
    async def plan_selected(
        self,
        session: AsyncSession,
        org_id: int,
        codes: list[str],
        summary: str,
    ) -> PlanningDecision:
        selected = _valid_codes(codes)
        if not selected:
            return PlanningDecision([], summary, "intent", [])
        steps, quality_gates = await apply_management_policies(
            session, org_id, _build_steps(selected)
        )
        return PlanningDecision(steps, summary, "intent", quality_gates)

    async def plan(
        self,
        session: AsyncSession,
        org_id: int,
        goal: str,
        task_type: BrainTaskType,
    ) -> PlanningDecision:
        fallback_codes = _fallback_codes(goal, task_type)
        try:
            result, _cost = await gateway.chat(
                session,
                org_id,
                AgentCode.DECISION.value,
                [
                    {"role": "system", "content": _planner_system_prompt()},
                    {"role": "user", "content": goal},
                ],
            )
            payload = extract_json(result.content)
            selected = _valid_codes(payload.get("selected_agent_codes"))
            if not selected:
                raise ValueError("主 Agent 未选择有效专家")
            summary = str(payload.get("summary") or "主 Agent 已按目标选择所需专家。")
            steps, quality_gates = await apply_management_policies(
                session, org_id, _build_steps(selected)
            )
            return PlanningDecision(steps, summary, "model", quality_gates)
        except Exception:  # noqa: BLE001 - 模型规划失败必须有可执行兜底
            steps, quality_gates = await apply_management_policies(
                session, org_id, _build_steps(fallback_codes)
            )
            return PlanningDecision(
                steps,
                "主 Agent 已根据目标选择必要专家；执行中可继续调整。",
                "fallback",
                quality_gates,
            )


def _planner_system_prompt() -> str:
    capabilities = "\n".join(
        f"- {code}: {spec['agent_name']}，{spec['intent']}" for code, spec in _CATALOG.items()
    )
    return f"""你是同舟行运营大脑的主 Agent。根据用户目标只选择真正需要的专家，禁止固定全流程调用。

可用专家：
{capabilities}

只输出 JSON：
{{"selected_agent_codes":["01-positioning"],"summary":"一句自然语言说明"}}

规则：
1. 普通账号诊断不调用视频制作或剪辑专家。
2. 只生成脚本时不调用美术、视频、剪辑或运营专家。
3. 只有目标明确要求对应交付物时才选择美术、视频、剪辑或发布准备能力。
4. 选择结果必须使用上面列出的 code，不能发明专家。"""


def _valid_codes(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    requested = [str(item) for item in value if str(item) in _CATALOG]
    return [code for code in _ORDER if code in requested]


def _fallback_codes(goal: str, task_type: BrainTaskType) -> list[str]:
    text = goal.lower()
    selected: set[str] = set()

    if task_type == BrainTaskType.ACCOUNT_DIAGNOSIS or _has(text, "定位", "人群", "人设", "账号"):
        selected.add(AgentCode.POSITIONING.value)
    if _has(text, "内容", "选题", "脚本", "文案", "标题", "短视频"):
        selected.update({AgentCode.POSITIONING.value, AgentCode.CONTENT_DIRECTOR.value})
    if _has(text, "视觉", "画面", "提示词", "美术", "封面"):
        selected.add(AgentCode.ART_DIRECTOR.value)
    if _has(text, "视频素材", "视频生成", "制作视频", "镜头素材"):
        selected.update(
            {
                AgentCode.CONTENT_DIRECTOR.value,
                AgentCode.ART_DIRECTOR.value,
                AgentCode.VIDEO_CREATOR.value,
            }
        )
    if _has(text, "剪辑", "成片", "字幕", "转场"):
        selected.add(AgentCode.EDITOR.value)
    if task_type in {BrainTaskType.REVIEW_OPTIMIZATION, BrainTaskType.MATRIX_DISTRIBUTION} or _has(
        text, "发布", "排期", "复盘", "优化", "分发", "矩阵", "数据", "完播率", "互动"
    ):
        selected.add(AgentCode.OPERATOR.value)

    if not selected:
        selected.update({AgentCode.POSITIONING.value, AgentCode.CONTENT_DIRECTOR.value})
    return [code for code in _ORDER if code in selected]


def _has(text: str, *keywords: str) -> bool:
    return any(keyword in text for keyword in keywords)


def _build_steps(codes: list[str]) -> list[dict[str, Any]]:
    selected = [code for code in _ORDER if code in codes]
    steps: list[dict[str, Any]] = []
    for code in selected:
        spec = dict(_CATALOG[code])
        spec["status"] = "planned"
        spec["depends_on"] = _dependencies(code, selected)
        steps.append(spec)
    return steps


def _dependencies(code: str, selected: list[str]) -> list[str]:
    ids = {agent_code: _CATALOG[agent_code]["id"] for agent_code in selected}
    if code == AgentCode.CONTENT_DIRECTOR.value and AgentCode.POSITIONING.value in ids:
        return [ids[AgentCode.POSITIONING.value]]
    if code == AgentCode.ART_DIRECTOR.value:
        upstream = (
            AgentCode.CONTENT_DIRECTOR.value
            if AgentCode.CONTENT_DIRECTOR.value in ids
            else AgentCode.POSITIONING.value
        )
        return [ids[upstream]] if upstream in ids else []
    if code == AgentCode.VIDEO_CREATOR.value:
        return [
            ids[item]
            for item in (AgentCode.CONTENT_DIRECTOR.value, AgentCode.ART_DIRECTOR.value)
            if item in ids
        ]
    if code == AgentCode.EDITOR.value and AgentCode.VIDEO_CREATOR.value in ids:
        return [ids[AgentCode.VIDEO_CREATOR.value]]
    if code == AgentCode.OPERATOR.value:
        for upstream in reversed(_ORDER[:-1]):
            if upstream in ids:
                return [ids[upstream]]
    return []


brain_planner = BrainPlanner()
