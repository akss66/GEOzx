"""LLMGateway 测试：路由 / 兜底 / 成本记账（全 mock，无真实网络）。"""

import sys
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select

from app.config import settings
from app.core.runtime_failures import FailureDisposition, classify_runtime_failure
from app.llm.adapters import CompletionResult
from app.llm.adapters.litellm import LiteLLMAdapter
from app.llm.cost import compute_cost
from app.llm.gateway import (
    LLMCallContext,
    LLMError,
    LLMGateway,
    bind_llm_call_context,
    provider_for,
)
from app.models import IntegrationConfig, LLMCall, ModelConfig, ModelProvider, Org
from app.services.model_provider_registry import replace_provider_key


class FakeAdapter:
    provider = "deepseek"

    def __init__(self, fail_models: set[str] | None = None) -> None:
        self.fail_models = fail_models or set()
        self.calls: list[str] = []
        self.options: list[dict] = []

    async def complete(
        self, model: str, messages: list[dict], options: dict | None = None
    ) -> CompletionResult:
        self.calls.append(model)
        self.options.append(dict(options or {}))
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


@pytest.mark.parametrize(
    ("model", "prompt_tokens", "completion_tokens", "expected"),
    [
        ("deepseek-v4-pro", 1_000_000, 1_000_000, 1.305),
        ("deepseek-v4-flash", 1_000_000, 1_000_000, 0.42),
    ],
)
def test_compute_cost_supports_current_deepseek_v4_models(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    expected: float,
) -> None:
    assert compute_cost(model, prompt_tokens, completion_tokens) == pytest.approx(expected)


@pytest.fixture
def encryption_key(monkeypatch):
    monkeypatch.setattr(
        settings,
        "credential_encryption_key",
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )


def _provider(
    *,
    org_id: int,
    code: str,
    base_url: str,
    models: list[str],
    enabled: bool = True,
    verification_status: str = "verified",
) -> ModelProvider:
    return ModelProvider(
        org_id=org_id,
        code=code,
        display_name=code.upper(),
        provider_type="custom_openai",
        template_code=None,
        protocol="openai_compatible",
        base_url=base_url,
        enabled=enabled,
        credential_source="none",
        verification_status=verification_status,
        models=models,
    )


def test_compute_cost() -> None:
    assert round(compute_cost("deepseek-chat", 1_000_000, 1_000_000), 4) == 0.42
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
async def test_gateway_records_prompt_scope_budget_and_trace_metadata(session, admin) -> None:
    adapter = FakeAdapter()
    context = LLMCallContext(
        task_id=41,
        invocation_id=73,
        trace_id="trace-abc",
        prompt_id="expert.01-positioning",
        prompt_version="1.0.0",
        prompt_hash="a" * 64,
        prompt_schema_version="positioning-strategy/v1",
        scope={"org_id": admin.org_id, "project_id": 9, "account_id": 12},
        budget={"max_tokens": 2048, "max_cost_usd": 0.2},
        response_format={"type": "json_object"},
    )

    with bind_llm_call_context(context):
        await _gw(adapter).chat(session, admin.org_id, "01-positioning", MSG)

    call = await session.scalar(select(LLMCall).where(LLMCall.org_id == admin.org_id))
    assert call is not None
    assert call.task_id == 41
    assert call.invocation_id == 73
    assert call.trace_id == "trace-abc"
    assert call.prompt_id == "expert.01-positioning"
    assert call.prompt_version == "1.0.0"
    assert call.prompt_hash == "a" * 64
    assert call.prompt_schema_version == "positioning-strategy/v1"
    assert call.scope == {"org_id": admin.org_id, "project_id": 9, "account_id": 12}
    assert call.budget == {"max_tokens": 2048, "max_cost_usd": 0.2}
    assert adapter.options[0]["response_format"] == {"type": "json_object"}


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


@pytest.mark.parametrize(
    ("provider_failure", "expected_status", "expected_kind"),
    [
        (
            httpx.HTTPStatusError(
                "rate limited: sk-provider-secret",
                request=httpx.Request(
                    "POST",
                    "https://provider.invalid/chat?api_key=sk-provider-secret",
                ),
                response=httpx.Response(
                    429,
                    text='{"error":"provider-secret-response-body"}',
                ),
            ),
            429,
            "http",
        ),
        (
            httpx.HTTPStatusError(
                "provider unavailable",
                request=httpx.Request("POST", "https://provider.invalid/chat"),
                response=httpx.Response(503),
            ),
            503,
            "http",
        ),
        (
            httpx.ReadTimeout(
                "provider-secret-timeout",
                request=httpx.Request("POST", "https://provider.invalid/chat"),
            ),
            None,
            "timeout",
        ),
    ],
    ids=["429", "503", "read-timeout"],
)
@pytest.mark.asyncio
async def test_gateway_preserves_safe_retry_metadata_as_the_llm_error_cause(
    session,
    provider_failure,
    expected_status,
    expected_kind,
) -> None:
    class FailingAdapter(FakeAdapter):
        async def complete(self, *_args, **_kwargs):
            raise provider_failure

    with pytest.raises(LLMError) as caught:
        await _gw(FailingAdapter()).chat(session, None, "x", MSG)

    error = caught.value
    cause = error.__cause__
    assert cause is not None
    assert getattr(cause, "status_code", None) == expected_status
    assert getattr(cause, "failure_kind", None) == expected_kind
    assert classify_runtime_failure(error) is FailureDisposition.RETRYABLE
    visible_error = f"{error} {cause}"
    assert "sk-provider-secret" not in visible_error
    assert "provider-secret-response-body" not in visible_error
    assert "provider-secret-timeout" not in visible_error


