"""Visual-brief Skill source-artifact and output contracts."""

from types import SimpleNamespace

import pytest

from app.models import Account, ContentItem, Deliverable
from app.models.enums import (
    AccountStatus,
    AgentCode,
    DeliverableStatus,
    DeliverableType,
    Platform,
)
from app.orchestrator.skill_runtime import SkillRuntime
from tests.test_operating_skills import _capability_request, _Harness, _scope, _Tools


class _VisualHarness(_Harness):
    async def execute(self, *args, **kwargs):
        result = await super().execute(*args, **kwargs)
        if kwargs["code"] is AgentCode.ART_DIRECTOR:
            result.output.clear()
            result.output.update(
                {
                    "cover_copy": "隔热膜怎么选？先看这三个实测",
                    "composition": "产品实拍与温度计数据左右对比",
                    "shot_list": ["问题开场", "数据实测", "施工细节", "结果总结"],
                    "asset_checklist": ["门店实拍", "温度计", "施工过程素材"],
                    "platform_constraints": ["9:16", "字幕留安全区"],
                }
            )
        return result


class _AcceptingCritic:
    async def review(self, **_kwargs):
        return SimpleNamespace(passed=True, score=92, issues=[], suggestions=[])


async def _source(session, *, account_id: int, created_by_id: int, status):
    content = ContentItem(account_id=account_id, created_by_id=created_by_id, title="source")
    session.add(content)
    await session.flush()
    deliverable = Deliverable(
        content_item_id=content.id,
        agent_code=AgentCode.CONTENT_DIRECTOR.value,
        type=DeliverableType.VIDEO_SCRIPT,
        version=1,
        status=status,
        payload={"title": "source script"},
    )
    session.add(deliverable)
    await session.commit()
    return deliverable


@pytest.mark.asyncio
async def test_visual_brief_consumes_only_confirmed_same_account_artifacts(session, admin):
    account, thread, turn, run = await _scope(
        session, admin, key="visual-brief", message="根据已确认脚本生成视觉 Brief"
    )
    source = await _source(
        session,
        account_id=account.id,
        created_by_id=admin.id,
        status=DeliverableStatus.APPROVED,
    )

    result = await SkillRuntime(
        tool_executor=_Tools(),
        harness=_VisualHarness(),
        critic=_AcceptingCritic(),
    ).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="visual_brief_generation",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="visual_brief_generation",
            structured_input={"source_artifact_ids": [source.id]},
        ),
    )

    assert result.status == "completed"
    assert result.report["source_artifact_ids"] == [source.id]
    assert {item["artifact_id"] for item in result.report["evidence_refs"]} == {source.id}
    assert result.report["cover_copy"]


@pytest.mark.asyncio
async def test_visual_brief_rejects_cross_account_artifact(session, admin):
    account, thread, turn, run = await _scope(
        session, admin, key="visual-cross-account", message="引用其他账号脚本"
    )
    other = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="other",
        status=AccountStatus.ACTIVE,
    )
    session.add(other)
    await session.flush()
    source = await _source(
        session,
        account_id=other.id,
        created_by_id=admin.id,
        status=DeliverableStatus.APPROVED,
    )

    harness = _VisualHarness()
    result = await SkillRuntime(
        tool_executor=_Tools(),
        harness=harness,
        critic=_AcceptingCritic(),
    ).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="visual_brief_generation",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="visual_brief_generation",
            structured_input={"source_artifact_ids": [source.id]},
        ),
    )

    assert result.status == "failed"
    assert result.artifact_id is None
    assert harness.calls == []
