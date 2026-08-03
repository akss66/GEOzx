from dataclasses import replace
from importlib import import_module

import pytest

from app.models import Account
from app.models.enums import Platform, UserRole
from app.orchestrator.skills.registry import SkillRegistry, skill_registry


async def _token(client, email: str, password: str) -> str:
    response = await client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_skill_catalog_requires_authentication(client):
    response = await client.get("/skills?platform=douyin&surface=composer")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_douyin_composer_exposes_only_stable_business_skill_fields(client, admin):
    token = await _token(client, admin.email, "admin-pw-123")

    response = await client.get(
        "/skills?platform=douyin&surface=composer",
        headers=_auth(token),
    )

    assert response.status_code == 200
    body = response.json()
    item = next(row for row in body["data"] if row["code"] == "account_inspection")
    assert item == {
        "code": "account_inspection",
        "version": skill_registry.get("account_inspection").version,
        "name": "一键账号体检",
        "description": skill_registry.get("account_inspection").description,
        "category": "quick_operations",
        "icon": "activity",
        "requires_account": True,
        "availability": "needs_input",
        "reason": "请选择账号后再使用该能力",
        "required_context": ["account"],
        "is_available": False,
        "unavailable_reason": "请选择账号后再使用该能力",
    }
    assert set(item) == {
        "code",
        "version",
        "name",
        "description",
        "category",
        "icon",
        "requires_account",
        "availability",
        "reason",
        "required_context",
        "is_available",
        "unavailable_reason",
    }
    serialized = str(body).lower()
    for internal_field in [
        "prompt",
        "expert_codes",
        "tool_codes",
        "model",
        "provider",
        "parameters",
        "supported_platforms",
        "risk_level",
        "approval_policy",
        "artifact_type",
        "input_model",
        "output_model",
    ]:
        assert internal_field not in serialized


@pytest.mark.asyncio
async def test_catalog_resolves_availability_for_the_authorized_account(
    client, session, admin
):
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="catalog-account",
        auth={"auth_status": "manual"},
    )
    session.add(account)
    await session.commit()
    token = await _token(client, admin.email, "admin-pw-123")

    response = await client.get(
        f"/skills?platform=douyin&surface=composer&account_id={account.id}",
        headers=_auth(token),
    )

    assert response.status_code == 200
    item = next(row for row in response.json()["data"] if row["code"] == "account_inspection")
    assert item["availability"] == "available"
    assert item["reason"] is None
    assert item["is_available"] is True


@pytest.mark.asyncio
async def test_catalog_applies_surface_and_platform_compatibility_before_projection(
    client, admin, monkeypatch
):
    skills_api = import_module("app.api.skills")
    definition = skill_registry.get("account_inspection")
    incompatible_registry = SkillRegistry(
        [replace(definition, supported_platforms=frozenset({"xiaohongshu"}))]
    )
    monkeypatch.setattr(skills_api, "skill_registry", incompatible_registry)
    token = await _token(client, admin.email, "admin-pw-123")
    headers = _auth(token)

    wrong_platform = await client.get(
        "/skills?platform=douyin&surface=composer",
        headers=headers,
    )
    wrong_surface = await client.get(
        "/skills?platform=xiaohongshu&surface=artifact_center",
        headers=headers,
    )

    assert wrong_platform.status_code == 200
    assert wrong_platform.json() == {"data": []}
    assert wrong_surface.status_code == 200
    assert wrong_surface.json() == {"data": []}


@pytest.mark.asyncio
async def test_disabled_skill_uses_generic_public_unavailability_reason(client, admin, monkeypatch):
    skills_api = import_module("app.api.skills")
    policy = skills_api._PUBLIC_SKILL_POLICIES["account_inspection"]
    monkeypatch.setitem(
        skills_api._PUBLIC_SKILL_POLICIES,
        "account_inspection",
        replace(
            policy,
            enabled=False,
            internal_disabled_reason="missing provider credentials and private tool failure",
        ),
    )
    token = await _token(client, admin.email, "admin-pw-123")

    response = await client.get(
        "/skills?platform=douyin&surface=composer",
        headers=_auth(token),
    )

    assert response.status_code == 200
    item = response.json()["data"][0]
    assert item["is_available"] is False
    assert item["availability"] == "coming_soon"
    assert item["reason"] == "暂不可用"
    assert item["unavailable_reason"] == "暂不可用"
    serialized = str(response.json()).lower()
    assert "provider credentials" not in serialized
    assert "private tool failure" not in serialized


@pytest.mark.asyncio
async def test_role_incompatible_skill_does_not_reveal_authorization_policy(
    client, member, monkeypatch
):
    skills_api = import_module("app.api.skills")
    policy = skills_api._PUBLIC_SKILL_POLICIES["account_inspection"]
    monkeypatch.setitem(
        skills_api._PUBLIC_SKILL_POLICIES,
        "account_inspection",
        replace(policy, allowed_roles=frozenset({UserRole.ADMIN})),
    )
    token = await _token(client, member.email, "user-pw-123")

    response = await client.get(
        "/skills?platform=douyin&surface=composer",
        headers=_auth(token),
    )

    assert response.status_code == 200
    item = response.json()["data"][0]
    assert item["is_available"] is False
    assert item["availability"] == "coming_soon"
    assert item["reason"] == "暂不可用"
    assert item["unavailable_reason"] == "暂不可用"
    assert "admin" not in str(response.json()).lower()


@pytest.mark.asyncio
async def test_unpublished_registry_skill_is_not_enumerated(client, admin, monkeypatch):
    skills_api = import_module("app.api.skills")
    public_definition = skill_registry.get("account_inspection")
    private_definition = replace(
        public_definition,
        code="internal_shadow_skill",
        name="Private orchestration graph",
        expert_codes=("secret-expert",),
        expert_stages=(("secret-expert",),),
        tool_codes=("secret.tool",),
    )
    monkeypatch.setattr(
        skills_api,
        "skill_registry",
        SkillRegistry([public_definition, private_definition]),
    )
    token = await _token(client, admin.email, "admin-pw-123")

    response = await client.get(
        "/skills?platform=douyin&surface=composer",
        headers=_auth(token),
    )

    assert response.status_code == 200
    assert [row["code"] for row in response.json()["data"]] == ["account_inspection"]
    serialized = str(response.json()).lower()
    assert "internal_shadow_skill" not in serialized
    assert "secret-expert" not in serialized
    assert "secret.tool" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "platform=kuaishou&surface=composer",
        "platform=douyin&surface=root_console",
    ],
)
async def test_unknown_catalog_combinations_fail_safely(client, admin, query):
    token = await _token(client, admin.email, "admin-pw-123")

    response = await client.get(f"/skills?{query}", headers=_auth(token))

    assert response.status_code == 422
    serialized = response.text.lower()
    for internal_field in [
        "expert_codes",
        "tool_codes",
        "prompt",
        "provider",
        "parameters",
    ]:
        assert internal_field not in serialized
