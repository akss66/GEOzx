"""Contract and runtime coverage for WeChat article production."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models import (
    Account,
    AgentInvocation,
    AgentRun,
    ArticleImageSlot,
    ArticleWorkingCopy,
    ConversationThread,
    ConversationTurn,
    Event,
    PlatformPublishJob,
    SkillRun,
    TurnInterrupt,
)
from app.models.enums import (
    AccountStatus,
    AgentCode,
    AgentInvocationStatus,
    ArticleImageSlotStatus,
    Platform,
)
from app.orchestrator.capability_router import (
    SkillUnavailable,
    route_deterministic_request,
    route_explicit_request,
)
from app.orchestrator.skill_runtime import SkillRuntime
from app.orchestrator.skills.public_catalog import PUBLIC_SKILL_POLICIES
from app.orchestrator.skills.registry import skill_registry
from app.schemas.capability_request import CapabilityRequest
from app.schemas.conversation import TurnExecutionMode
from app.services.image_generation import ImageGenerationResult
from app.services.turn_interrupts import resolve_interrupt
from app.worker import _execute_v2_conversation_run


def _brief(*, include_primary_cta: bool = True) -> dict:
    value = {
        "objective": {
            "kind": "lead_generation",
            "description": "介绍企业知识库产品并获取咨询线索",
        },
        "target_audience": {
            "segments": ["中小企业内容负责人"],
            "scenarios": ["评估团队知识管理工具"],
        },
        "topic_or_product": "企业知识库产品",
        "brand_requirements": {
            "tone": ["专业", "清晰"],
            "must_include": ["人工确认后才同步草稿"],
            "forbidden_expressions": ["行业第一"],
        },
        "core_selling_points": ["信息可追溯", "多人协作"],
    }
    if include_primary_cta:
        value["primary_cta"] = {
            "action": "contact",
            "label": "预约产品演示",
            "url": "https://example.com/demo",
        }
    return value


def _document() -> dict:
    return {
        "title": "企业知识库如何让内容更可靠",
        "digest": "从信息可追溯到多人协作，介绍企业知识库的落地方法。",
        "author": "品牌内容团队",
        "blocks": [
            {
                "type": "heading",
                "block_id": "opening",
                "level": 2,
                "text": "团队内容为什么需要可追溯",
            },
            {
                "type": "paragraph",
                "block_id": "body",
                "text": "企业知识库把资料来源和协作过程保留在同一条工作链路中。",
            },
            {
                "type": "cta",
                "block_id": "cta",
                "label": "预约产品演示",
                "action": "contact",
                "url": "https://example.com/demo",
            },
        ],
        "claims": [],
    }


class _ArticleHarness:
    def __init__(self) -> None:
        self.calls: list[AgentCode] = []

    async def execute(self, session, **kwargs):
        code = kwargs["code"]
        self.calls.append(code)
        payload = {
            AgentCode.CONTENT_DIRECTOR: {
                "content_strategy": {
                    "angle": "以可追溯的内容协作为主线",
                    "outline": ["问题", "方法", "行动"],
                }
            },
            AgentCode.EDITOR: {"document": _document()},
            AgentCode.ART_DIRECTOR: {
                "image_slots": [
                    {
                        "stable_key": "workflow-overview",
                        "purpose": "解释知识从来源到成稿的工作链路",
                        "placement_after_block_id": "body",
                        "aspect_ratio": "16:9",
                        "visual_brief": "简洁的信息流图，使用品牌蓝色，不出现夸张数据。",
                    }
                ]
            },
        }[code]
        invocation = AgentInvocation(
            task_id=kwargs["task"].id,
            run_id=kwargs["run_id"],
            skill_run_id=kwargs["skill_run_id"],
            thread_id=kwargs["thread_id"],
            turn_id=kwargs["turn_id"],
            step_key=kwargs["step_key"],
            attempt=kwargs["attempt"],
            agent_code=code,
            agent_name=code.value,
            status=AgentInvocationStatus.DONE,
            input_summary=kwargs["purpose"],
            output_summary=f"{code.value} completed",
            model="test-article-harness",
            token_count=1,
            cost=Decimal("0"),
            upstream=[],
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(invocation)
        await session.flush()
        return SimpleNamespace(invocation=invocation, output=payload)


class _ImageProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, **_kwargs):
        self.calls += 1
        return ImageGenerationResult(
            provider="task-14-test",
            content=base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            ),
            media_type="image/png",
        )


async def _wechat_scope(
    session,
    admin,
    *,
    key: str,
    platform: Platform = Platform.WECHAT_OFFICIAL_ACCOUNT,
):
    account = Account(
        org_id=admin.org_id,
        platform=platform,
        nickname=f"wechat-{key}",
        status=AccountStatus.ACTIVE,
        auth={"auth_status": "authorized", "data_sync_status": "ready"},
    )
    session.add(account)
    await session.flush()
    thread = ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title=key,
    )
    session.add(thread)
    await session.flush()
    turn = ConversationTurn(
        thread_id=thread.id,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id=key,
        user_input="请制作企业知识库产品的公众号文章",
    )
    session.add(turn)
    await session.flush()
    run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        thread_id=thread.id,
        turn_id=turn.id,
        client_message_id=key,
        status="claimed",
        request_payload={"message": turn.user_input},
    )
    session.add(run)
    await session.commit()
    return account, thread, turn, run


async def _turn_scope_for_account(session, admin, account, *, key: str):
    thread = ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title=key,
    )
    session.add(thread)
    await session.flush()
    turn = ConversationTurn(
        thread_id=thread.id,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id=key,
        user_input="为企业知识库文章生成已规划图片",
    )
    session.add(turn)
    await session.flush()
    run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        thread_id=thread.id,
        turn_id=turn.id,
        client_message_id=key,
        status="claimed",
        request_payload={"message": turn.user_input},
    )
    session.add(run)
    await session.commit()
    return thread, turn, run


def _request(
    *,
    admin,
    account,
    thread,
    turn,
    run,
    brief: dict | None,
    requested_action: str = "produce",
    **extra,
) -> CapabilityRequest:
    structured_input = {
        "requested_action": requested_action,
        **({"brief": brief} if brief is not None else {}),
        **extra,
    }
    return CapabilityRequest(
        org_id=admin.org_id,
        user_id=admin.id,
        account_id=account.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        message=turn.user_input,
        requested_skill_code="wechat_article_production",
        execution_preference="FORMAL_TASK",
        structured_input=structured_input,
    )


def test_wechat_article_skill_is_public_only_for_wechat_accounts() -> None:
    """Catches registration drift that exposes article production cross-platform."""

    skill = skill_registry.get("wechat_article_production")

    assert skill.version == 1
    assert skill.supported_platforms == frozenset({"wechat_official_account"})
    assert skill.approval_policy == "explicit_before_external_write"
    assert skill.expert_codes == (
        "02-content-director",
        "05-editor",
        "03-art-director",
    )
    policy = PUBLIC_SKILL_POLICIES["wechat_article_production"]
    assert policy.enabled is True
    assert policy.surfaces == frozenset({"composer", "artifact_center"})


def test_natural_language_article_request_routes_deterministically() -> None:
    """Catches a concrete WeChat article request falling through to model routing."""

    decision = route_deterministic_request(
        "请为公众号制作一篇产品介绍文章",
        platform="wechat_official_account",
        registry=skill_registry,
        has_account=True,
    )

    assert decision is not None
    assert decision.mode is TurnExecutionMode.SKILL
    assert decision.skill_code == "wechat_article_production"
    assert decision.reason == "deterministic_wechat_article_production"


def test_natural_language_article_request_is_not_routed_for_other_platforms() -> None:
    """Catches the WeChat-only intent leaking into established platform accounts."""

    decision = route_deterministic_request(
        "请为公众号制作一篇产品介绍文章",
        platform="douyin",
        registry=skill_registry,
        has_account=True,
    )

    assert decision is None


def test_explicit_wechat_article_skill_rejects_other_platforms() -> None:
    """Catches explicit selection bypassing the Skill platform boundary."""

    with pytest.raises(SkillUnavailable) as exc_info:
        route_explicit_request(
            "wechat_article_production",
            platform="xiaohongshu",
            registry=skill_registry,
            has_account=True,
        )

    assert exc_info.value.code == "unsupported_platform"


def test_existing_skill_approval_contract_remains_unchanged() -> None:
    """Catches the new approval literal rewriting an established Skill policy."""

    assert skill_registry.get("content_publishing").approval_policy == "before_tools"
    assert skill_registry.get("account_inspection").approval_policy == "none"


@pytest.mark.asyncio
async def test_skill_waits_for_missing_primary_cta_without_creating_duplicate_turns(
    session,
    admin,
) -> None:
    """Catches strict brief validation escaping instead of pausing the owned turn."""

    account, thread, turn, run = await _wechat_scope(
        session,
        admin,
        key="wechat-missing-primary-cta",
    )
    runtime = SkillRuntime()

    result = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="wechat_article_production",
        capability_request=_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            brief=_brief(include_primary_cta=False),
        ),
    )

    assert result.status == "waiting_user"
    assert result.interrupt == {
        "kind": "clarification",
        "required_fields": ["primary_cta"],
        "article": "企业知识库产品",
        "action": "produce",
    }
    assert (
        await session.scalar(
            select(func.count(ConversationTurn.id)).where(ConversationTurn.thread_id == thread.id)
        )
        == 1
    )
    interrupts = list(
        await session.scalars(select(TurnInterrupt).where(TurnInterrupt.run_id == run.id))
    )
    assert len(interrupts) == 1
    assert interrupts[0].response_schema["required"] == ["primary_cta"]


@pytest.mark.asyncio
async def test_worker_resumes_missing_cta_on_same_turn_without_duplicate_experts(
    session,
    admin,
    monkeypatch,
) -> None:
    """Exercise resolve -> queued run -> worker -> recovered SkillRuntime end to end."""

    account, thread, turn, run = await _wechat_scope(
        session,
        admin,
        key="wechat-resume-primary-cta",
    )
    original_request = _request(
        admin=admin,
        account=account,
        thread=thread,
        turn=turn,
        run=run,
        brief=_brief(include_primary_cta=False),
    )
    run.request_payload = {
        **dict(run.request_payload or {}),
        "client_message_id": run.client_message_id,
        "execution_preference": "FORMAL_TASK",
        "requested_skill_code": "wechat_article_production",
        "trusted_structured_input": original_request.structured_input,
    }
    harness = _ArticleHarness()
    runtime = SkillRuntime(harness=harness)
    monkeypatch.setattr("app.services.turn_execution.skill_runtime", runtime)
    waiting = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="wechat_article_production",
        capability_request=original_request,
    )
    assert waiting.status == "waiting_user"
    interrupt = await session.scalar(select(TurnInterrupt).where(TurnInterrupt.run_id == run.id))
    assert interrupt is not None

    resolved = await resolve_interrupt(
        session,
        user=admin,
        interrupt_id=interrupt.id,
        expected_version=interrupt.version,
        idempotency_key="wechat-resume-primary-cta",
        resolution={"primary_cta": _brief()["primary_cta"]},
    )
    assert resolved.run.id == run.id
    assert resolved.run.status == "queued"
    assert set(resolved.run.request_payload["trusted_structured_input"]) == {
        "brief",
        "requested_action",
        "sync_confirmed",
    }

    result = await _execute_v2_conversation_run(
        session,
        run=resolved.run,
        worker_id="wechat-resume-primary-cta-worker",
    )

    assert result.status == "waiting_user"
    assert result.projections[0]["artifact_type"] == "wechat_article"
    replay = await _execute_v2_conversation_run(
        session,
        run=resolved.run,
        worker_id="wechat-resume-primary-cta-worker",
    )
    assert replay == result
    assert resolved.run.id == run.id
    assert resolved.run.turn_id == turn.id
    assert (
        await session.scalar(
            select(func.count(ConversationTurn.id)).where(ConversationTurn.thread_id == thread.id)
        )
        == 1
    )
    assert harness.calls == [
        AgentCode.CONTENT_DIRECTOR,
        AgentCode.EDITOR,
        AgentCode.ART_DIRECTOR,
    ]
    assert (
        await session.scalar(select(func.count(AgentRun.id)).where(AgentRun.turn_id == turn.id))
        == 1
    )
    assert (
        await session.scalar(select(func.count(SkillRun.id)).where(SkillRun.run_id == run.id)) == 1
    )
    assert (
        await session.scalar(
            select(func.count(AgentInvocation.id)).where(AgentInvocation.run_id == run.id)
        )
        == 3
    )


@pytest.mark.asyncio
async def test_wechat_cta_interrupt_rejects_invalid_cta_without_resolving(
    session,
    admin,
) -> None:
    """A present but invalid CTA cannot consume the domain clarification."""

    account, thread, turn, run = await _wechat_scope(
        session,
        admin,
        key="wechat-invalid-primary-cta",
    )
    waiting = await SkillRuntime().execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="wechat_article_production",
        capability_request=_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            brief=_brief(include_primary_cta=False),
        ),
    )
    assert waiting.status == "waiting_user"
    interrupt = await session.scalar(select(TurnInterrupt).where(TurnInterrupt.run_id == run.id))
    assert interrupt is not None

    with pytest.raises(Exception) as exc_info:
        await resolve_interrupt(
            session,
            user=admin,
            interrupt_id=interrupt.id,
            expected_version=interrupt.version,
            idempotency_key="wechat-invalid-primary-cta",
            resolution={"primary_cta": {}},
        )

    assert getattr(exc_info.value, "status_code", None) == 422
    await session.refresh(interrupt)
    assert interrupt.status == "pending"
    assert interrupt.version == 1


@pytest.mark.asyncio
async def test_initial_production_uses_experts_and_never_generates_images_or_syncs_draft(
    session,
    admin,
    monkeypatch,
) -> None:
    """Catches Skill startup crossing either separately-confirmed external-action gate."""

    account, thread, turn, run = await _wechat_scope(
        session,
        admin,
        key="wechat-initial-production",
    )
    image_generation = 0
    draft_syncs = 0

    async def reject_image_generation(*_args, **_kwargs):
        nonlocal image_generation
        image_generation += 1
        raise AssertionError("initial production must not generate images")

    async def reject_draft_sync(*_args, **_kwargs):
        nonlocal draft_syncs
        draft_syncs += 1
        raise AssertionError("initial production must not synchronize a draft")

    monkeypatch.setattr(
        "app.services.image_generation.WechatArticleImageService.generate_all",
        reject_image_generation,
    )
    monkeypatch.setattr(
        "app.services.publishing.prepare_wechat_draft_sync_job",
        reject_draft_sync,
    )
    harness = _ArticleHarness()
    runtime = SkillRuntime(harness=harness)

    result = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="wechat_article_production",
        capability_request=_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            brief=_brief(),
        ),
    )

    assert result.status == "waiting_user"
    assert result.interrupt == {
        "kind": "article_action",
        "article": "企业知识库如何让内容更可靠",
        "article_id": result.report["article_id"],
        "article_version_id": result.artifact_id,
        "available_actions": ["generate_images", "sync_draft"],
    }
    assert harness.calls == [
        AgentCode.CONTENT_DIRECTOR,
        AgentCode.EDITOR,
        AgentCode.ART_DIRECTOR,
    ]
    assert image_generation == 0
    assert draft_syncs == 0
    assert await session.scalar(select(func.count(PlatformPublishJob.id))) == 0
    slots = list(
        await session.scalars(
            select(ArticleImageSlot).where(
                ArticleImageSlot.content_item_id == result.report["article_id"]
            )
        )
    )
    assert [slot.status for slot in slots] == [ArticleImageSlotStatus.PLANNED]
    working_copy = await session.scalar(
        select(ArticleWorkingCopy).where(
            ArticleWorkingCopy.content_item_id == result.report["article_id"]
        )
    )
    assert working_copy is not None
    assert [
        block["slot_key"]
        for block in working_copy.document["blocks"]
        if block["type"] == "imageSlot"
    ] == ["workflow-overview"]
    assert result.report["explicit_user_decisions"] == [
        {"action": "generate_images", "status": "not_requested"},
        {"action": "sync_draft", "status": "not_requested"},
    ]
    assert result.report["readiness"]["quality_review"] == {"status": "unavailable"}
    skill_run = await session.get(SkillRun, result.skill_run_id)
    assert skill_run is not None and skill_run.quality_score is None
    stage_events = list(
        await session.scalars(
            select(Event)
            .where(Event.run_id == run.id, Event.type == "step.started")
            .order_by(Event.id)
        )
    )
    assert [event.payload["step"] for event in stage_events] == [
        "brief_resolution",
        "scoped_knowledge",
        "content_strategy",
        "article_editing",
        "visual_planning",
        "compliance_and_fact_gate",
        "render_preview",
        "waiting_user",
    ]


@pytest.mark.asyncio
async def test_runtime_rejects_wechat_skill_for_a_non_wechat_account(
    session,
    admin,
) -> None:
    """Catches direct runtime invocation bypassing router platform isolation."""

    account, thread, turn, run = await _wechat_scope(
        session,
        admin,
        key="wechat-runtime-platform-rejection",
        platform=Platform.DOUYIN,
    )

    with pytest.raises(PermissionError, match="platform"):
        await SkillRuntime(harness=_ArticleHarness()).execute(
            session,
            user=admin,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="wechat_article_production",
            capability_request=_request(
                admin=admin,
                account=account,
                thread=thread,
                turn=turn,
                run=run,
                brief=_brief(),
            ),
        )

    assert await session.scalar(select(func.count(SkillRun.id))) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested_action", "extra", "required_fields"),
    [
        (
            "generate_images",
            {},
            ["idempotency_key", "working_copy_id"],
        ),
        (
            "sync_draft",
            {"working_copy_id": 91, "article_version_id": 17},
            ["idempotency_key", "sync_confirmed"],
        ),
    ],
)
async def test_external_actions_require_separate_explicit_bound_inputs(
    session,
    admin,
    requested_action,
    extra,
    required_fields,
) -> None:
    """Catches external actions running from an action name without bound confirmation data."""

    account, thread, turn, run = await _wechat_scope(
        session,
        admin,
        key=f"wechat-action-gate-{requested_action}",
    )

    result = await SkillRuntime(harness=_ArticleHarness()).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="wechat_article_production",
        capability_request=_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            brief=None,
            requested_action=requested_action,
            **extra,
        ),
    )

    assert result.status == "waiting_user"
    assert result.interrupt["required_fields"] == required_fields
    assert await session.scalar(select(func.count(PlatformPublishJob.id))) == 0


@pytest.mark.asyncio
async def test_waiting_article_production_replays_without_duplicate_work(
    session,
    admin,
) -> None:
    """Catches recovery recreating experts, article versions, slots, or interrupts."""

    account, thread, turn, run = await _wechat_scope(
        session,
        admin,
        key="wechat-replay-idempotency",
    )
    request = _request(
        admin=admin,
        account=account,
        thread=thread,
        turn=turn,
        run=run,
        brief=_brief(),
    )
    harness = _ArticleHarness()
    runtime = SkillRuntime(harness=harness)

    first = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="wechat_article_production",
        capability_request=request,
    )
    replay = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="wechat_article_production",
        capability_request=request,
    )

    assert replay == first
    assert len(harness.calls) == 3
    assert await session.scalar(select(func.count(SkillRun.id))) == 1
    assert await session.scalar(select(func.count(TurnInterrupt.id))) == 1
    assert await session.scalar(select(func.count(ArticleImageSlot.id))) == 1


@pytest.mark.asyncio
async def test_explicit_generate_images_action_uses_existing_scoped_image_service(
    session,
    admin,
    monkeypatch,
    tmp_path,
) -> None:
    """Catches the confirmed image action remaining a no-op or crossing article scope."""

    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    account, source_thread, source_turn, source_run = await _wechat_scope(
        session,
        admin,
        key="wechat-image-action-source",
    )
    source = await SkillRuntime(harness=_ArticleHarness()).execute(
        session,
        user=admin,
        thread=source_thread,
        turn=source_turn,
        run=source_run,
        skill_code="wechat_article_production",
        capability_request=_request(
            admin=admin,
            account=account,
            thread=source_thread,
            turn=source_turn,
            run=source_run,
            brief=_brief(),
        ),
    )
    working_copy = await session.scalar(
        select(ArticleWorkingCopy).where(
            ArticleWorkingCopy.content_item_id == source.report["article_id"]
        )
    )
    assert working_copy is not None
    thread, turn, run = await _turn_scope_for_account(
        session,
        admin,
        account,
        key="wechat-image-action",
    )
    provider = _ImageProvider()

    result = await SkillRuntime(
        harness=_ArticleHarness(),
        image_generation_provider=provider,
    ).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="wechat_article_production",
        capability_request=_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            brief=None,
            requested_action="generate_images",
            working_copy_id=working_copy.id,
            idempotency_key="task-14-generate-images",
        ),
    )

    assert result.status == "completed"
    assert provider.calls == 1
    assert result.report["article_id"] == source.report["article_id"]
    assert result.report["explicit_user_decisions"] == [
        {"action": "generate_images", "status": "executed"}
    ]
    assert await session.scalar(select(func.count(PlatformPublishJob.id))) == 0


@pytest.mark.asyncio
async def test_explicit_sync_action_uses_task13_immutable_draft_path(
    session,
    admin,
    monkeypatch,
) -> None:
    """Catches a sync confirmation bypassing or replacing the Task 13 service path."""

    account, source_thread, source_turn, source_run = await _wechat_scope(
        session,
        admin,
        key="wechat-sync-action-source",
    )
    source = await SkillRuntime(harness=_ArticleHarness()).execute(
        session,
        user=admin,
        thread=source_thread,
        turn=source_turn,
        run=source_run,
        skill_code="wechat_article_production",
        capability_request=_request(
            admin=admin,
            account=account,
            thread=source_thread,
            turn=source_turn,
            run=source_run,
            brief=_brief(),
        ),
    )
    working_copy = await session.scalar(
        select(ArticleWorkingCopy).where(
            ArticleWorkingCopy.content_item_id == source.report["article_id"]
        )
    )
    assert working_copy is not None
    calls: list[tuple[str, int]] = []

    async def prepare(_session, _user, *, article_id, request):
        assert request.article_version_id == source.artifact_id
        assert request.idempotency_key == "task-14-sync-draft"
        calls.append(("prepare", article_id))
        return SimpleNamespace(id=41)

    async def execute(_session, _user, *, job_id, capability_probe, token_provider, draft_client):
        assert (capability_probe, token_provider, draft_client) == ("probe", "token", "draft")
        calls.append(("execute", job_id))
        return SimpleNamespace(
            id=job_id,
            status=SimpleNamespace(value="wechat_succeeded"),
            external_media_id="wechat-draft-media-1",
        )

    monkeypatch.setattr(
        "app.orchestrator.skill_runtime.prepare_wechat_draft_sync_job",
        prepare,
    )
    monkeypatch.setattr(
        "app.orchestrator.skill_runtime.execute_wechat_draft_sync_job",
        execute,
    )
    thread, turn, run = await _turn_scope_for_account(
        session,
        admin,
        account,
        key="wechat-sync-action",
    )

    result = await SkillRuntime(
        harness=_ArticleHarness(),
        wechat_capability_probe="probe",
        wechat_token_provider="token",
        wechat_draft_client="draft",
    ).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="wechat_article_production",
        capability_request=_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            brief=None,
            requested_action="sync_draft",
            working_copy_id=working_copy.id,
            article_version_id=source.artifact_id,
            idempotency_key="task-14-sync-draft",
            sync_confirmed=True,
        ),
    )

    assert result.status == "completed"
    assert calls == [("prepare", source.report["article_id"]), ("execute", 41)]
    assert result.report["explicit_user_decisions"] == [
        {
            "action": "sync_draft",
            "status": "executed",
            "article_version_id": source.artifact_id,
        }
    ]


@pytest.mark.asyncio
async def test_external_action_fails_closed_on_cross_account_working_copy(
    session,
    admin,
) -> None:
    """Catches an explicit action using a valid working-copy ID from another account."""

    owner, owner_thread, owner_turn, owner_run = await _wechat_scope(
        session,
        admin,
        key="wechat-lineage-owner",
    )
    source = await SkillRuntime(harness=_ArticleHarness()).execute(
        session,
        user=admin,
        thread=owner_thread,
        turn=owner_turn,
        run=owner_run,
        skill_code="wechat_article_production",
        capability_request=_request(
            admin=admin,
            account=owner,
            thread=owner_thread,
            turn=owner_turn,
            run=owner_run,
            brief=_brief(),
        ),
    )
    foreign_copy = await session.scalar(
        select(ArticleWorkingCopy).where(
            ArticleWorkingCopy.content_item_id == source.report["article_id"]
        )
    )
    assert foreign_copy is not None
    account, thread, turn, run = await _wechat_scope(
        session,
        admin,
        key="wechat-lineage-attacker",
    )
    provider = _ImageProvider()

    result = await SkillRuntime(
        harness=_ArticleHarness(),
        image_generation_provider=provider,
    ).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="wechat_article_production",
        capability_request=_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            brief=None,
            requested_action="generate_images",
            working_copy_id=foreign_copy.id,
            idempotency_key="task-14-cross-account",
        ),
    )

    assert result.status == "blocked"
    assert result.error_code == "TOOL_RESULT_SCOPE_MISMATCH"
    assert provider.calls == 0
