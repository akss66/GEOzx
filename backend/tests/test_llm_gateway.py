"""LLMGateway 测试：路由 / 兜底 / 成本记账（全 mock，无真实网络）。"""

import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.llm.adapters import CompletionResult
from app.llm.adapters.litellm import LiteLLMAdapter
from app.llm.cost import compute_cost
from app.llm.gateway import LLMError, LLMGateway, provider_for
from app.models import IntegrationConfig, LLMCall, ModelConfig, Org


class FakeAdapter:
    provider = "deepseek"

    def __init__(self, fail_models: set[str] | None = None) -> None:
        self.fail_models = fail_models or set()
        self.calls: list[str] = []

    async def complete(
        self, model: str, messages: list[dict], options: dict | None = None
    ) -> CompletionResult:
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


def test_provider_for_routes_litellm_prefix() -> None:
    assert provider_for("litellm:openai/gpt-4o-mini") == "litellm"
    assert provider_for("deepseek-chat") == "deepseek"


@pytest.mark.asyncio
async def test_litellm_adapter_uses_lazy_import(monkeypatch) -> None:
    captured: dict = {}

    async def fake_acompletion(model: str, messages: list[dict]) -> dict:
        captured["model"] = model
        captured["messages"] = messages
        return {
            "choices": [{"message": {"content": "lite reply"}}],
            "usage": {
                "prompt_tokens": 3,
                "completion_tokens": 4,
                "total_tokens": 7,
            },
        }

    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(acompletion=fake_acompletion))

    result = await LiteLLMAdapter().complete("litellm:openai/gpt-4o-mini", MSG)

    assert captured == {"model": "openai/gpt-4o-mini", "messages": MSG}
    assert result == CompletionResult(
        content="lite reply",
        model="litellm:openai/gpt-4o-mini",
        prompt_tokens=3,
        completion_tokens=4,
        total_tokens=7,
    )


@pytest.mark.asyncio
async def test_default_model_when_no_config(session) -> None:
    result, cost = await _gw(FakeAdapter()).chat(session, None, "x", MSG)
    assert result.model == "deepseek-chat"
    assert result.content == "reply from deepseek-chat"
    assert cost > 0
    count = await session.scalar(select(func.count()).select_from(LLMCall))
    assert count == 1


@pytest.mark.asyncio
async def test_gateway_records_the_authenticated_request_actor(session, admin) -> None:
    from app.core.request_context import reset_acting_user, set_acting_user

    actor_token = set_acting_user(admin.id)
    try:
        await _gw(FakeAdapter()).chat(session, admin.org_id, "x", MSG)
    finally:
        reset_acting_user(actor_token)

    call = await session.scalar(select(LLMCall).where(LLMCall.org_id == admin.org_id))
    assert call is not None
    assert call.created_by_id == admin.id


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
async def test_gateway_uses_provider_reference_and_route_options(session, monkeypatch) -> None:
    org = Org(name="Runtime config")
    session.add(org)
    await session.flush()
    session.add_all(
        [
            IntegrationConfig(
                org_id=org.id,
                provider="deepseek",
                enabled=True,
                credentials={"api_key_ref": "env:DEEPSEEK_API_KEY"},
            ),
            ModelConfig(
                org_id=org.id,
                agent_code="02-content-director",
                primary_model="deepseek-chat",
                params={
                    "routing_config": {
                        "temperature": 0.25,
                        "max_tokens": 2048,
                        "timeout_seconds": 45,
                    }
                },
            ),
        ]
    )
    await session.commit()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "runtime-secret")
    captured: dict = {}

    class RuntimeDeepSeekAdapter:
        provider = "deepseek"

        def __init__(self, api_key=None, base_url=None):
            captured["api_key"] = api_key
            captured["base_url"] = base_url

        async def complete(self, model, messages, options=None):
            captured["model"] = model
            captured["options"] = options
            return CompletionResult("ok", model, 1, 2, 3)

        async def stream(self, model, messages, options=None):
            if False:
                yield ""

    monkeypatch.setattr("app.llm.gateway.DeepSeekAdapter", RuntimeDeepSeekAdapter)

    result, _ = await LLMGateway().chat(
        session, org.id, "02-content-director", MSG
    )

    assert result.content == "ok"
    assert captured == {
        "api_key": "runtime-secret",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "options": {
            "temperature": 0.25,
            "max_tokens": 2048,
            "timeout_seconds": 45,
        },
    }


@pytest.mark.asyncio
async def test_gateway_rejects_a_disabled_provider(session) -> None:
    org = Org(name="Disabled provider")
    session.add(org)
    await session.flush()
    session.add(
        IntegrationConfig(org_id=org.id, provider="deepseek", enabled=False)
    )
    await session.commit()

    with pytest.raises(LLMError, match="all candidate models failed"):
        await LLMGateway().chat(session, org.id, "01-positioning", MSG)

    call = await session.scalar(
        select(LLMCall).where(LLMCall.org_id == org.id)
    )
    assert call is not None
    assert call.status == "error"
    assert "disabled" in (call.error or "")


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
