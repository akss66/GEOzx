"""Engagement review stays evidence-bound and never auto-replies."""

from types import SimpleNamespace

import pytest

from app.models.enums import AgentCode
from app.orchestrator.skill_runtime import SkillRuntime
from tests.test_operating_skills import _capability_request, _Harness, _scope


class _EngagementTools:
    def __init__(self, *, samples: list[dict] | None = None, account_offset: int = 0):
        self.samples = samples or []
        self.account_offset = account_offset
        self.calls: list[str] = []

    async def execute(self, **kwargs):
        request = kwargs["request"]
        self.calls.append(request.tool_code)
        account_id = kwargs["scope"].account_id + self.account_offset
        return SimpleNamespace(
            status="success",
            tool_call=None,
            result={
                "account_id": account_id,
                "period": {"days": request.arguments["days"]},
                "response_scope": request.arguments["response_scope"],
                "content_item_ids": request.arguments["content_item_ids"],
                "metrics": {"comment_count": {"value": len(self.samples)}},
                "comment_samples": self.samples,
                "data_sufficiency": "sampled" if self.samples else "aggregate_only",
                "sources": [{"batch_id": 17}],
            },
        )


class _EngagementHarness(_Harness):
    async def execute(self, *args, **kwargs):
        result = await super().execute(*args, **kwargs)
        assert kwargs["code"] is AgentCode.CUSTOMER_SERVICE
        context = kwargs["upstream"]["tool_results"]["items"][0]["result"]
        assert all(
            item["account_id"] == context["account_id"]
            for item in context["comment_samples"]
        )
        result.output.clear()
        result.output.update(
            {
                "common_questions": ["施工需要多久？"],
                "sentiment": {"positive": 1, "neutral": 1, "negative": 0},
                "response_guidelines": ["先回答工期，再询问面积和现场条件"],
                "content_opportunities": ["制作不同面积施工时长对照视频"],
            }
        )
        return result


class _AcceptingCritic:
    async def review(self, **_kwargs):
        return SimpleNamespace(passed=True, score=93, issues=[], suggestions=[])


@pytest.mark.asyncio
async def test_engagement_review_requests_comment_details_when_only_aggregates_exist(
    session, admin
):
    account, thread, turn, run = await _scope(
        session, admin, key="engagement-missing", message="复盘最近互动"
    )
    tools = _EngagementTools()
    harness = _EngagementHarness()

    result = await SkillRuntime(
        tool_executor=tools,
        harness=harness,
        critic=_AcceptingCritic(),
    ).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="engagement_review",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="engagement_review",
            structured_input={"days": 30, "response_scope": "all"},
        ),
    )

    assert result.status == "waiting_user"
    assert result.report["status"] == "needs_input"
    assert result.report["common_questions"] == []
    assert harness.calls == []


@pytest.mark.asyncio
async def test_engagement_review_uses_only_current_account_samples_and_stays_read_only(
    session, admin
):
    account, thread, turn, run = await _scope(
        session, admin, key="engagement-ready", message="复盘最近互动"
    )
    tools = _EngagementTools(
        samples=[
            {"account_id": account.id, "text": "施工需要多久？"},
            {"account_id": account.id, "text": "讲得很清楚"},
        ]
    )

    result = await SkillRuntime(
        tool_executor=tools,
        harness=_EngagementHarness(),
        critic=_AcceptingCritic(),
    ).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="engagement_review",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="engagement_review",
            structured_input={"days": 14, "response_scope": "questions"},
        ),
    )

    assert result.status == "completed"
    assert result.report["common_questions"] == ["施工需要多久？"]
    assert result.report["participating_experts"] == [AgentCode.CUSTOMER_SERVICE.value]
    assert result.report["evidence_refs"] == [{"kind": "data_import_batch", "id": 17}]
    assert tools.calls == ["account.engagement_context"]


@pytest.mark.asyncio
async def test_engagement_review_rejects_cross_account_tool_result_before_expert(
    session, admin
):
    account, thread, turn, run = await _scope(
        session, admin, key="engagement-cross-account", message="复盘最近互动"
    )
    harness = _EngagementHarness()
    result = await SkillRuntime(
        tool_executor=_EngagementTools(
            samples=[{"account_id": account.id + 1, "text": "wrong account"}],
            account_offset=1,
        ),
        harness=harness,
        critic=_AcceptingCritic(),
    ).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="engagement_review",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="engagement_review",
            structured_input={},
        ),
    )

    assert result.status == "blocked"
    assert harness.calls == []
