from dataclasses import FrozenInstanceError

import pytest
from pydantic import BaseModel
from sqlalchemy import func, select

from app.models import (
    Account,
    AgentInvocation,
    AgentRun,
    BrainTask,
    ContentItem,
    ConversationThread,
    ConversationTurn,
    Deliverable,
    Event,
    Org,
    SkillRun,
    User,
)
from app.models.enums import (
    AgentCode,
    AgentInvocationStatus,
    BrainTaskStatus,
    BrainTaskType,
    DeliverableStatus,
    DeliverableType,
    Platform,
    UserRole,
)
from app.orchestrator.agent_harness import AgentHarness
from app.orchestrator.runtime_scope import RuntimeScope, RuntimeScopeConflict
from app.orchestrator.tool_executor import (
    DurableToolExecutor,
    ToolIdempotencyConflict,
)
from app.schemas.brain import RuntimeToolCall
from app.services.runtime_deliverables import write_runtime_deliverable
from app.tools import ToolAdapter, ToolExecutionContext, ToolSpec


class _EchoParams(BaseModel):
    message: str


async def _runtime_graph(session, user: User, *, suffix: str) -> dict:
    account = Account(
        org_id=user.org_id,
        platform=Platform.DOUYIN,
        nickname=f"account-{suffix}",
    )
    session.add(account)
    await session.flush()
    content = ContentItem(account_id=account.id, title=f"content-{suffix}")
    task = BrainTask(
        org_id=user.org_id,
        created_by_id=user.id,
        content_item_id=None,
        title=f"task-{suffix}",
        type=BrainTaskType.CONTENT_CREATION,
        status=BrainTaskStatus.RUNNING,
    )
    thread = ConversationThread(
        org_id=user.org_id,
        created_by_id=user.id,
        account_id=account.id,
        title=f"thread-{suffix}",
    )
    turn = ConversationTurn(
        thread=thread,
        org_id=user.org_id,
        created_by_id=user.id,
        client_message_id=f"turn-{suffix}",
        user_input=f"inspect {suffix}",
    )
    session.add_all([content, task, thread, turn])
    await session.flush()
    task.content_item_id = content.id
    run = AgentRun(
        org_id=user.org_id,
        requested_by_id=user.id,
        task_id=task.id,
        thread_id=thread.id,
        turn_id=turn.id,
        client_message_id=f"run-{suffix}",
    )
    session.add(run)
    await session.flush()
    skill_run = SkillRun(
        org_id=user.org_id,
        task_id=task.id,
        run_id=run.id,
        thread_id=thread.id,
        turn_id=turn.id,
        idempotency_key=f"skill-{suffix}",
        skill_code="account_inspection",
        skill_version=1,
        status="running",
    )
    session.add(skill_run)
    await session.flush()
    return {
        "account": account,
        "content": content,
        "task": task,
        "thread": thread,
        "turn": turn,
        "run": run,
        "skill_run": skill_run,
    }


async def _scope(session, user: User, graph: dict) -> RuntimeScope:
    scope = await RuntimeScope.from_conversation(
        session,
        user=user,
        thread=graph["thread"],
        turn=graph["turn"],
        run=graph["run"],
    )
    scope = await scope.bind_task(session, graph["task"])
    return await scope.bind_skill(session, graph["skill_run"])


@pytest.mark.asyncio
async def test_runtime_scope_is_immutable_and_validates_the_full_graph(
    session,
    admin,
) -> None:
    graph = await _runtime_graph(session, admin, suffix="valid")

    scope = await _scope(session, admin, graph)

    assert scope == RuntimeScope(
        org_id=admin.org_id,
        user_id=admin.id,
        account_id=graph["account"].id,
        thread_id=graph["thread"].id,
        turn_id=graph["turn"].id,
        run_id=graph["run"].id,
        task_id=graph["task"].id,
        skill_run_id=graph["skill_run"].id,
    )
    with pytest.raises(FrozenInstanceError):
        scope.account_id = 999  # type: ignore[misc]


