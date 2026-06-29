"""成本估算：按模型的近似单价（USD / 1M tokens）计算调用成本。

价格随供应商调整，后续可改为配置化/入库。当前为公开近似价。
"""

# USD / 1,000,000 tokens
PRICING: dict[str, dict[str, float]] = {
    "deepseek-chat": {"input": 0.27, "output": 1.10},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19},
}


def compute_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    price = PRICING.get(model)
    if price is None:
        return 0.0
    return prompt_tokens / 1_000_000 * price["input"] + (
        completion_tokens / 1_000_000 * price["output"]
    )
