"""LLMGateway 测试：路由 / 兜底 / 成本记账（全 mock，无真实网络）。"""

import pytest
from sqlalchemy import func, select

from app.llm.adapters import CompletionResult
from app.llm.cost import compute_cost
from app.llm.gateway import LLMError, LLMGateway
from app.models import LLMCall, ModelConfig, Org


class FakeAdapter:
    provider = "deepseek"

    def __init__(self, fail_models: set[str] | None = None) -> None:
        self.fail_models = fail_models or set()
        self.calls: list[str] = []

    async def complete(self, model: str, messages: list[dict]) -> CompletionResult:
        self.calls.append(model)
        if model in self.fail_models:
            raise RuntimeError(f"boom {model}")
        return CompletionResult(
            content=f"reply from {model}",
            model=model,
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
        )


def _gw(adapter: FakeAdapter) -> LLMGateway:
    return LLMGateway(adapters={"deepseek": adapter})


MSG = [{"role": "user", "content": "hi"}]


def test_compute_cost() -> None:
    assert round(compute_cost("deepseek-chat", 1_000_000, 1_000_000), 4) == 1.37
    assert compute_cost("unknown-model", 100, 100) == 0.0


@pytest.mark.asyncio
async def test_default_model_when_no_config(session) -> None:
    result, cost = await _gw(FakeAdapter()).chat(session, None, "x", MSG)
    assert result.model == "deepseek-chat"
    assert result.content == "reply from deepseek-chat"
    assert cost > 0
    count = await session.scalar(select(func.count()).select_from(LLMCall))
    assert count == 1


@pytest.mark.asyncio
async def test_routing_uses_model_config(session) -> None:
    org = Org(name="O")
    session.add(org)
    await session.flush()
    session.add(ModelConfig(org_id=org.id, agent_code="01", primary_model="deepseek-reasoner"))
    await session.commit()

    result, _ = await _gw(FakeAdapter()).chat(session, org.id, "01", MSG)
    assert result.model == "deepseek-reasoner"


@pytest.mark.asyncio
async def test_fallback_on_primary_failure(session) -> None:
    org = Org(name="O")
    session.add(org)
    await session.flush()
    session.add(
        ModelConfig(
            org_id=org.id,
            agent_code="01",
            primary_model="bad",
            fallback_model="deepseek-chat",
        )
    )
    await session.commit()

    adapter = FakeAdapter(fail_models={"bad"})
    result, _ = await _gw(adapter).chat(session, org.id, "01", MSG)

    assert result.model == "deepseek-chat"
    assert adapter.calls == ["bad", "deepseek-chat"]
    rows = (await session.scalars(select(LLMCall))).all()
    assert sorted(r.status for r in rows) == ["error", "ok"]


@pytest.mark.asyncio
async def test_all_candidates_fail_raises(session) -> None:
    org = Org(name="O")
    session.add(org)
    await session.flush()
    session.add(
        ModelConfig(org_id=org.id, agent_code="01", primary_model="bad", fallback_model="bad2")
    )
    await session.commit()

    with pytest.raises(LLMError):
        await _gw(FakeAdapter(fail_models={"bad", "bad2"})).chat(session, org.id, "01", MSG)