@pytest.mark.asyncio
async def test_runtime_scope_rejects_cross_owned_graphs_before_writes(
    session,
    admin,
    member,
) -> None:
    graph_a = await _runtime_graph(session, admin, suffix="a")
    graph_b = await _runtime_graph(session, admin, suffix="b")
    scope_a = await _scope(session, admin, graph_a)

    for overrides in (
        {"user": member},
        {"thread": graph_b["thread"]},
        {"turn": graph_b["turn"]},
        {"run": graph_b["run"]},
    ):
        arguments = {
            "user": admin,
            "thread": graph_a["thread"],
            "turn": graph_a["turn"],
            "run": graph_a["run"],
            **overrides,
        }
        with pytest.raises(RuntimeScopeConflict):
            await RuntimeScope.from_conversation(session, **arguments)

    with pytest.raises(RuntimeScopeConflict):
        await scope_a.bind_task(session, graph_b["task"])
    with pytest.raises(RuntimeScopeConflict):
        await scope_a.bind_skill(session, graph_b["skill_run"])

    wrong_account = RuntimeScope(
        **{
            **scope_a.as_dict(),
            "account_id": graph_b["account"].id,
        }
    )
    with pytest.raises(RuntimeScopeConflict):
        await wrong_account.validate(session)


@pytest.mark.asyncio
async def test_runtime_deliverable_rejects_partial_and_cross_account_provenance(
    session,
    admin,
) -> None:
    graph_a = await _runtime_graph(session, admin, suffix="writer-a")
    graph_b = await _runtime_graph(session, admin, suffix="writer-b")
    scope_a = await _scope(session, admin, graph_a)

    with pytest.raises(RuntimeScopeConflict):
        await write_runtime_deliverable(
            session,
            scope=None,
            content=graph_a["content"],
            agent_code=AgentCode.DECISION.value,
            deliverable_type=DeliverableType.REVIEW_REPORT,
            status=DeliverableStatus.PENDING_REVIEW,
            payload={"summary": "partial"},
            legacy_provenance={"thread_id": graph_a["thread"].id},
        )
    with pytest.raises(RuntimeScopeConflict):
        await write_runtime_deliverable(
            session,
            scope=scope_a,
            content=graph_b["content"],
            agent_code=AgentCode.DECISION.value,
            deliverable_type=DeliverableType.REVIEW_REPORT,
            status=DeliverableStatus.PENDING_REVIEW,
            payload={"summary": "wrong account"},
        )
    assert await session.scalar(select(func.count()).select_from(Deliverable)) == 0

    deliverable = await write_runtime_deliverable(
        session,
        scope=scope_a,
        content=graph_a["content"],
        agent_code=AgentCode.DECISION.value,
        deliverable_type=DeliverableType.REVIEW_REPORT,
        status=DeliverableStatus.PENDING_REVIEW,
        payload={"summary": "valid"},
    )
    assert (
        deliverable.thread_id,
        deliverable.turn_id,
        deliverable.run_id,
        deliverable.skill_run_id,
    ) == (
        scope_a.thread_id,
        scope_a.turn_id,
        scope_a.run_id,
        scope_a.skill_run_id,
    )


@pytest.mark.asyncio
async def test_runtime_deliverable_allocates_next_version_and_replays_same_skill_write(
    session,
    admin,
) -> None:
    graph = await _runtime_graph(session, admin, suffix="deliverable-version")
    source_scope = await _scope(session, admin, graph)
    first = await write_runtime_deliverable(
        session,
        scope=source_scope,
        content=graph["content"],
        agent_code=AgentCode.DECISION.value,
        deliverable_type=DeliverableType.REVIEW_REPORT,
        status=DeliverableStatus.PENDING_REVIEW,
        payload={"summary": "source"},
    )
    replay = await write_runtime_deliverable(
        session,
        scope=source_scope,
        content=graph["content"],
        agent_code=AgentCode.DECISION.value,
        deliverable_type=DeliverableType.REVIEW_REPORT,
        status=DeliverableStatus.PENDING_REVIEW,
        payload={"summary": "source"},
    )
    other_agent = await write_runtime_deliverable(
        session,
        scope=source_scope,
        content=graph["content"],
        agent_code=AgentCode.CONTENT_DIRECTOR.value,
        deliverable_type=DeliverableType.REVIEW_REPORT,
        status=DeliverableStatus.PENDING_REVIEW,
        payload={"summary": "other agent"},
    )

    revision_turn = ConversationTurn(
        thread_id=graph["thread"].id,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id="deliverable-version-revision-turn",
        user_input="supplement",
        target_turn_id=graph["turn"].id,
        steering_mode="supplement",
    )
    session.add(revision_turn)
    await session.flush()
    revision_run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        task_id=graph["task"].id,
        thread_id=graph["thread"].id,
        turn_id=revision_turn.id,
        client_message_id="deliverable-version-revision-run",
        status="running",
        request_payload={},
    )
    session.add(revision_run)
    await session.flush()
    revision_skill = SkillRun(
        org_id=admin.org_id,
        thread_id=graph["thread"].id,
        turn_id=revision_turn.id,
        run_id=revision_run.id,
        task_id=graph["task"].id,
        idempotency_key="deliverable-version-revision-skill",
        skill_code=graph["skill_run"].skill_code,
        skill_version=1,
        status="running",
        input_snapshot={},
        output_snapshot={},
    )
    session.add(revision_skill)
    await session.flush()
    revision_scope = RuntimeScope(
        org_id=admin.org_id,
        user_id=admin.id,
        account_id=graph["account"].id,
        thread_id=graph["thread"].id,
        turn_id=revision_turn.id,
        run_id=revision_run.id,
        task_id=graph["task"].id,
        skill_run_id=revision_skill.id,
    )
    second = await write_runtime_deliverable(
        session,
        scope=revision_scope,
        content=graph["content"],
        agent_code=AgentCode.DECISION.value,
        deliverable_type=DeliverableType.REVIEW_REPORT,
        status=DeliverableStatus.PENDING_REVIEW,
        payload={"summary": "revision"},
    )

    assert replay.id == first.id
    assert first.version == 1
    assert other_agent.version == 1
    assert second.version == 2
    event_count = await session.scalar(
        select(func.count(Event.id)).where(
            Event.turn_id.in_([graph["turn"].id, revision_turn.id]),
            Event.type == "deliverable.updated",
        )
    )
    assert event_count == 3


