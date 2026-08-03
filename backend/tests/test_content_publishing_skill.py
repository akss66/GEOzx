"""Content-publishing Skill must report only durable platform truth."""

from types import SimpleNamespace

import pytest

from app.models.enums import DeliverableStatus
from app.orchestrator.skill_runtime import SkillRuntime
from tests.test_operating_skills import _capability_request, _scope
from tests.test_visual_brief_skill import _source


class _PublishingTools:
    def __init__(self, *, blocked: bool = False) -> None:
        self.blocked = blocked
        self.calls = 0

    async def execute(self, **kwargs):
        self.calls += 1
        arguments = kwargs["request"].arguments
        account_id = kwargs["scope"].account_id
        result = {
            "account_id": account_id,
            "source_artifact_id": arguments["approved_publish_artifact_id"],
            "source_artifact_version": arguments["source_artifact_version"],
            "platform_receipt_id": None if self.blocked else 91,
            "status": "blocked" if self.blocked else "handoff_ready",
            "published_at": None,
            "retryable": False,
            "connection_state": "needs_connection" if self.blocked else "connected",
            "reason": "DOUYIN_APP_NOT_CONFIGURED" if self.blocked else None,
        }
        return SimpleNamespace(status="success", result=result, tool_call=None)


@pytest.mark.asyncio
async def test_content_publishing_rejects_unapproved_artifact_before_tool(session, admin):
    account, thread, turn, run = await _scope(
        session, admin, key="publish-unapproved", message="发布这个内容"
    )
    source = await _source(
        session,
        account_id=account.id,
        created_by_id=admin.id,
        status=DeliverableStatus.PENDING_REVIEW,
    )
    tools = _PublishingTools()

    result = await SkillRuntime(tool_executor=tools).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="content_publishing",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="content_publishing",
            structured_input={"approved_publish_artifact_id": source.id},
        ),
    )

    assert result.status == "failed"
    assert tools.calls == 0


@pytest.mark.asyncio
async def test_content_publishing_maps_missing_adapter_to_blocked_without_receipt(
    session, admin
):
    account, thread, turn, run = await _scope(
        session, admin, key="publish-disconnected", message="发布这个内容"
    )
    source = await _source(
        session,
        account_id=account.id,
        created_by_id=admin.id,
        status=DeliverableStatus.APPROVED,
    )

    result = await SkillRuntime(tool_executor=_PublishingTools(blocked=True)).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="content_publishing",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="content_publishing",
            structured_input={"approved_publish_artifact_id": source.id},
        ),
    )

    assert result.status == "blocked"
    assert result.report["platform_receipt_id"] is None
    assert result.report["connection_state"] == "needs_connection"


@pytest.mark.asyncio
async def test_content_publishing_is_idempotent_and_never_claims_handoff_is_published(
    session, admin
):
    account, thread, turn, run = await _scope(
        session, admin, key="publish-idempotent", message="发布这个内容"
    )
    source = await _source(
        session,
        account_id=account.id,
        created_by_id=admin.id,
        status=DeliverableStatus.APPROVED,
    )
    tools = _PublishingTools()
    runtime = SkillRuntime(tool_executor=tools)
    request = _capability_request(
        admin=admin,
        account=account,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="content_publishing",
        structured_input={"approved_publish_artifact_id": source.id},
    )

    first = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="content_publishing",
        capability_request=request,
    )
    second = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="content_publishing",
        capability_request=request,
    )

    assert first.report["status"] == "handoff_ready"
    assert "不视为已发布" in first.response
    assert second.report == first.report
    assert tools.calls == 1
