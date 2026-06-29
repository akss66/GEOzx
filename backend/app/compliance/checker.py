"""合规检测服务：脚本敏感词 / 绝对化用语 / 限流风险词预检。

v1 用内置词库做规则匹配（不调外部 API），给人工质量门提供参考。
后续可扩展：接第三方合规 API、原创度比对、可配置词库（IntegrationConfig）。
"""

from app.models.enums import ComplianceRisk

# 高危违禁/极限词（广告法绝对化用语等）→ block
_BLOCK_WORDS: dict[str, str] = {
    "最佳": "绝对化用语",
    "最好": "绝对化用语",
    "第一": "绝对化用语",
    "唯一": "绝对化用语",
    "国家级": "绝对化用语",
    "100%": "绝对化用语",
    "根治": "医疗violation",
    "特效": "夸大功效",
}

# 疑似风险/限流词 → warn（需人工确认）
_WARN_WORDS: dict[str, str] = {
    "免费": "诱导风险",
    "微信": "导流风险",
    "加我": "导流风险",
    "私聊": "导流风险",
    "稳赚": "夸大收益",
    "暴富": "夸大收益",
    "秒杀": "营销夸张",
}

# 脚本 payload 里参与文本检测的字段
_TEXT_FIELDS = ("title", "hook", "scenes", "bgm_suggestion")


def _collect_text(payload: dict) -> str:
    """从脚本 payload 抽取所有文本拼成一段，供词库扫描。"""
    parts: list[str] = []
    for field in _TEXT_FIELDS:
        val = payload.get(field)
        if isinstance(val, str):
            parts.append(val)
        elif isinstance(val, list):
            parts.extend(str(x) for x in val)
    return "\n".join(parts)


def check_script(payload: dict) -> tuple[ComplianceRisk, str, list[dict]]:
    """检测脚本 payload，返回 (风险等级, 概述, 命中明细)。

    命中任一 block 词 → BLOCK；仅命中 warn 词 → WARN；都没有 → PASS。
    """
    text = _collect_text(payload)
    findings: list[dict] = []

    for word, category in _BLOCK_WORDS.items():
        if word in text:
            findings.append({"word": word, "category": category, "level": "block"})
    for word, category in _WARN_WORDS.items():
        if word in text:
            findings.append({"word": word, "category": category, "level": "warn"})

    if any(f["level"] == "block" for f in findings):
        risk = ComplianceRisk.BLOCK
        n_block = sum(1 for f in findings if f["level"] == "block")
        summary = f"检出 {n_block} 处高危违禁/绝对化用语，建议打回"
    elif findings:
        risk = ComplianceRisk.WARN
        summary = f"检出 {len(findings)} 处疑似风险词，请人工确认"
    else:
        risk = ComplianceRisk.PASS
        summary = "未检出敏感词，建议通过"

    return risk, summary, findings
