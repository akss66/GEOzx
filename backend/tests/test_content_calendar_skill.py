"""Content-calendar Skill contract regression."""

from types import SimpleNamespace

import pytest

from app.models.enums import AgentCode, DeliverableStatus
from app.orchestrator.skill_runtime import SkillRuntime
from tests.test_operating_skills import _capability_request, _Harness, _scope, _Tools
from tests.test_visual_brief_skill import _source


class _CalendarHarness(_Harness):
    async def execute(self, *args, **kwargs):
        result = await super().execute(*args, **kwargs)
        result.output.clear()
        result.output.update(
            {
                "items": [
                    {
                        "date": "2026-08-05",
                        "title": "隔热膜实测",
                        "owner": "运营",
                        "readiness": "ready",
                        "dependencies": [],
                    }
                ]
            }
        )
        return result


class _AcceptingCritic:
    async def review(self, **_kwargs):
        return SimpleNamespace(passed=True, score=91, issues=[], suggestions=[])


@pytest.mark.asyncio
async def test_content_calendar_persists_dates_owners_readiness_and_dependencies(
    session, admin
):
    account, thread, turn, run = await _scope(
        session, admin, key="content-calendar", message="把确认选题排到未来 7 天"
    )
    source = await _source(
        session,
        account_id=account.id,
        created_by_id=admin.id,
        status=DeliverableStatus.APPROVED,
    )

    result = await SkillRuntime(
        tool_executor=_Tools(),
        harness=_CalendarHarness(),
        critic=_AcceptingCritic(),
    ).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="content_calendar_planning",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="content_calendar_planning",
            structured_input={"source_artifact_ids": [source.id], "days": 7},
        ),
    )

    assert result.status == "completed"
    assert result.report["days"] == 7
    assert result.report["items"][0] == {
        "date": "2026-08-05",
        "title": "隔热膜实测",
        "owner": "运营",
        "readiness": "ready",
        "dependencies": [],
    }
    assert result.report["participating_experts"] == [AgentCode.OPERATOR.value]
