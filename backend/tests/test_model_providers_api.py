"""Model provider administration API contract tests."""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.core.security import hash_password
from app.models import Event, ModelConfig, ModelProvider, Org, User
from app.models.enums import UserRole
from app.services.model_provider_registry import provider_public_row, replace_provider_key


async def _token(client, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def encryption_key(monkeypatch):
    monkeypatch.setattr(
        settings,
        "credential_encryption_key",
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )


async def _other_org_admin(session) -> User:
    org = Org(name="Other Org")
    user = User(
        org=org,
        email="other-admin@test.com",
        hashed_password=hash_password("other-admin-pw-123"),
        display_name="Other Admin",
        role=UserRole.ADMIN,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _provider(
    session,
    *,
    org_id: int,
    code: str = "openai",
    display_name: str = "OpenAI",
    template_code: str | None = "openai",
    provider_type: str = "preset",
    base_url: str = "https://api.openai.com/v1",
    models: list[str] | None = None,
    credential_source: str = "none",
) -> ModelProvider:
    provider = ModelProvider(
        org_id=org_id,
        code=code,
        display_name=display_name,
        provider_type=provider_type,
        template_code=template_code,
        protocol="openai_compatible",
        base_url=base_url,
        credential_source=credential_source,
        verification_status="pending",
        models=models,
    )
    session.add(provider)
    await session.commit()
    await session.refresh(provider)
    return provider


@pytest.mark.asyncio
async def test_templates_and_provider_listing_are_admin_only_and_org_scoped(
    client, session, admin, member
):
    own_provider = await _provider(session, org_id=admin.org_id)
    other_admin = await _other_org_admin(session)
    await _provider(session, org_id=other_admin.org_id, code="moonshot", display_name="Moonshot")

    admin_headers = _auth(await _token(client, "admin@test.com", "admin-pw-123"))
    member_headers = _auth(await _token(client, "user@test.com", "user-pw-123"))

    assert (
        await client.get("/model-providers/templates", headers=member_headers)
    ).status_code == 403
    assert (await client.get("/model-providers", headers=member_headers)).status_code == 403

    templates = await client.get("/model-providers/templates", headers=admin_headers)
    listing = await client.get("/model-providers", headers=admin_headers)

    assert templates.status_code == 200
    assert {item["code"] for item in templates.json()} >= {"deepseek", "openai", "qwen"}
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [own_provider.id]
    assert "encrypted_api_key" not in listing.text


@pytest.mark.asyncio
async def test_create_get_patch_and_credential_rotation_never_expose_key_material(
    client, session, admin, encryption_key, monkeypatch
):
    headers = _auth(await _token(client, "admin@test.com", "admin-pw-123"))

    invalid = await client.post(
        "/model-providers",
        headers=headers,
        json={
            "display_name": "Unsafe Custom",
            "provider_type": "custom_openai",
            "base_url": "http://unsafe.example.com/v1",
        },
    )
    created = await client.post(
        "/model-providers",
        headers=headers,
        json={"template_code": "openai"},
    )

    assert invalid.status_code == 422
    assert created.status_code == 201
    provider_id = created.json()["id"]
    provider = await session.get(ModelProvider, provider_id)
    assert provider is not None
    provider.verification_status = "verified"
    provider.verified_at = provider.created_at
    await session.commit()

    async def _validated_url(url: str) -> str:
        return url

    monkeypatch.setattr(
        "app.services.model_provider_registry.validate_public_https_url",
        _validated_url,
    )

    duplicate = await client.post(
        "/model-providers",
        headers=headers,
        json={"template_code": "openai"},
    )
    detail = await client.get(f"/model-providers/{provider_id}", headers=headers)
    patched = await client.patch(
        f"/model-providers/{provider_id}",
        headers=headers,
        json={
            "display_name": "OpenAI Production",
            "base_url": "https://api-alt.example.com/v1",
            "enabled": False,
        },
    )
    put_credential = await client.put(
        f"/model-providers/{provider_id}/credential",
        headers=headers,
        json={"api_key": "sk-sensitive-provider-key-4321"},
    )
    delete_credential = await client.delete(
        f"/model-providers/{provider_id}/credential",
        headers=headers,
    )

    assert duplicate.status_code == 409
    assert detail.status_code == 200
    assert patched.status_code == 200
    assert patched.json()["display_name"] == "OpenAI Production"
    assert patched.json()["verification_status"] == "pending"
    assert patched.json()["verified_at"] is None
    assert patched.json()["enabled"] is False
    assert put_credential.status_code == 200
    assert put_credential.json()["key_last_four"] == "4321"
    assert put_credential.json()["key_configured"] is True
    assert delete_credential.status_code == 200
    assert delete_credential.json()["key_configured"] is False
    assert "sk-sensitive-provider-key-4321" not in put_credential.text
    assert "encrypted_api_key" not in put_credential.text

    provider = await session.get(ModelProvider, provider_id)
    assert provider is not None
    assert provider.encrypted_api_key is None


@pytest.mark.asyncio
async def test_cross_org_provider_access_returns_org_scoped_404(
    client, session, admin, encryption_key
):
    other_admin = await _other_org_admin(session)
    foreign_provider = await _provider(session, org_id=other_admin.org_id)
    headers = _auth(await _token(client, "admin@test.com", "admin-pw-123"))

    get_response = await client.get(f"/model-providers/{foreign_provider.id}", headers=headers)
    patch_response = await client.patch(
        f"/model-providers/{foreign_provider.id}",
        headers=headers,
        json={"display_name": "Hidden"},
    )
    credential_response = await client.put(
        f"/model-providers/{foreign_provider.id}/credential",
        headers=headers,
        json={"api_key": "sk-should-not-work"},
    )
    delete_response = await client.delete(
        f"/model-providers/{foreign_provider.id}",
        headers=headers,
    )

    assert get_response.status_code == 404
    assert patch_response.status_code == 404
    assert credential_response.status_code == 404
    assert delete_response.status_code == 404


@pytest.mark.asyncio
async def test_environment_deepseek_endpoint_is_immutable_and_fallback_requires_trusted_template(
    client, session, admin, encryption_key, monkeypatch
):
    async def accept_public_url(url: str) -> str:
        return url.rstrip("/")

    monkeypatch.setattr(
        "app.services.model_provider_registry.validate_public_https_url",
        accept_public_url,
    )
    monkeypatch.setattr(settings, "deepseek_api_key", "server-managed-key")
    provider = await _provider(
        session,
        org_id=admin.org_id,
        code="deepseek",
        display_name="DeepSeek",
        template_code="deepseek",
        base_url="https://api.deepseek.com",
        credential_source="environment",
    )
    headers = _auth(await _token(client, "admin@test.com", "admin-pw-123"))

    patched = await client.patch(
        f"/model-providers/{provider.id}",
        headers=headers,
        json={"base_url": "https://attacker.example.com/v1"},
    )

    assert patched.status_code == 409
    await session.refresh(provider)
    assert provider.base_url == "https://api.deepseek.com"
    assert provider_public_row(provider)["key_configured"] is True

    provider.template_code = None
    provider.base_url = "https://attacker.example.com/v1"
    await session.commit()
    removed = await client.delete(
        f"/model-providers/{provider.id}/credential",
        headers=headers,
    )

    assert removed.status_code == 200
    assert removed.json()["credential_source"] == "none"
    assert removed.json()["key_configured"] is False


@pytest.mark.asyncio
async def test_verify_maps_authentication_failure_without_leaking_key_material(
    client, session, admin, encryption_key, monkeypatch
):
    provider = await _provider(session, org_id=admin.org_id, models=["gpt-4.1-mini"])
    replace_provider_key(provider, "sk-sensitive-provider-key-4321")
    await session.commit()
    await session.refresh(provider)

    async def _bounded_request(method: str, url: str, **kwargs):
        return httpx.Response(
            401,
            json={"error": {"message": "invalid api key sk-sensitive-provider-key-4321"}},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(
        "app.services.model_provider_registry.bounded_outbound_request",
        _bounded_request,
        raising=False,
    )
    headers = _auth(await _token(client, "admin@test.com", "admin-pw-123"))

    response = await client.post(f"/model-providers/{provider.id}/verify", headers=headers)

    assert response.status_code == 200
    assert response.json()["verification_status"] == "error"
    assert response.json()["verification_error_code"] == "authentication_failed"
    assert "sk-sensitive-provider-key-4321" not in response.text

    await session.refresh(provider)
    assert provider.verification_error_code == "authentication_failed"
    audit = await session.scalar(
        select(Event).where(Event.type == "model_provider.verify").order_by(Event.id.desc())
    )
    assert audit is not None
    assert audit.payload["provider_id"] == provider.id
    assert audit.payload["verification_error_code"] == "authentication_failed"
    assert "sk-sensitive-provider-key-4321" not in json.dumps(audit.payload)
    assert provider.encrypted_api_key not in json.dumps(audit.payload)


@pytest.mark.asyncio
async def test_verify_and_discover_models_use_bounded_request_and_preserve_manual_models_on_unsupported_discovery(  # noqa: E501
    client, session, admin, encryption_key, monkeypatch
):
    provider = await _provider(
        session,
        org_id=admin.org_id,
        template_code=None,
        code="custom-provider",
        display_name="Custom Provider",
        provider_type="custom_openai",
        base_url="https://provider.example.com/v1",
        models=["manual-model"],
    )
    replace_provider_key(provider, "sk-sensitive-provider-key-4321")
    await session.commit()
    await session.refresh(provider)

    calls: list[tuple[str, str]] = []

    async def _success(method: str, url: str, **kwargs):
        calls.append((method, url))
        return httpx.Response(
            200,
            json={"data": [{"id": "manual-model"}, {"id": "gpt-4.1-mini"}]},
            request=httpx.Request(method, url),
        )

    async def _unsupported(method: str, url: str, **kwargs):
        calls.append((method, url))
        return httpx.Response(404, json={"detail": "not found"}, request=httpx.Request(method, url))

    monkeypatch.setattr(
        "app.services.model_provider_registry.bounded_outbound_request",
        _success,
        raising=False,
    )
    headers = _auth(await _token(client, "admin@test.com", "admin-pw-123"))

    verified = await client.post(f"/model-providers/{provider.id}/verify", headers=headers)

    monkeypatch.setattr(
        "app.services.model_provider_registry.bounded_outbound_request",
        _unsupported,
        raising=False,
    )
    discovered = await client.post(
        f"/model-providers/{provider.id}/discover-models",
        headers=headers,
    )

    assert verified.status_code == 200
    assert verified.json()["verification_status"] == "verified"
    assert verified.json()["verification_error_code"] is None
    assert discovered.status_code == 200
    assert discovered.json()["error_code"] == "discovery_unsupported"
    assert discovered.json()["models"] == ["manual-model"]
    assert calls == [
        ("GET", "https://provider.example.com/v1/models"),
        ("GET", "https://provider.example.com/v1/models"),
    ]

    await session.refresh(provider)
    assert provider.models == ["manual-model"]


@pytest.mark.asyncio
async def test_put_models_and_delete_provider_conflict_reports_affected_agent_names_and_safe_audits(
    client, session, admin
):
    provider = await _provider(session, org_id=admin.org_id)
    provider.verification_status = "verified"
    session.add(
        ModelConfig(
            org_id=admin.org_id,
            agent_code="02-content-director",
            primary_provider_id=provider.id,
            primary_model="gpt-4.1-mini",
        )
    )
    unused = await _provider(
        session,
        org_id=admin.org_id,
        code="moonshot",
        display_name="Moonshot",
        template_code="moonshot",
        base_url="https://api.moonshot.cn/v1",
    )
    await session.commit()

    headers = _auth(await _token(client, "admin@test.com", "admin-pw-123"))

    updated_models = await client.put(
        f"/model-providers/{provider.id}/models",
        headers=headers,
        json={"models": ["gpt-4.1-mini", "gpt-4.1"]},
    )
    blocked_delete = await client.delete(f"/model-providers/{provider.id}", headers=headers)
    deleted = await client.delete(f"/model-providers/{unused.id}", headers=headers)

    assert updated_models.status_code == 200
    assert updated_models.json()["models"] == ["gpt-4.1-mini", "gpt-4.1"]
    assert updated_models.json()["verification_status"] == "pending"
    assert blocked_delete.status_code == 409
    assert blocked_delete.json()["affected_agents"] == ["编导文案专家"]
    assert deleted.status_code == 204

    audit = (
        await session.scalars(
            select(Event)
            .where(Event.type.in_(["model_provider.models_updated", "model_provider.deleted"]))
            .order_by(Event.id.asc())
        )
    ).all()
    assert len(audit) == 2
    for event in audit:
        payload = json.dumps(event.payload)
        assert "api_key" not in payload
        assert "encrypted_api_key" not in payload


@pytest.mark.asyncio
async def test_put_models_rejects_removing_models_still_used_by_agent_routes(
    client, session, admin
):
    provider = await _provider(session, org_id=admin.org_id)
    provider.models = ["gpt-4.1-mini", "gpt-4.1"]
    provider.verification_status = "verified"
    session.add(
        ModelConfig(
            org_id=admin.org_id,
            agent_code="01-positioning",
            primary_provider_id=provider.id,
            primary_model="gpt-4.1-mini",
        )
    )
    await session.commit()

    headers = _auth(await _token(client, "admin@test.com", "admin-pw-123"))
    response = await client.put(
        f"/model-providers/{provider.id}/models",
        headers=headers,
        json={"models": ["gpt-4.1"]},
    )

    assert response.status_code == 409
    assert response.json() == {
        "affected_agents": ["账号定位专家"],
        "missing_models": ["gpt-4.1-mini"],
    }
    await session.refresh(provider)
    assert provider.models == ["gpt-4.1-mini", "gpt-4.1"]
    assert provider.verification_status == "verified"


@pytest.mark.asyncio
async def test_delete_maps_commit_time_route_reference_race_to_conflict(
    client, session, admin, monkeypatch
):
    provider = await _provider(session, org_id=admin.org_id)
    provider_id = provider.id
    headers = _auth(await _token(client, "admin@test.com", "admin-pw-123"))
    reference_calls = 0

    async def _references(*args, **kwargs):
        nonlocal reference_calls
        reference_calls += 1
        if reference_calls == 1:
            return {}
        return {
            provider_id: [
                {
                    "agent_code": "02-content-director",
                    "agent_name": "content-director",
                }
            ]
        }

    async def _commit_conflict():
        from sqlalchemy.exc import IntegrityError

        raise IntegrityError("DELETE", {}, RuntimeError("foreign key conflict"))

    monkeypatch.setattr(
        "app.services.model_provider_registry._provider_route_references",
        _references,
    )
    monkeypatch.setattr(session, "commit", _commit_conflict)

    response = await client.delete(f"/model-providers/{provider_id}", headers=headers)

    assert response.status_code == 409
    assert response.json()["affected_agents"] == ["content-director"]
    assert reference_calls == 2