@pytest.mark.asyncio
async def test_tool_idempotency_reuse_compares_invocation_and_complete_scope(
    session,
    admin,
) -> None:
    calls = 0

    async def handler(params: _EchoParams, _context: ToolExecutionContext) -> dict:
        nonlocal calls
        calls += 1
        return {"echo": params.message}

    graph = await _runtime_graph(session, admin, suffix="tool")
    scope = await _scope(session, admin, graph)
    invocations = []
    for step in ("first", "second"):
        invocation = AgentInvocation(
            task_id=scope.task_id,
            run_id=scope.run_id,
            skill_run_id=scope.skill_run_id,
            thread_id=scope.thread_id,
            turn_id=scope.turn_id,
            step_key=step,
            agent_code=AgentCode.POSITIONING,
            agent_name="positioning",
            status=AgentInvocationStatus.RUNNING,
        )
        session.add(invocation)
        await session.flush()
        invocations.append(invocation)

    adapter = ToolAdapter(
        [
            ToolSpec(
                name="diagnostics.echo",
                handler=handler,
                params_model=_EchoParams,
                side_effect_level="read",
                allowed_roles=frozenset({UserRole.ADMIN, UserRole.USER}),
            )
        ]
    )
    executor = DurableToolExecutor(adapter)
    request = RuntimeToolCall(
        tool_code="diagnostics.echo",
        arguments={"message": "hello"},
        purpose="scope conflict",
        idempotency_key="same-scope-key",
    )

    with pytest.raises(RuntimeScopeConflict):
        await executor.execute(
            task=graph["task"],
            user=admin,
            request=request,
            skill_run_id=scope.skill_run_id,
        )
    first = await executor.execute(
        task=graph["task"],
        user=admin,
        request=request,
        scope=scope,
        invocation_id=invocations[0].id,
    )
    assert first.status == "success"
    with pytest.raises(ToolIdempotencyConflict):
        await executor.execute(
            task=graph["task"],
            user=admin,
            request=request,
            scope=scope,
            invocation_id=invocations[1].id,
        )
    assert calls == 1


@pytest.mark.asyncio
async def test_runtime_scope_rejects_other_organization(session, admin) -> None:
    graph = await _runtime_graph(session, admin, suffix="org")
    other_org = Org(name="other")
    outsider = User(
        org=other_org,
        email="outsider@test.local",
        hashed_password="unused",
        display_name="Outsider",
    )
    session.add(outsider)
    await session.flush()

    with pytest.raises(RuntimeScopeConflict):
        await RuntimeScope.from_conversation(
            session,
            user=outsider,
            thread=graph["thread"],
            turn=graph["turn"],
            run=graph["run"],
        )


@pytest.mark.asyncio
async def test_harness_rejects_cross_task_scope_before_creating_invocation(
    session,
    admin,
) -> None:
    graph_a = await _runtime_graph(session, admin, suffix="harness-a")
    graph_b = await _runtime_graph(session, admin, suffix="harness-b")
    scope_a = await _scope(session, admin, graph_a)

    with pytest.raises(RuntimeScopeConflict):
        await AgentHarness().execute(
            session,
            user=admin,
            task=graph_b["task"],
            code=AgentCode.POSITIONING,
            purpose="must not start",
            evidence_refs=[],
            scope=scope_a,
            trace_only=True,
        )

    assert await session.scalar(select(func.count()).select_from(AgentInvocation)) == 0
