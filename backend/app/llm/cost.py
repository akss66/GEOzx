"""Estimate model cost from provider prices in USD per million tokens.

The adapters currently expose aggregate prompt tokens without a cache-hit split,
so input is priced at the cache-miss rate. This intentionally avoids
under-reporting operational cost.
"""

# USD / 1,000,000 tokens
PRICING: dict[str, dict[str, float]] = {
    "deepseek-v4-pro": {"input": 0.435, "output": 0.87},
    "deepseek-v4-flash": {"input": 0.14, "output": 0.28},
    # DeepSeek keeps these compatibility aliases mapped to V4 Flash.
    "deepseek-chat": {"input": 0.14, "output": 0.28},
    "deepseek-reasoner": {"input": 0.14, "output": 0.28},
}


def compute_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    price = PRICING.get(model)
    if price is None:
        return 0.0
    return prompt_tokens / 1_000_000 * price["input"] + (
        completion_tokens / 1_000_000 * price["output"]
    )
