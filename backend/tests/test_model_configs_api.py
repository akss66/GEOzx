"""妯″瀷鍩虹璁炬柦鎺ュ彛锛氱鐞嗗憳鏉冮檺銆佸瘑閽ュ紩鐢ㄣ€佽矾鐢辩瓥鐣ュ拰璋冪敤璐︽湰銆?"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models import Event, IntegrationConfig, LLMCall, ModelConfig, ModelProvider, Org, User
from app.models.enums import UserRole


async def _token(client, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_model_infrastructure_is_admin_only_and_never_returns_secret(
    client, admin, member, monkeypatch
):
    monkeypatch.setattr("app.config.settings.deepseek_api_key", "sk-sensitive-value")
    admin_headers = _auth(await _token(client, "admin@test.com", "admin-pw-123"))
    member_headers = _auth(await _token(client, "user@test.com", "user-pw-123"))

    assert (await client.get("/model-infrastructure", headers=member_headers)).status_code == 403
    assert (await client.get("/model-configs", headers=member_headers)).status_code == 403

    response = await client.get("/model-infrastructure", headers=admin_headers)

    assert response.status_code == 200
    body = response.json()
    assert {provider["code"] for provider in body["providers"]} == {"deepseek", "litellm"}
    deepseek = next(row for row in body["providers"] if row["code"] == "deepseek")
    assert deepseek["credential_ref"] == "env:DEEPSEEK_API_KEY"
    assert deepseek["credential_configured"] is True
    assert deepseek["runtime_ready"] is True
    assert len(body["routes"]) == 9
    assert "sk-sensitive-value" not in response.text


@pytest.mark.asyncio
async def test_provider_update_accepts_reference_only_and_writes_audit_event(
    client, admin, session, monkeypatch
):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-runtime-only")
    headers = _auth(await _token(client, "admin@test.com", "admin-pw-123"))

    rejected = await client.put(
        "/model-infrastructure/providers/deepseek",
        headers=headers,
        json={"enabled": True, "credential_ref": "sk-plaintext-is-forbidden"},
    )
    assert rejected.status_code == 422

    updated = await client.put(
        "/model-infrastructure/providers/deepseek",
        headers=headers,
        json={"enabled": True, "credential_ref": "env:DEEPSEEK_API_KEY"},
    )

    assert updated.status_code == 200
    assert updated.json()["credential_ref"] == "env:DEEPSEEK_API_KEY"
    assert updated.json()["credential_configured"] is True
    assert "sk-runtime-only" not in updated.text
    row = await session.scalar(
        select(IntegrationConfig).where(
            IntegrationConfig.org_id == admin.org_id,
            IntegrationConfig.provider == "deepseek",
        )
    )
    assert row is not None
    assert row.credentials == {"api_key_ref": "env:DEEPSEEK_API_KEY"}
    event = await session.scalar(select(Event).where(Event.type == "model.provider.updated"))
    assert event is not None
    assert event.payload["credential_ref"] == "env:DEEPSEEK_API_KEY"
    assert "sk-runtime-only" not in str(event.payload)


@pytest.mark.asyncio
async def test_route_update_preserves_business_policy_and_validates_fallback(
    client, admin, session
):
    foreign_org = Org(name="Foreign Org")
    session.add(foreign_org)
    await session.flush()
    primary = ModelProvider(
        org_id=admin.org_id,
        code="openai",
        display_name="OpenAI",
        provider_type="preset",
        template_code="openai",
        protocol="openai_compatible",
        base_url="https://api.openai.com/v1",
        enabled=True,
        credential_source="encrypted",
        verification_status="verified",
        models=["gpt-4.1-mini", "gpt-4.1"],
    )
    fallback = ModelProvider(
        org_id=admin.org_id,
        code="moonshot",
        display_name="Moonshot",
        provider_type="preset",
        template_code="moonshot",
        protocol="openai_compatible",
        base_url="https://api.moonshot.cn/v1",
        enabled=True,
        credential_source="encrypted",
        verification_status="verified",
        models=["moonshot-v1-8k"],
    )
    pending = ModelProvider(
        org_id=admin.org_id,
        code="pending",
        display_name="Pending",
        provider_type="custom_openai",
        template_code=None,
        protocol="openai_compatible",
        base_url="https://pending.example.com/v1",
        enabled=True,
        credential_source="encrypted",
        verification_status="pending",
        models=["pending-model"],
    )
    foreign_provider = ModelProvider(
        org_id=foreign_org.id,
        code="openai",
        display_name="Foreign OpenAI",
        provider_type="preset",
        template_code="openai",
        protocol="openai_compatible",
        base_url="https://api.openai.com/v1",
        enabled=True,
        credential_source="encrypted",
        verification_status="verified",
        models=["gpt-4.1-mini"],
    )
    foreign_admin = User(
        org_id=foreign_org.id,
        email="foreign-admin@test.com",
        hashed_password=hash_password("foreign-admin-pw-123"),
        display_name="Foreign Admin",
        role=UserRole.ADMIN,
    )
    config = ModelConfig(
        org_id=admin.org_id,
        agent_code="02-content-director",
        primary_model="deepseek-chat",
        params={"business_config": {"enabled": True, "responsibility": "缂栧啓鑴氭湰"}},
    )
    session.add_all([foreign_admin, primary, fallback, pending, foreign_provider, config])
    await session.commit()
    headers = _auth(await _token(client, "admin@test.com", "admin-pw-123"))

    same_fallback = await client.put(
        "/model-infrastructure/routes/02-content-director",
        headers=headers,
        json={
            "primary_provider_id": primary.id,
            "primary_model": "gpt-4.1-mini",
            "fallback_provider_id": primary.id,
            "fallback_model": "gpt-4.1-mini",
            "temperature": 0.4,
            "max_tokens": 4096,
            "timeout_seconds": 60,
        },
    )
    assert same_fallback.status_code == 422

    unverified = await client.put(
        "/model-infrastructure/routes/02-content-director",
        headers=headers,
        json={
            "primary_provider_id": pending.id,
            "primary_model": "pending-model",
            "fallback_provider_id": fallback.id,
            "fallback_model": "moonshot-v1-8k",
            "temperature": 0.4,
            "max_tokens": 4096,
            "timeout_seconds": 60,
        },
    )
    assert unverified.status_code == 422

    foreign = await client.put(
        "/model-infrastructure/routes/02-content-director",
        headers=headers,
        json={
            "primary_provider_id": foreign_provider.id,
            "primary_model": "gpt-4.1-mini",
            "fallback_provider_id": fallback.id,
            "fallback_model": "moonshot-v1-8k",
            "temperature": 0.4,
            "max_tokens": 4096,
            "timeout_seconds": 60,
        },
    )
    assert foreign.status_code == 422

    missing_model = await client.put(
        "/model-infrastructure/routes/02-content-director",
        headers=headers,
        json={
            "primary_provider_id": primary.id,
            "primary_model": "not-in-catalog",
            "fallback_provider_id": fallback.id,
            "fallback_model": "moonshot-v1-8k",
            "temperature": 0.4,
            "max_tokens": 4096,
            "timeout_seconds": 60,
        },
    )
    assert missing_model.status_code == 422

    updated = await client.put(
        "/model-infrastructure/routes/02-content-director",
        headers=headers,
        json={
            "primary_provider_id": primary.id,
            "primary_model": "gpt-4.1",
            "fallback_provider_id": fallback.id,
            "fallback_model": "moonshot-v1-8k",
            "temperature": 0.3,
            "max_tokens": 6144,
            "timeout_seconds": 90,
        },
    )

    assert updated.status_code == 200
    assert updated.json()["agent_name"] == "编导文案专家"
    assert updated.json()["primary_provider_id"] == primary.id
    assert updated.json()["fallback_provider_id"] == fallback.id
    assert updated.json()["primary_model"] == "gpt-4.1"
    assert updated.json()["temperature"] == 0.3
    await session.refresh(config)
    assert config.primary_provider_id == primary.id
    assert config.fallback_provider_id == fallback.id
    assert config.params["business_config"]["responsibility"] == "缂栧啓鑴氭湰"
    assert config.params["routing_config"] == {
        "temperature": 0.3,
        "max_tokens": 6144,
        "timeout_seconds": 90,
    }
    event = await session.scalar(select(Event).where(Event.type == "model.route.updated"))
    assert event is not None
    assert event.payload["agent_code"] == "02-content-director"


@pytest.mark.asyncio
async def test_recent_calls_are_admin_only_filtered_and_redacted(
    client, admin, member, session
):
    session.add_all(
        [
            LLMCall(
                org_id=admin.org_id,
                agent_code="02-content-director",
                provider="deepseek",
                model="deepseek-chat",
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30,
                cost_usd=0.01,
                latency_ms=250,
                status="ok",
                created_at=datetime.now(UTC),
            ),
            LLMCall(
                org_id=admin.org_id,
                agent_code="01-positioning",
                provider="deepseek",
                model="deepseek-reasoner",
                latency_ms=800,
                status="error",
                error="Authorization Bearer sk-secret-token failed at api.deepseek.com",
                created_at=datetime.now(UTC),
            ),
        ]
    )
    await session.commit()
    admin_headers = _auth(await _token(client, "admin@test.com", "admin-pw-123"))
    member_headers = _auth(await _token(client, "user@test.com", "user-pw-123"))

    assert (
        await client.get("/model-infrastructure/calls", headers=member_headers)
    ).status_code == 403
    response = await client.get(
        "/model-infrastructure/calls?status=error&limit=20", headers=admin_headers
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    call = response.json()["items"][0]
    assert call["status"] == "error"
    assert call["agent_name"] == "账号定位专家"
    assert "sk-secret-token" not in response.text
    assert "[REDACTED]" in call["error_summary"]


@pytest.mark.asyncio
async def test_legacy_model_config_update_remains_admin_only(client, admin, member, session):
    cfg = ModelConfig(
        org_id=admin.org_id,
        agent_code="01-positioning",
        primary_model="deepseek-chat",
    )
    session.add(cfg)
    await session.commit()
    await session.refresh(cfg)
    member_headers = _auth(await _token(client, "user@test.com", "user-pw-123"))
    admin_headers = _auth(await _token(client, "admin@test.com", "admin-pw-123"))

    assert (await client.get("/model-configs", headers=member_headers)).status_code == 403
    updated = await client.patch(
        f"/model-configs/{cfg.id}",
        headers=admin_headers,
        json={"primary_model": "deepseek-reasoner", "fallback_model": "deepseek-chat"},
    )

    assert updated.status_code == 200
    assert updated.json()["primary_model"] == "deepseek-reasoner"


@pytest.mark.asyncio
async def test_legacy_model_config_update_rejects_provider_backed_routes(
    client, admin, session
):
    provider = ModelProvider(
        org_id=admin.org_id,
        code="openai",
        display_name="OpenAI",
        provider_type="preset",
        template_code="openai",
        protocol="openai_compatible",
        base_url="https://api.openai.com/v1",
        enabled=True,
        credential_source="encrypted",
        verification_status="verified",
        models=["gpt-4.1-mini"],
    )
    session.add(provider)
    await session.flush()
    cfg = ModelConfig(
        org_id=admin.org_id,
        agent_code="02-content-director",
        primary_provider_id=provider.id,
        primary_model="gpt-4.1-mini",
    )
    session.add(cfg)
    await session.commit()
    headers = _auth(await _token(client, "admin@test.com", "admin-pw-123"))

    response = await client.patch(
        f"/model-configs/{cfg.id}",
        headers=headers,
        json={"fallback_model": "litellm:openai/gpt-4o-mini"},
    )

    assert response.status_code == 409
    assert "model-infrastructure/routes" in response.json()["detail"]
    await session.refresh(cfg)
    assert cfg.fallback_model is None
