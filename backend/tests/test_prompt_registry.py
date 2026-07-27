from hashlib import sha256

import pytest

from app.prompts.manifest import (
    PromptIntegrityError,
    PromptNotFound,
    PromptNotPublishable,
    PromptRegistry,
    PromptSpec,
    PromptTemplateError,
)


def _hash(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()


def test_prompt_registry_loads_and_renders_an_immutable_active_version(tmp_path):
    content = "你是主 Agent。\n当前上下文：${operating_context}"
    (tmp_path / "main-agent-v1.md").write_text(content, encoding="utf-8")
    registry = PromptRegistry(
        tmp_path,
        [
            PromptSpec(
                id="main-agent",
                version="1.0.0",
                relative_path="main-agent-v1.md",
                content_hash=_hash(content),
                schema_version="main-agent-input/v1",
            )
        ],
    )

    rendered = registry.render(
        "main-agent",
        variables={"operating_context": "抖音 / 数码菌"},
    )

    assert rendered.spec.id == "main-agent"
    assert rendered.spec.version == "1.0.0"
    assert rendered.content_hash == _hash(content)
    assert rendered.content.endswith("抖音 / 数码菌")


def test_prompt_registry_rejects_unknown_version_draft_and_changed_hash(tmp_path):
    active = "active prompt"
    draft = "draft prompt"
    (tmp_path / "active.md").write_text(active, encoding="utf-8")
    (tmp_path / "draft.md").write_text(draft, encoding="utf-8")
    registry = PromptRegistry(
        tmp_path,
        [
            PromptSpec(
                id="expert",
                version="1.0.0",
                relative_path="active.md",
                content_hash=_hash(active),
                schema_version="expert-output/v1",
            ),
            PromptSpec(
                id="expert",
                version="2.0.0-draft",
                relative_path="draft.md",
                content_hash=_hash(draft),
                schema_version="expert-output/v2",
                status="draft",
            ),
        ],
    )

    with pytest.raises(PromptNotFound):
        registry.load("expert", version="9.9.9")
    with pytest.raises(PromptNotPublishable):
        registry.load("expert", version="2.0.0-draft")

    (tmp_path / "active.md").write_text("tampered prompt", encoding="utf-8")
    with pytest.raises(PromptIntegrityError):
        registry.load("expert", version="1.0.0")


def test_prompt_registry_rejects_a_manifest_entry_with_a_missing_file(tmp_path):
    registry = PromptRegistry(
        tmp_path,
        [
            PromptSpec(
                id="missing",
                version="1.0.0",
                relative_path="missing.md",
                content_hash="0" * 64,
                schema_version="missing/v1",
            )
        ],
    )

    with pytest.raises(PromptNotFound):
        registry.load("missing")


def test_prompt_registry_rejects_unresolved_or_unknown_template_variables(tmp_path):
    content = "${goal} / ${scope}"
    (tmp_path / "prompt.md").write_text(content, encoding="utf-8")
    registry = PromptRegistry(
        tmp_path,
        [
            PromptSpec(
                id="template",
                version="1.0.0",
                relative_path="prompt.md",
                content_hash=_hash(content),
                schema_version="template/v1",
            )
        ],
    )

    with pytest.raises(PromptTemplateError):
        registry.render("template", variables={"goal": "增长"})
    with pytest.raises(PromptTemplateError):
        registry.render(
            "template",
            variables={"goal": "增长", "scope": "抖音", "extra": "not-declared"},
        )


def test_production_prompt_manifest_contains_main_agent_and_all_eight_experts():
    registry = PromptRegistry.production()

    expected = {
        "main-agent.intent",
        "main-agent.next-step",
        "main-agent.strategy-planning",
        "main-agent.decision-revision",
        "main-agent.acknowledgement",
        "main-agent.summary",
        "main-agent.conversation",
        "expert.01-positioning",
        "expert.02-content",
        "expert.03-art",
        "expert.04-video",
        "expert.05-editing",
        "expert.06-operation",
        "expert.07-advertiser",
        "expert.08-customer-service",
        "memory.compactor",
        "knowledge.extractor",
    }

    assert expected <= set(registry.prompt_ids())
    for prompt_id in expected:
        prompt = registry.load(prompt_id)
        assert prompt.spec.status == "active"
        assert "TODO" not in prompt.content
        assert "草稿" not in prompt.content


def test_strategy_prompt_requires_measurable_kpis_and_verified_experience_memory():
    prompt = PromptRegistry.production().load("main-agent.strategy-planning")

    assert '"baseline": 0' in prompt.content
    assert '"direction": "increase|decrease|maintain|observe"' in prompt.content
    assert "无法建立数值目标时只能使用 `observe`" in prompt.content
    assert "只能使用 `memory_context.experience.verified_items`" in prompt.content
