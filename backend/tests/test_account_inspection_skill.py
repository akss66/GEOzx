"""Contract tests for the bounded one-click account-inspection Skill."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import (
    Account,
    AgentInvocation,
    AgentQualityScore,
    AgentRun,
    AgentToolCall,
    BrainTask,
    ContentItem,
    ConversationThread,
    ConversationTurn,
    Deliverable,
    SkillRun,
    StrategyPlan,
)
from app.models.enums import (
    AccountStatus,
    AgentCode,
    AgentInvocationStatus,
    BrainTaskStatus,
    DeliverableStatus,
    Platform,
    UserRole,
)
from app.orchestrator.skill_runtime import SkillExecutionResult, SkillRuntime
from app.orchestrator.skills.account_inspection import (
    ACCOUNT_INSPECTION_SKILL,
    AccountInspectionReport,
)
from app.orchestrator.tool_executor import DurableToolExecutor
from app.schemas.conversation import (
    CreateConversationTurnRequest,
    TurnExecutionMode,
    TurnRouteDecision,
)
from app.services.turn_execution import execute_conversation_turn
from app.tools import ToolAdapter, ToolExecutionContext, ToolSpec


async def _conversation_scope(session, admin, *, key: str):
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname=f"account-{key}",
        status=AccountStatus.ACTIVE,
        auth={"auth_status": "authorized", "data_sync_status": "ready"},
    )
    session.add(account)
    await session.flush()
    thread = ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title=f"thread-{key}",
    )
    session.add(thread)
    await session.flush()
    turn = ConversationTurn(
        thread_id=thread.id,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id=key,
        user_input="请帮我做一次账号体检",
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


class _FakeTools:
    def __init__(self, *, sufficient: bool) -> None:
        self.sufficient = sufficient
        self.calls: list[str] = []
        sufficient_flag = sufficient
        calls = self.calls

        class _EmptyParams(BaseModel):
            model_config = ConfigDict(extra="forbid")

        class _DaysParams(BaseModel):
            model_config = ConfigDict(extra="forbid")

            days: int = 30

        async def profile(_params: _EmptyParams, context: ToolExecutionContext) -> dict:
            calls.append("account.profile")
            return {
                "account_id": context.account_id,
                "nickname": "测试账号",
                "platform": "douyin",
                "status": "active",
                "auth_status": "authorized",
                "data_sync_status": "ready",
            }

        async def data_context(_params: _DaysParams, context: ToolExecutionContext) -> dict:
            calls.append("account.data_context")
            return {
                "account_id": context.account_id,
                "period": {"days": 30, "start": "2026-06-29", "end": "2026-07-28"},
                "coverage": {"content_metrics": "available" if sufficient_flag else "missing"},
                "metrics": (
                    {
                        "play": {
                            "value": 1200,
                            "evidence_refs": [{"kind": "data_import_batch", "id": 7}],
                        },
                        "like_count": {
                            "value": 86,
                            "evidence_refs": [{"kind": "data_import_batch", "id": 7}],
                        },
                        "comment_count": {
                            "value": 12,
                            "evidence_refs": [{"kind": "data_import_batch", "id": 7}],
                        },
                    }
                    if sufficient_flag
                    else {}
                ),
                "sources": (
                    [{"batch_id": 7, "source_kind": "platform_export"}] if sufficient_flag else []
                ),
                "content_snapshot_count": 3 if sufficient_flag else 0,
                "account_snapshot_count": 1 if sufficient_flag else 0,
            }

        self._executor = DurableToolExecutor(
            ToolAdapter(
                [
                    ToolSpec(
                        name="account.profile",
                        handler=profile,
                        params_model=_EmptyParams,
                        side_effect_level="read",
                        allowed_roles=frozenset({UserRole.ADMIN, UserRole.USER}),
                    ),
                    ToolSpec(
                        name="account.data_context",
                        handler=data_context,
                        params_model=_DaysParams,
                        side_effect_level="read",
                        allowed_roles=frozenset({UserRole.ADMIN, UserRole.USER}),
                    ),
                ]
            )
        )

    async def execute(self, **kwargs):
        return await self._executor.execute(**kwargs)


class _FakeHarness:
    def __init__(self) -> None:
        self.calls: list[AgentCode] = []

    async def execute(self, *args, **kwargs):
        self.calls.append(kwargs["code"])
        session = args[0]
        scope = kwargs["scope"]
        invocation = AgentInvocation(
            task_id=kwargs["task"].id,
            run_id=scope.run_id,
            skill_run_id=scope.skill_run_id,
            thread_id=scope.thread_id,
            turn_id=scope.turn_id,
            step_key=kwargs["step_key"],
            attempt=kwargs["attempt"],
            agent_code=kwargs["code"],
            agent_name=kwargs["code"].value,
            status=AgentInvocationStatus.DONE,
            output_summary=f"{kwargs['code'].value} output",
            upstream=[{"trace_only_output": {"summary": "output"}}],
        )
        session.add(invocation)
        await session.commit()
        await session.refresh(invocation)
        return SimpleNamespace(
            invocation=invocation,
            deliverable=None,
            output={"summary": f"{kwargs['code'].value} output"},
        )


class _RevisionHarness(_FakeHarness):
    async def execute(self, *args, **kwargs):
        result = await super().execute(*args, **kwargs)
        if kwargs["code"] is AgentCode.OPERATOR:
            revised = kwargs["attempt"] > 0
            result.output = {
                "period": "最近30天",
                "summary": "已完成账号体检",
                "key_metrics": {"play": 1200},
                "highlights": ["内容已有稳定播放基础"],
                "issues": ["2秒跳出率偏高"],
                "optimization_suggestions": [
                    (
                        "前3秒分别测试提问式、温差对比式和案例结果式开场，"
                        "每种连续发布3条并对比2秒跳出率。"
                        if revised
                        else "优化视频开头"
                    )
                ],
            }
        return result


class _PassingCritic:
    def __init__(self, outcomes: list[bool] | None = None) -> None:
        self.outcomes = list(outcomes or [True])
        self.calls = 0
        self.reports: list[dict] = []

    async def review(self, **_kwargs):
        self.reports.append(dict(_kwargs["report"]))
        passed = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        iteration = _kwargs["iteration"]
        invocation = _kwargs["invocation"]
        task = _kwargs["task"]
        session = _kwargs["session"]
        score = AgentQualityScore(
            org_id=task.org_id,
            task_id=task.id,
            run_id=invocation.run_id,
            thread_id=invocation.thread_id,
            turn_id=invocation.turn_id,
            skill_run_id=invocation.skill_run_id,
            invocation_id=invocation.id,
            deliverable_id=None,
            score=92 if passed else 60,
            dimensions={"factual_accuracy": 92 if passed else 60},
            issues=[] if passed else ["建议缺少证据"],
            suggestions=[] if passed else ["按证据修订建议"],
            passed=passed,
            iteration=iteration,
            evidence_refs=list(_kwargs["evidence_refs"]),
        )
        session.add(score)
        await session.commit()
        self.calls += 1
        return SimpleNamespace(
            passed=passed,
            score=score.score,
            issues=score.issues,
            suggestions=score.suggestions,
        )


@pytest.mark.asyncio
async def test_account_inspection_reports_missing_data_without_fabricated_metrics(
    session,
    admin,
) -> None:
    account, thread, turn, run = await _conversation_scope(session, admin, key="inspection-missing")
    runtime = SkillRuntime(
        tool_executor=_FakeTools(sufficient=False),
        harness=_FakeHarness(),
        critic=_PassingCritic(),
    )

    result = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="account_inspection",
        days=30,
    )

    report = AccountInspectionReport.model_validate(result.report)
    assert report.data_sufficiency == "insufficient"
    assert report.missing_data
    assert report.key_metrics == []
    assert "无法" in report.summary
    assert all("output" not in finding for finding in report.findings)
    assert report.account_id == account.id


@pytest.mark.asyncio
async def test_account_inspection_retryable_infrastructure_failure_bubbles_to_worker(
    session,
    admin,
) -> None:
    _account, thread, turn, run = await _conversation_scope(
        session, admin, key="inspection-retryable"
    )

    class RetryableTools:
        async def execute(self, **_kwargs):
            raise HTTPException(status_code=503, detail="provider-secret")

    runtime = SkillRuntime(
        tool_executor=RetryableTools(),
        harness=_FakeHarness(),
        critic=_PassingCritic(),
    )

    with pytest.raises(HTTPException) as caught:
        await runtime.execute(
            session,
            user=admin,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="account_inspection",
            days=30,
        )

    assert caught.value.status_code == 503
    skill_run = await session.scalar(select(SkillRun))
    task = await session.scalar(select(BrainTask))
    assert skill_run is not None
    assert task is not None
    assert skill_run.status == "running"
    assert task.status == BrainTaskStatus.RUNNING


@pytest.mark.asyncio
async def test_account_inspection_runs_bounded_graph_and_persists_one_artifact(
    session,
    admin,
) -> None:
    account, thread, turn, run = await _conversation_scope(
        session, admin, key="inspection-complete"
    )
    tools = _FakeTools(sufficient=True)
    harness = _FakeHarness()
    runtime = SkillRuntime(
        tool_executor=tools,
        harness=harness,
        critic=_PassingCritic(),
    )

    result = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="account_inspection",
        days=30,
    )

    assert result.status == "completed"
    assert result.artifact_type == "account_inspection_report"
    assert tools.calls == ["account.profile", "account.data_context"]
    assert harness.calls == [
        AgentCode.POSITIONING,
        AgentCode.CONTENT_DIRECTOR,
        AgentCode.OPERATOR,
    ]
    assert await session.scalar(select(func.count(SkillRun.id))) == 1
    assert await session.scalar(select(func.count(AgentToolCall.id))) == 2
    assert await session.scalar(select(func.count(AgentInvocation.id))) == 3
    assert await session.scalar(select(func.count(Deliverable.id))) == 1
    assert await session.scalar(select(func.count(StrategyPlan.id))) == 0
    persisted = await session.scalar(select(Deliverable))
    assert persisted is not None
    content = await session.get(ContentItem, persisted.content_item_id)
    assert content is not None
    assert content.account_id == account.id
    assert persisted.thread_id == thread.id
    assert persisted.turn_id == turn.id
    assert persisted.run_id == run.id
    assert persisted.skill_run_id == result.skill_run_id
    assert persisted.payload["artifact_type"] == "account_inspection_report"
    assert persisted.payload["data_sufficiency"] == "sufficient"
    assert persisted.payload["next_action"]
    assert "recommendations" not in persisted.payload
    assert persisted.payload["optimization_suggestions"]
    assert persisted.payload["evidence_refs"] == [{"kind": "data_import_batch", "id": 7}]
    assert (
        await session.scalar(
            select(func.count(AgentQualityScore.id)).where(
                AgentQualityScore.deliverable_id == persisted.id
            )
        )
        == 1
    )
    for model in (AgentToolCall, AgentInvocation, AgentQualityScore):
        rows = list(await session.scalars(select(model)))
        assert {row.skill_run_id for row in rows} == {result.skill_run_id}
        assert {row.thread_id for row in rows} == {thread.id}
        assert {row.turn_id for row in rows} == {turn.id}


@pytest.mark.asyncio
async def test_critic_reviews_the_revised_operator_report(
    session,
    admin,
) -> None:
    _account, thread, turn, run = await _conversation_scope(
        session, admin, key="inspection-critic-revision"
    )
    critic = _PassingCritic([False, True])
    harness = _RevisionHarness()
    result = await SkillRuntime(
        tool_executor=_FakeTools(sufficient=True),
        harness=harness,
        critic=critic,
    ).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="account_inspection",
        days=30,
    )

    assert result.status == "completed"
    assert harness.calls == [
        AgentCode.POSITIONING,
        AgentCode.CONTENT_DIRECTOR,
        AgentCode.OPERATOR,
        AgentCode.OPERATOR,
    ]
    assert critic.calls == 2
    assert critic.reports[0]["recommendations"] == ["优化视频开头"]
    assert "每种连续发布3条" in critic.reports[1]["recommendations"][0]
    assert "每种连续发布3条" in result.report["recommendations"][0]


def test_account_inspection_definition_freezes_one_explicit_graph() -> None:
    assert ACCOUNT_INSPECTION_SKILL.code == "account_inspection"
    assert ACCOUNT_INSPECTION_SKILL.version > 0
    assert ACCOUNT_INSPECTION_SKILL.tool_codes == (
        "account.profile",
        "account.data_context",
    )
    assert ACCOUNT_INSPECTION_SKILL.expert_codes == (
        "01-positioning",
        "02-content-director",
        "06-operator",
    )
    assert ACCOUNT_INSPECTION_SKILL.artifact_type == "account_inspection_report"


@pytest.mark.asyncio
async def test_account_inspection_critic_retry_budget_delivers_for_human_review(
    session,
    admin,
) -> None:
    _account, thread, turn, run = await _conversation_scope(
        session, admin, key="inspection-critic-blocked"
    )
    critic = _PassingCritic([False, False, False])
    harness = _FakeHarness()
    runtime = SkillRuntime(
        tool_executor=_FakeTools(sufficient=True),
        harness=harness,
        critic=critic,
    )

    result = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="account_inspection",
        days=30,
    )

    assert result.status == "needs_review"
    assert result.error_code is None
    assert result.artifact_id is not None
    assert result.report["critic"]["passed"] is False
    assert "人工确认" in result.response
    assert critic.calls == 3
    assert harness.calls == [
        AgentCode.POSITIONING,
        AgentCode.CONTENT_DIRECTOR,
        AgentCode.OPERATOR,
        AgentCode.OPERATOR,
        AgentCode.OPERATOR,
    ]
    assert await session.scalar(select(func.count(AgentQualityScore.id))) == 3
    assert await session.scalar(select(func.count(Deliverable.id))) == 1
    deliverable = await session.get(Deliverable, result.artifact_id)
    assert deliverable is not None
    assert deliverable.status is DeliverableStatus.PENDING_REVIEW
    latest_quality = await session.scalar(
        select(AgentQualityScore)
        .where(AgentQualityScore.skill_run_id == result.skill_run_id)
        .order_by(AgentQualityScore.iteration.desc())
    )
    assert latest_quality is not None
    assert latest_quality.deliverable_id == deliverable.id
    assert await session.scalar(select(func.count(StrategyPlan.id))) == 0


@pytest.mark.asyncio
async def test_account_inspection_duplicate_execution_reuses_terminal_skill_run(
    session,
    admin,
) -> None:
    _account, thread, turn, run = await _conversation_scope(
        session, admin, key="inspection-idempotent"
    )
    tools = _FakeTools(sufficient=True)
    harness = _FakeHarness()
    critic = _PassingCritic()
    runtime = SkillRuntime(tool_executor=tools, harness=harness, critic=critic)

    first = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="account_inspection",
    )
    duplicate = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="account_inspection",
    )

    assert duplicate == first
    assert tools.calls == ["account.profile", "account.data_context"]
    assert len(harness.calls) == 3
    assert critic.calls == 1
    assert await session.scalar(select(func.count(SkillRun.id))) == 1
    assert await session.scalar(select(func.count(Deliverable.id))) == 1


@pytest.mark.asyncio
async def test_account_inspection_concurrent_creator_reuses_unique_winner(
    session,
    admin,
    monkeypatch,
) -> None:
    _account, thread, turn, run = await _conversation_scope(
        session, admin, key="inspection-concurrent-winner"
    )
    tools = _FakeTools(sufficient=True)
    harness = _FakeHarness()
    critic = _PassingCritic()
    runtime = SkillRuntime(tool_executor=tools, harness=harness, critic=critic)
    original_flush = session.flush
    original_commit = session.commit
    injected = False
    account_id = thread.account_id

    async def flush_with_concurrent_winner(*args, **kwargs):
        nonlocal injected
        pending = next(
            (item for item in session.new if isinstance(item, SkillRun)),
            None,
        )
        if pending is None or injected:
            return await original_flush(*args, **kwargs)
        injected = True
        values = {
            "org_id": pending.org_id,
            "thread_id": pending.thread_id,
            "turn_id": pending.turn_id,
            "run_id": pending.run_id,
            "task_id": pending.task_id,
            "idempotency_key": pending.idempotency_key,
            "skill_code": pending.skill_code,
            "skill_version": pending.skill_version,
        }
        await session.rollback()
        session.add(
            SkillRun(
                **values,
                status="completed",
                input_snapshot={"account_id": account_id, "days": 30},
                output_snapshot={
                    "status": "completed",
                    "task_id": values["task_id"],
                    "artifact_id": 501,
                    "artifact_type": "account_inspection_report",
                    "report": {"summary": "winner"},
                    "response": "并发执行已由唯一 winner 完成",
                },
            )
        )
        await original_commit()
        raise IntegrityError("INSERT skill_runs", {}, RuntimeError("unique"))

    monkeypatch.setattr(session, "flush", flush_with_concurrent_winner)
    result = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="account_inspection",
    )

    assert result.status == "completed"
    assert result.artifact_id == 501
    assert result.report == {"summary": "winner"}
    assert tools.calls == []
    assert harness.calls == []
    assert critic.calls == 0
    winners = list(await session.scalars(select(SkillRun)))
    assert len(winners) == 1
    assert winners[0].skill_version == ACCOUNT_INSPECTION_SKILL.version


@pytest.mark.asyncio
async def test_account_inspection_active_owner_reuses_running_winner_without_reexecution(
    session,
    admin,
) -> None:
    _account, thread, turn, run = await _conversation_scope(
        session, admin, key="inspection-running-winner"
    )
    now = datetime.now(UTC)
    run.status = "running"
    run.phase = "skill_runtime"
    run.lease_owner = "skill-owner-a"
    run.heartbeat_at = now
    run.leased_until = now + timedelta(minutes=5)
    content = ContentItem(
        account_id=thread.account_id,
        created_by_id=admin.id,
        title="正在执行的账号体检",
    )
    session.add(content)
    await session.flush()
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        content_item_id=content.id,
        title="正在执行的账号体检",
        status=BrainTaskStatus.RUNNING,
        runtime_mode="skill",
    )
    session.add(task)
    await session.flush()
    run.task_id = task.id
    winner = SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        task_id=task.id,
        idempotency_key=(f"skill:account_inspection:v{ACCOUNT_INSPECTION_SKILL.version}"),
        skill_code="account_inspection",
        skill_version=ACCOUNT_INSPECTION_SKILL.version,
        status="running",
        input_snapshot={"account_id": thread.account_id, "days": 30},
        output_snapshot={},
    )
    session.add(winner)
    await session.flush()
    running_tool = AgentToolCall(
        org_id=admin.org_id,
        task_id=task.id,
        skill_run_id=winner.id,
        thread_id=thread.id,
        turn_id=turn.id,
        tool_code="account.profile",
        tool_name="Account profile",
        idempotency_key=f"{winner.id}:account.profile",
        status="running",
    )
    session.add(running_tool)
    await session.commit()
    winner_id = winner.id
    tool_id = running_tool.id
    tools = _FakeTools(sufficient=True)
    harness = _FakeHarness()
    critic = _PassingCritic()

    result = await SkillRuntime(
        tool_executor=tools,
        harness=harness,
        critic=critic,
    ).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="account_inspection",
    )

    assert result.status == "running"
    assert result.skill_run_id == winner_id
    assert result.response == "账号体检正在执行中，请稍候。"
    assert tools.calls == []
    assert harness.calls == []
    assert critic.calls == 0
    persisted_winner = await session.get(SkillRun, winner_id)
    persisted_tool = await session.get(AgentToolCall, tool_id)
    assert persisted_winner is not None
    assert persisted_winner.status == "running"
    assert persisted_winner.error_code is None
    assert persisted_tool is not None
    assert persisted_tool.status == "running"

    active_lease_until = run.leased_until
    routed = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        CreateConversationTurnRequest(
            client_message_id=turn.client_message_id,
            message=turn.user_input,
            requested_skill_code="account_inspection",
            execution_preference="FORMAL_TASK",
        ),
    )

    await session.refresh(run)
    assert routed.status == "running"
    assert run.status == "running"
    assert run.phase == "skill_runtime"
    assert run.lease_owner == "skill-owner-a"
    assert run.leased_until == active_lease_until
    assert run.finished_at is None


@pytest.mark.asyncio
async def test_account_inspection_crash_replay_closes_stale_running_side_effects(
    session,
    admin,
) -> None:
    _account, thread, turn, run = await _conversation_scope(
        session, admin, key="inspection-crash-replay"
    )
    now = datetime.now(UTC)
    run.status = "running"
    run.phase = "skill_runtime"
    run.lease_owner = "crashed-skill-owner"
    run.heartbeat_at = now - timedelta(minutes=5)
    run.leased_until = now - timedelta(seconds=1)
    content = ContentItem(
        account_id=thread.account_id,
        created_by_id=admin.id,
        title="Interrupted account inspection",
    )
    session.add(content)
    await session.flush()
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        content_item_id=content.id,
        title="Interrupted account inspection",
        status=BrainTaskStatus.RUNNING,
        runtime_mode="skill",
    )
    session.add(task)
    await session.flush()
    run.task_id = task.id
    winner = SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        task_id=task.id,
        idempotency_key=(f"skill:account_inspection:v{ACCOUNT_INSPECTION_SKILL.version}"),
        skill_code="account_inspection",
        skill_version=ACCOUNT_INSPECTION_SKILL.version,
        status="running",
        input_snapshot={"account_id": thread.account_id, "days": 30},
        output_snapshot={},
    )
    session.add(winner)
    await session.flush()
    running_tool = AgentToolCall(
        org_id=admin.org_id,
        task_id=task.id,
        skill_run_id=winner.id,
        thread_id=thread.id,
        turn_id=turn.id,
        tool_code="account.profile",
        tool_name="Account profile",
        idempotency_key=f"{winner.id}:account.profile",
        status="running",
        meta={"arguments": {}},
    )
    session.add(running_tool)
    await session.commit()
    tools = _FakeTools(sufficient=True)
    harness = _FakeHarness()
    critic = _PassingCritic()

    result = await SkillRuntime(
        tool_executor=tools,
        harness=harness,
        critic=critic,
    ).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="account_inspection",
    )

    assert result.status == "failed"
    assert result.error_code == "SKILL_EXECUTION_INTERRUPTED"
    assert result.skill_run_id == winner.id
    assert tools.calls == []
    assert harness.calls == []
    assert critic.calls == 0
    assert await session.scalar(select(func.count(Deliverable.id))) == 0
    await session.refresh(winner)
    await session.refresh(running_tool)
    await session.refresh(task)
    await session.refresh(run)
    await session.refresh(turn)
    assert winner.status == "failed"
    assert running_tool.status == "failed"
    assert running_tool.error == "SKILL_EXECUTION_INTERRUPTED"
    assert task.status is BrainTaskStatus.FAILED
    assert run.status == "failed"
    assert run.phase == "failed"
    assert run.finished_at is not None
    assert run.lease_owner is None
    assert run.leased_until is None
    assert run.error_code == "SKILL_EXECUTION_INTERRUPTED"
    assert run.result_payload["status"] == "failed"
    assert turn.assistant_response == result.response


@pytest.mark.asyncio
async def test_account_inspection_blocks_tool_result_from_another_account(
    session,
    admin,
) -> None:
    _account, thread, turn, run = await _conversation_scope(
        session, admin, key="inspection-scope-mismatch"
    )
    tools = _FakeTools(sufficient=True)
    original_execute = tools.execute

    async def execute_with_wrong_scope(**kwargs):
        outcome = await original_execute(**kwargs)
        if kwargs["request"].tool_code == "account.data_context":
            return SimpleNamespace(
                status=outcome.status,
                tool_call=outcome.tool_call,
                result={**outcome.result, "account_id": thread.account_id + 999},
            )
        return outcome

    tools.execute = execute_with_wrong_scope
    harness = _FakeHarness()
    result = await SkillRuntime(
        tool_executor=tools,
        harness=harness,
        critic=_PassingCritic(),
    ).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="account_inspection",
    )

    assert result.status == "blocked"
    assert result.error_code == "TOOL_RESULT_SCOPE_MISMATCH"
    assert harness.calls == []
    assert await session.scalar(select(func.count(Deliverable.id))) == 0


@pytest.mark.asyncio
async def test_explicit_and_natural_language_requests_use_same_skill_executor(
    session,
    admin,
    monkeypatch,
) -> None:
    contexts = [
        await _conversation_scope(session, admin, key="inspection-explicit"),
        await _conversation_scope(session, admin, key="inspection-natural"),
    ]
    calls: list[dict] = []

    async def fake_execute(
        runtime_session,
        *,
        user,
        thread,
            turn,
            run,
            skill_code,
            capability_request,
        ):
        content = ContentItem(
            account_id=thread.account_id,
            created_by_id=user.id,
            title="账号体检",
        )
        runtime_session.add(content)
        await runtime_session.flush()
        task = BrainTask(
            org_id=user.org_id,
            created_by_id=user.id,
            content_item_id=content.id,
            title="账号体检",
            status=BrainTaskStatus.COMPLETED,
            runtime_mode="skill",
        )
        runtime_session.add(task)
        await runtime_session.flush()
        run.task_id = task.id
        await runtime_session.commit()
        account_id = thread.account_id
        task_id = task.id
        calls.append(
                {
                    "skill_code": skill_code,
                    "days": capability_request.structured_input.get("days", 30),
                    "account_id": account_id,
                }
        )
        # The durable Skill runtime commits several ledgers. Reproduce the
        # production state where request-scoped ORM objects are expired before
        # the Turn delivery layer resumes.
        runtime_session.expire_all()
        return SkillExecutionResult(
            status="completed",
            skill_run_id=100 + len(calls),
            task_id=task_id,
            artifact_id=200 + len(calls),
            artifact_type="account_inspection_report",
            report={"summary": "完成"},
            response="账号体检已完成",
        )

    async def classify(*_args, **_kwargs):
        return TurnRouteDecision(
            mode=TurnExecutionMode.SKILL,
            intent="account_inspection",
            confidence=0.99,
            reason="natural language account inspection",
            skill_code="account_inspection",
            requires_account_context=True,
            requires_operation_task=True,
        )

    monkeypatch.setattr("app.services.turn_execution.skill_runtime.execute", fake_execute)
    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    explicit = await execute_conversation_turn(
        session,
        admin,
        contexts[0][2],
        contexts[0][3],
        CreateConversationTurnRequest(
            client_message_id="inspection-explicit",
            message="请帮我做一次账号体检",
            requested_skill_code="account_inspection",
            execution_preference="FORMAL_TASK",
        ),
    )
    # The natural-language case represents a separate HTTP request, whose Turn
    # and Run would be loaded afresh even though this unit test shares a session.
    await session.refresh(contexts[1][2])
    await session.refresh(contexts[1][3])
    natural = await execute_conversation_turn(
        session,
        admin,
        contexts[1][2],
        contexts[1][3],
        CreateConversationTurnRequest(
            client_message_id="inspection-natural",
            message="请帮我做一次账号体检",
        ),
    )

    assert explicit.mode is TurnExecutionMode.SKILL
    assert natural.mode is TurnExecutionMode.SKILL
    assert [item["skill_code"] for item in calls] == [
        "account_inspection",
        "account_inspection",
    ]
    assert [item["days"] for item in calls] == [30, 30]
    assert {
        explicit.projections[0]["artifact_type"],
        natural.projections[0]["artifact_type"],
    } == {"account_inspection_report"}


@pytest.mark.asyncio
async def test_natural_language_skill_alias_is_normalized_to_public_executor(
    session,
    admin,
    monkeypatch,
) -> None:
    _account, _thread, turn, run = await _conversation_scope(
        session,
        admin,
        key="inspection-model-alias",
    )
    turn.user_input = "帮我做一次账号健康检查"
    await session.commit()
    received: list[str] = []

    async def classify(*_args, **_kwargs):
        return TurnRouteDecision(
            mode=TurnExecutionMode.SKILL,
            intent="account_health_check",
            confidence=0.98,
            reason="model selected a semantic alias",
            skill_code="account_health_check",
            requires_account_context=True,
            requires_operation_task=True,
        )

    async def fake_execute(runtime_session, **kwargs):
        received.append(kwargs["skill_code"])
        task = BrainTask(
            org_id=admin.org_id,
            created_by_id=admin.id,
            title="Alias inspection",
            status=BrainTaskStatus.COMPLETED,
            runtime_mode="skill",
        )
        runtime_session.add(task)
        await runtime_session.flush()
        kwargs["run"].task_id = task.id
        await runtime_session.commit()
        return SkillExecutionResult(
            status="completed",
            skill_run_id=999,
            task_id=task.id,
            artifact_id=1000,
            artifact_type="account_inspection_report",
            report={"summary": "completed"},
            response="账号体检已完成。",
        )

    monkeypatch.setattr(
        "app.services.turn_execution.brain_intelligence.classify_turn",
        classify,
    )
    monkeypatch.setattr(
        "app.services.turn_execution.skill_runtime.execute",
        fake_execute,
    )

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        CreateConversationTurnRequest(
            client_message_id=turn.client_message_id,
            message="帮我做一次账号健康检查",
        ),
    )

    assert received == ["account_inspection"]
    assert result.status == "completed"
