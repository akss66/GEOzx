"""Task 6 regressions for generic quality gates and legacy recovery."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models import Deliverable, SkillRun
from app.models.enums import DeliverableStatus
from app.orchestrator.skill_runtime import SkillRecoveryConflict, SkillRuntime
from app.orchestrator.skills.registry import skill_registry
from tests.test_operating_skills import _Harness, _scope, _Tools


class _CountingTools(_Tools):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    async def execute(self, **kwargs):
        self.calls.append(str(kwargs["request"].tool_code))
        return await super().execute(**kwargs)


@pytest.mark.asyncio
async def test_required_generic_skill_cannot_bypass_failed_quality_gate(
    session, admin, monkeypatch
) -> None:
    _account, thread, turn, run = await _scope(
        session, admin, key="required-generic-critic", message="Plan topics"
    )
    required = replace(
        skill_registry.get("topic_planning"),
        critic_policy="required",
    )
    original_get = skill_registry.get
    monkeypatch.setattr(
        skill_registry,
        "get",
        lambda code, version=None: (
            required if code == required.code else original_get(code, version)
        ),
    )

    class RejectingCritic:
        calls = 0

        async def review(self, **_kwargs):
            self.calls += 1
            return SimpleNamespace(
                passed=False,
                score=55,
                issues=["needs review"],
                suggestions=[],
            )

    critic = RejectingCritic()
    result = await SkillRuntime(
        tool_executor=_Tools(),
        harness=_Harness(),
        critic=critic,
    ).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="topic_planning",
    )

    assert critic.calls == 1
    assert result.status == "completed"
    assert result.artifact_id is not None
    deliverable = await session.get(Deliverable, result.artifact_id)
    assert deliverable is not None
    assert deliverable.status is DeliverableStatus.PENDING_REVIEW
    assert await session.scalar(select(func.count(Deliverable.id))) == 1


@pytest.mark.asyncio
async def test_required_generic_skill_failed_quality_gate_is_terminal_and_idempotent(
    session, admin, monkeypatch
) -> None:
    _account, thread, turn, run = await _scope(
        session,
        admin,
        key="required-generic-critic-terminal",
        message="Plan topics",
    )
    required = replace(
        skill_registry.get("topic_planning"),
        critic_policy="required",
    )
    original_get = skill_registry.get
    monkeypatch.setattr(
        skill_registry,
        "get",
        lambda code, version=None: (
            required if code == required.code else original_get(code, version)
        ),
    )

    class RejectingCritic:
        calls = 0

        async def review(self, **_kwargs):
            self.calls += 1
            return SimpleNamespace(
                passed=False,
                score=55,
                issues=["needs review"],
                suggestions=[],
            )

    tools = _CountingTools()
    harness = _Harness()
    critic = RejectingCritic()
    runtime = SkillRuntime(
        tool_executor=tools,
        harness=harness,
        critic=critic,
    )

    first = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="topic_planning",
    )
    duplicate = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="topic_planning",
    )

    assert first.status == "completed"
    assert first.artifact_id is not None
    assert duplicate == first
    assert critic.calls == 1
    assert tools.calls == ["account.profile", "account.data_context"]
    assert harness.calls == [required.expert_stages[0][0]]
    assert await session.scalar(select(func.count(Deliverable.id))) == 1


@pytest.mark.asyncio
async def test_none_generic_skill_never_calls_critic(session, admin) -> None:
    _account, thread, turn, run = await _scope(
        session, admin, key="none-generic-critic", message="Plan topics"
    )

    class UnexpectedCritic:
        calls = 0

        async def review(self, **_kwargs):
            self.calls += 1
            raise AssertionError("critic_policy=none must not call Critic")

    critic = UnexpectedCritic()
    result = await SkillRuntime(
        tool_executor=_Tools(),
        harness=_Harness(),
        critic=critic,
    ).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="topic_planning",
    )

    assert result.status == "completed"
    assert critic.calls == 0


@pytest.mark.asyncio
async def test_unique_legacy_terminal_skill_run_is_reused_without_execution(
    session, admin, monkeypatch
) -> None:
    account, thread, turn, run = await _scope(
        session, admin, key="legacy-terminal-reuse", message="Plan topics"
    )
    snapshot = {"account_id": account.id, "days": 30, "topic_count": 5}
    legacy = SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        task_id=None,
        idempotency_key="skill:topic_planning:v1",
        skill_code="topic_planning",
        skill_version=1,
        status="completed",
        input_snapshot=snapshot,
        output_snapshot={
            "status": "completed",
            "task_id": None,
            "artifact_id": 991,
            "artifact_type": "topic_plan",
            "report": {"summary": "persisted"},
            "response": "reused",
        },
    )
    session.add(legacy)
    await session.commit()
    v1 = skill_registry.get("topic_planning", version=1)
    v2 = replace(v1, version=2)
    original_get = skill_registry.get
    monkeypatch.setattr(
        skill_registry,
        "get",
        lambda code, version=None: (
            (v1 if version == 1 else v2)
            if code == "topic_planning"
            else original_get(code, version)
        ),
    )
    harness = _Harness()

    result = await SkillRuntime(tool_executor=_Tools(), harness=harness).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="topic_planning",
    )

    assert result.artifact_id == 991
    assert result.response == "reused"
    assert harness.calls == []
    assert await session.scalar(select(func.count(SkillRun.id))) == 1


@pytest.mark.asyncio
async def test_multiple_legacy_terminal_candidates_are_ambiguous(session, admin) -> None:
    account, thread, turn, run = await _scope(
        session, admin, key="legacy-terminal-ambiguous", message="Plan topics"
    )
    snapshot = {"account_id": account.id, "days": 30, "topic_count": 5}
    for index, key in enumerate(("skill:topic_planning:v1", "skill:topic_planning")):
        session.add(
            SkillRun(
                org_id=admin.org_id,
                thread_id=thread.id,
                turn_id=turn.id,
                run_id=run.id,
                task_id=None,
                idempotency_key=key,
                skill_code="topic_planning",
                skill_version=1,
                status="completed",
                input_snapshot=snapshot,
                output_snapshot={
                    "status": "completed",
                    "artifact_id": 100 + index,
                },
            )
        )
    await session.commit()

    with pytest.raises(SkillRecoveryConflict, match="SKILL_RECOVERY_AMBIGUOUS"):
        await SkillRuntime(tool_executor=_Tools(), harness=_Harness()).execute(
            session,
            user=admin,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="topic_planning",
        )
