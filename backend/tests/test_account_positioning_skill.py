"""Account-positioning Skill contract and execution regressions."""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import Deliverable, SkillRun
from app.models.enums import AgentCode, DeliverableStatus, DeliverableType
from app.orchestrator.skill_runtime import SkillRuntime
from app.orchestrator.skills.registry import skill_registry
from tests.test_operating_skills import (
    _capability_request,
    _Harness,
    _scope,
    _Tools,
)


class _PositioningHarness(_Harness):
    async def execute(self, *args, **kwargs):
        result = await super().execute(*args, **kwargs)
        result.output.clear()
        result.output.update(
            {
                "positioning_statement": "帮助本地车主用可验证案例选择合适的玻璃膜。",
                "audience": ["重视隔热与隐私的本地车主"],
                "content_pillars": ["实测对比", "施工避坑", "真实案例"],
                "tone": "专业、直接、不过度承诺",
                "boundaries": ["不承诺绝对隔热", "不虚构施工案例"],
            }
        )
        return result


class _AcceptingCritic:
    async def review(self, **_kwargs):
        return SimpleNamespace(passed=True, score=93, issues=[], suggestions=[])


@pytest.mark.asyncio
async def test_account_positioning_runs_positioning_expert_and_persists_evidence(
    session, admin
) -> None:
    account, thread, turn, run = await _scope(
        session,
        admin,
        key="account-positioning",
        message="帮我明确账号定位，目标是获取本地车主咨询",
    )
    harness = _PositioningHarness()

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
        skill_code="account_positioning",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="account_positioning",
            structured_input={
                "business_goal": "获取本地车主咨询",
                "target_audience": "关注隔热和隐私的车主",
                "differentiation_constraints": ["不做低价承诺"],
            },
        ),
    )

    assert result.status == "completed"
    assert result.artifact_type == "account_positioning"
    assert harness.calls == [AgentCode.POSITIONING]
    assert result.report["positioning_statement"]
    assert result.report["boundaries"] == ["不承诺绝对隔热", "不虚构施工案例"]
    assert result.report["evidence_refs"]
    assert result.report["participating_experts"] == [AgentCode.POSITIONING.value]
    skill_run = await session.scalar(select(SkillRun).where(SkillRun.run_id == run.id))
    assert skill_run is not None
    assert skill_run.input_snapshot["business_goal"] == "获取本地车主咨询"
    deliverable = await session.get(Deliverable, result.artifact_id)
    assert deliverable is not None
    assert deliverable.type is DeliverableType.POSITIONING_STRATEGY
    assert deliverable.status is DeliverableStatus.PENDING_REVIEW


def test_account_positioning_is_registered_as_a_required_critic_skill() -> None:
    definition = skill_registry.get("account_positioning")

    assert definition.expert_codes == (AgentCode.POSITIONING.value,)
    assert definition.tool_codes == ("account.profile", "account.data_context")
    assert definition.critic_policy == "required"