@pytest.mark.asyncio
async def test_gateway_routes_provider_backed_calls_with_org_scoped_credentials(
    session, encryption_key, monkeypatch
) -> None:
    org_a = Org(name="Org A")
    org_b = Org(name="Org B")
    session.add_all([org_a, org_b])
    await session.flush()
    provider_a = _provider(
        org_id=org_a.id,
        code="openai",
        base_url="https://api.openai.com/v1",
        models=["gpt-4.1-mini"],
    )
    provider_b = _provider(
        org_id=org_b.id,
        code="openai",
        base_url="https://api.openai.com/v1",
        models=["gpt-4.1-mini"],
    )
    replace_provider_key(provider_a, "sk-org-a-provider-key-1111")
    replace_provider_key(provider_b, "sk-org-b-provider-key-2222")
    provider_a.verification_status = "verified"
    provider_b.verification_status = "verified"
    session.add_all([provider_a, provider_b])
    await session.flush()
    session.add_all(
        [
            ModelConfig(
                org_id=org_a.id,
                agent_code="01",
                primary_provider_id=provider_a.id,
                primary_model="gpt-4.1-mini",
            ),
            ModelConfig(
                org_id=org_b.id,
                agent_code="01",
                primary_provider_id=provider_b.id,
                primary_model="gpt-4.1-mini",
            ),
        ]
    )
    await session.commit()

    captured: list[tuple[str, str | None, str | None, str]] = []

    class RuntimeAdapter:
        provider = "openai"

        def __init__(self, provider_code, api_key=None, base_url=None):
            self.provider_code = provider_code
            self.api_key = api_key
            self.base_url = base_url

        async def complete(self, model, messages, options=None):
            captured.append((self.provider_code, self.api_key, self.base_url, model))
            return CompletionResult(f"reply via {self.api_key}", model, 1, 2, 3)

        async def stream(self, model, messages, options=None):
            if False:
                yield ""

    monkeypatch.setattr("app.llm.gateway.OpenAICompatibleAdapter", RuntimeAdapter)

    gateway = LLMGateway()
    result_a, _ = await gateway.chat(session, org_a.id, "01", MSG)
    result_b, _ = await gateway.chat(session, org_b.id, "01", MSG)

    assert result_a.content == "reply via sk-org-a-provider-key-1111"
    assert result_b.content == "reply via sk-org-b-provider-key-2222"
    assert captured == [
        ("openai", "sk-org-a-provider-key-1111", "https://api.openai.com/v1", "gpt-4.1-mini"),
        ("openai", "sk-org-b-provider-key-2222", "https://api.openai.com/v1", "gpt-4.1-mini"),
    ]


@pytest.mark.asyncio
async def test_gateway_stream_preserves_observer_semantics_with_provider_backed_fallback(
    session, encryption_key, monkeypatch
) -> None:
    org = Org(name="Streaming Org")
    session.add(org)
    await session.flush()
    primary = _provider(
        org_id=org.id,
        code="openai",
        base_url="https://api.openai.com/v1",
        models=["gpt-4.1-mini"],
    )
    fallback = _provider(
        org_id=org.id,
        code="moonshot",
        base_url="https://api.moonshot.cn/v1",
        models=["moonshot-v1-8k"],
    )
    replace_provider_key(primary, "sk-primary-key-1111")
    replace_provider_key(fallback, "sk-fallback-key-2222")
    primary.verification_status = "verified"
    fallback.verification_status = "verified"
    session.add_all([primary, fallback])
    await session.flush()
    session.add(
        ModelConfig(
            org_id=org.id,
            agent_code="02",
            primary_provider_id=primary.id,
            fallback_provider_id=fallback.id,
            primary_model="gpt-4.1-mini",
            fallback_model="moonshot-v1-8k",
        )
    )
    await session.commit()

    class RuntimeAdapter:
        provider = "openai"

        def __init__(self, provider_code, api_key=None, base_url=None):
            self.provider_code = provider_code

        async def complete(self, model, messages, options=None):
            raise AssertionError("stream test should not use complete")

        async def stream(self, model, messages, options=None):
            if self.provider_code == "openai":
                raise RuntimeError("primary stream failed")
            for chunk in ["hello", " world"]:
                yield chunk

    monkeypatch.setattr("app.llm.gateway.OpenAICompatibleAdapter", RuntimeAdapter)

    events: list[dict] = []

    async def observer(event: dict) -> None:
        events.append(event)

    result, _ = await LLMGateway().chat_stream(session, org.id, "02", MSG, observer)

    assert result.content == "hello world"
    assert [event["phase"] for event in events] == [
        "start",
        "error",
        "start",
        "delta",
        "delta",
        "done",
    ]
    assert [event["model"] for event in events] == [
        "gpt-4.1-mini",
        "gpt-4.1-mini",
        "moonshot-v1-8k",
        "moonshot-v1-8k",
        "moonshot-v1-8k",
        "moonshot-v1-8k",
    ]
    rows = (await session.scalars(select(LLMCall).where(LLMCall.org_id == org.id))).all()
    assert sorted((row.provider, row.status) for row in rows) == [
        ("moonshot", "ok"),
        ("openai", "error"),
    ]


