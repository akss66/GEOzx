from dataclasses import FrozenInstanceError

import pytest
from pydantic import BaseModel, Field

from app.orchestrator.skills.registry import SkillRegistry, skill_registry
from app.schemas.skills import SkillCatalogItem, SkillDefinition


class AccountInspectionInput(BaseModel):
    days: int = Field(default=30, ge=1, le=90)


class AccountInspectionReport(BaseModel):
    summary: str


def _definition(
    *,
    code: str = "account_inspection",
    version: int = 1,
    name: str = "一键账号体检",
    supported_platforms: frozenset[str] = frozenset({"douyin"}),
) -> SkillDefinition:
    return SkillDefinition(
        code=code,
        version=version,
        name=name,
        description="诊断当前账号",
        supported_platforms=supported_platforms,
        input_model=AccountInspectionInput,
        output_model=AccountInspectionReport,
        expert_codes=("01-positioning", "02-content-director", "06-operator"),
        expert_stages=(
            ("01-positioning", "02-content-director"),
            ("06-operator",),
        ),
        tool_codes=("account.profile", "account.data_context"),
        critic_policy="required",
        risk_level="low",
        approval_policy="none",
        artifact_type="account_inspection_report",
    )


def test_registry_returns_only_platform_compatible_latest_business_skills() -> None:
    registry = SkillRegistry(
        [
            _definition(version=1, name="旧版账号体检"),
            _definition(version=2, name="账号体检"),
            _definition(
                code="content_plan",
                name="内容策划",
                supported_platforms=frozenset({"xiaohongshu"}),
            ),
        ]
    )

    catalog = registry.list_for("douyin")

    assert [item.code for item in catalog] == ["account_inspection"]
    assert catalog[0].version == 2
    assert registry.list_for("kuaishou") == []


def test_registry_gets_latest_by_default_and_an_exact_requested_version() -> None:
    registry = SkillRegistry([_definition(version=1), _definition(version=2)])

    assert registry.get("account_inspection").version == 2
    assert registry.get("account_inspection", version=1).version == 1
    with pytest.raises(KeyError):
        registry.get("account_inspection", version=3)


@pytest.mark.parametrize("version", [True, "1", 0, -1])
def test_registry_rejects_invalid_explicit_versions(version: object) -> None:
    registry = SkillRegistry([_definition()])

    with pytest.raises(ValueError, match="positive integer"):
        registry.get("account_inspection", version=version)


def test_registry_rejects_duplicate_code_and_version() -> None:
    definition = _definition()

    with pytest.raises(ValueError, match="duplicate Skill definition"):
        SkillRegistry([definition, definition])


@pytest.mark.parametrize("code", ["AccountInspection", "account-inspection", "1_account"])
def test_skill_definition_rejects_unstable_skill_codes(code: str) -> None:
    with pytest.raises(ValueError, match="snake_case"):
        _definition(code=code)


@pytest.mark.parametrize("version", [0, -1, "1"])
def test_skill_definition_rejects_non_positive_or_non_integer_versions(version: object) -> None:
    with pytest.raises(ValueError, match="positive"):
        _definition(version=version)


def test_skill_definition_is_immutable() -> None:
    definition = _definition()

    with pytest.raises(FrozenInstanceError):
        definition.name = "改名"


def test_catalog_item_projects_stable_business_fields_without_python_model_types() -> None:
    catalog_item = SkillRegistry([_definition()]).list_for("douyin")[0]

    assert catalog_item.model_dump() == {
        "code": "account_inspection",
        "version": 1,
        "name": "一键账号体检",
        "description": "诊断当前账号",
        "supported_platforms": ["douyin"],
        "expert_codes": ["01-positioning", "02-content-director", "06-operator"],
        "expert_stages": [
            ["01-positioning", "02-content-director"],
            ["06-operator"],
        ],
        "tool_codes": ["account.profile", "account.data_context"],
        "critic_policy": "required",
        "risk_level": "low",
        "approval_policy": "none",
        "artifact_type": "account_inspection_report",
    }
    assert "input_model" not in SkillCatalogItem.model_fields
    assert "output_model" not in SkillCatalogItem.model_fields


def test_production_registry_covers_the_first_account_operations_loop() -> None:
    expected = {
        "account_inspection",
        "topic_planning",
        "script_generation",
        "publishing_preparation",
        "performance_review",
    }

    assert {item.code for item in skill_registry.list_for("douyin")} == expected
    for code in expected:
        definition = skill_registry.get(code)
        assert definition.version >= 1
        assert definition.expert_codes
        assert definition.artifact_type


def test_skill_definition_rejects_expert_stage_drift() -> None:
    with pytest.raises(ValueError, match="expert_stages"):
        SkillDefinition(
            code="account_inspection",
            version=1,
            name="Account inspection",
            description="Inspect",
            supported_platforms=frozenset({"douyin"}),
            input_model=AccountInspectionInput,
            output_model=AccountInspectionReport,
            expert_codes=("01-positioning", "06-operator"),
            expert_stages=(("01-positioning",),),
            tool_codes=(),
            critic_policy="required",
            risk_level="low",
            approval_policy="none",
            artifact_type="account_inspection_report",
        )
