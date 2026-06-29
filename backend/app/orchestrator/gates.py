"""质量门策略：哪些门默认强制人工（可后续配置化）。

SPEC 5.5：脚本合规(3) / 发布前(5) / 大额投放(6) 默认强制人工，其余自动通过可打回。
"""

from app.models.enums import GateType

FORCED_GATES: set[GateType] = {
    GateType.SCRIPT_COMPLIANCE,
    GateType.PRE_PUBLISH_REVIEW,
    GateType.LARGE_AD_SPEND,
}


def is_forced(gate: GateType) -> bool:
    return gate in FORCED_GATES