@pytest.mark.asyncio
async def test_gateway_rejects_unverified_provider_backed_route(
    session, monkeypatch
) -> None:
    org = Org(name="Pending Provider")
    session.add(org)
    await session.flush()
    provider = _provider(
        org_id=org.id,
        code="openai",
        base_url="https://api.openai.com/v1",
        models=["gpt-4.1-mini"],
        verification_status="pending",
    )
    session.add(provider)
    await session.flush()
    session.add(
        ModelConfig(
            org_id=org.id,
            agent_code="03",
            primary_provider_id=provider.id,
            primary_model="gpt-4.1-mini",
        )
    )
    await session.commit()

    class UnexpectedAdapter:
        provider = "openai"

        def __init__(self, *args, **kwargs):
            raise AssertionError("unverified providers should fail before adapter creation")

    monkeypatch.setattr("app.llm.gateway.OpenAICompatibleAdapter", UnexpectedAdapter)

    with pytest.raises(LLMError, match="all candidate models failed"):
        await LLMGateway().chat(session, org.id, "03", MSG)

    call = await session.scalar(select(LLMCall).where(LLMCall.org_id == org.id))
    assert call is not None
    assert call.status == "error"
    assert "verified" in (call.error or "")


@pytest.mark.asyncio
async def test_custom_adapter_cannot_bypass_provider_backed_route_validation(
    session,
) -> None:
    org = Org(name="Injected Adapter Org")
    session.add(org)
    await session.flush()
    provider = _provider(
        org_id=org.id,
        code="openai",
        base_url="https://api.openai.com/v1",
        models=["gpt-4.1-mini"],
        verification_status="pending",
    )
    session.add(provider)
    await session.flush()
    session.add(
        ModelConfig(
            org_id=org.id,
            agent_code="03",
            primary_provider_id=provider.id,
            primary_model="gpt-4.1-mini",
        )
    )
    await session.commit()

    class InjectedAdapter:
        provider = "openai"

        async def complete(self, model, messages, options=None):
            return CompletionResult("must not execute", model, 1, 1, 2)

        async def stream(self, model, messages, options=None):
            yield "must not execute"

    gateway = LLMGateway(adapters={"openai": InjectedAdapter()})
    with pytest.raises(LLMError, match="all candidate models failed"):
        await gateway.chat(session, org.id, "03", MSG)

    call = await session.scalar(select(LLMCall).where(LLMCall.org_id == org.id))
    assert call is not None
    assert call.status == "error"
    assert "verified" in (call.error or "")


@pytest.mark.asyncio
async def test_gateway_redacts_secret_bearing_adapter_errors_before_storage_and_observer(
    session,
) -> None:
    org = Org(name="Redaction Org")
    session.add(org)
    await session.flush()
    session.add(
        ModelConfig(
            org_id=org.id,
            agent_code="01",
            primary_model="deepseek-chat",
        )
    )
    await session.commit()

    class SecretFailingAdapter:
        provider = "deepseek"

        async def complete(self, model, messages, options=None):
            raise AssertionError("stream test should not use complete")

        async def stream(self, model, messages, options=None):
            if False:
                yield ""
            raise RuntimeError(
                "Authorization Bearer sk-sensitive-provider-key-4321 failed"
            )

    events: list[dict] = []

    async def observer(event: dict) -> None:
        events.append(event)

    gateway = LLMGateway(adapters={"deepseek": SecretFailingAdapter()})
    with pytest.raises(LLMError) as exc_info:
        await gateway.chat_stream(session, org.id, "01", MSG, observer)

    call = await session.scalar(select(LLMCall).where(LLMCall.org_id == org.id))
    assert call is not None
    assert "sk-sensitive-provider-key-4321" not in (call.error or "")
    assert "[REDACTED]" in (call.error or "")
    serialized_events = str(events)
    assert "sk-sensitive-provider-key-4321" not in serialized_events
    assert "[REDACTED]" in serialized_events
    assert "sk-sensitive-provider-key-4321" not in str(exc_info.value.__cause__)
    assert "[REDACTED]" in str(exc_info.value.__cause__)
