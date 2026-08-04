"""PostgreSQL-only concurrency gates for composite Skill transitions."""

import asyncio
import os
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import (
    AgentRun,
    AgentToolCall,
    BrainTask,
    ContentItem,
    ConversationThread,
    Deliverable,
    Event,
    SkillRun,
    User,
)
from app.models.enums import (
    AgentCode,
    BrainTaskStatus,
    DeliverableStatus,
    DeliverableType,
    UserRole,
)
from app.orchestrator.runtime_scope import RuntimeScope
from app.orchestrator.skill_runtime import SkillRuntime
from app.services.agent_runs import cancel_agent_run
from app.services.artifacts import accept_artifact
from app.services.composite_skill_runs import (
    lock_composite_finish_approval,
    pause_composite_parent_for_artifacts,
)
from app.services.runtime_deliverables import write_runtime_deliverable
from app.services.runtime_locking import lock_runtime_root_scope
from app.services.runtime_state import RuntimeStateScope, close_runtime_state
from app.services.skill_approvals import (
    SkillApprovalConflict,
    finalize_skill_finish_approval,
)
from tests.test_operating_skills import (
    _capability_request,
    _Harness,
    _nested_finish_approval_scope,
    _scope,
    _Tools,
)
from tests.test_operation_iteration_skill import _artifact


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is required for the composite locking gate",
)
@pytest.mark.parametrize("ordering", ["pause_first", "accept_first"])
def test_postgres_accept_pause_interleavings_never_lose_wakeup(ordering: str) -> None:
    raw_url = os.environ["TEST_POSTGRES_URL"]
    async_url = raw_url.replace(
        "postgresql+psycopg://", "postgresql+asyncpg://"
    ).replace("postgresql://", "postgresql+asyncpg://")

    async def exercise() -> None:
        engine = create_async_engine(async_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        suffix = uuid4().hex
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with sessions() as setup:
                from app.models import Org

                org = Org(name=f"composite-lock-{suffix}")
                admin = User(
                    org=org,
                    email=f"composite-lock-{suffix}@test.invalid",
                    hashed_password="unused",
                    display_name="Composite lock gate",
                    role=UserRole.ADMIN,
                )
                setup.add(admin)
                await setup.commit()
                account, thread, turn, run = await _scope(
                    setup,
                    admin,
                    key=f"composite-lock-{suffix}",
                    message="根据复盘安排下周运营",
                )
                review = await _artifact(
                    setup,
                    admin,
                    account,
                    kind=DeliverableType.REVIEW_REPORT,
                    status=DeliverableStatus.APPROVED,
                )
                result = await SkillRuntime(
                    tool_executor=_Tools(), harness=_Harness()
                ).execute(
                    setup,
                    user=admin,
                    thread=thread,
                    turn=turn,
                    run=run,
                    skill_code="operation_iteration",
                    capability_request=_capability_request(
                        admin=admin,
                        account=account,
                        thread=thread,
                        turn=turn,
                        run=run,
                        skill_code="operation_iteration",
                        structured_input={"confirmed_review_artifact_id": review.id},
                    ),
                )
                parent_id = result.skill_run_id
                run_id = run.id
                turn_id = turn.id
                task_id = result.task_id
                script_artifact_id = next(
                    node["artifact_id"]
                    for node in result.report["child_skill_graph"]
                    if node["skill_code"] == "script_generation"
                )
                admin_id = admin.id
                parent = await setup.get(SkillRun, parent_id)
                task = await setup.get(BrainTask, task_id)
                assert parent is not None and task is not None
                parent.status = "running"
                run.status = "running"
                turn.status = "running"
                task.status = BrainTaskStatus.RUNNING
                await setup.commit()

            pause_locked = asyncio.Event()
            release_pause = asyncio.Event()

            async def pause_first() -> None:
                async with sessions() as pause_session:
                    parent = await pause_session.get(SkillRun, parent_id)
                    assert parent is not None
                    assert await pause_composite_parent_for_artifacts(
                        pause_session,
                        parent_skill_run=parent,
                        source_artifact_ids=[script_artifact_id],
                    )
                    locked_run = await pause_session.get(type(run), run_id)
                    locked_turn = await pause_session.get(type(turn), turn_id)
                    locked_task = await pause_session.get(BrainTask, task_id)
                    assert locked_run is not None and locked_turn is not None
                    assert locked_task is not None
                    parent.status = "waiting_permission"
                    locked_run.status = "waiting_user"
                    locked_turn.status = "waiting_user"
                    locked_task.status = BrainTaskStatus.RUNNING
                    await pause_session.flush()
                    pause_locked.set()
                    await release_pause.wait()
                    await pause_session.commit()

            async def accept_second() -> None:
                await pause_locked.wait()
                async with sessions() as accept_session:
                    actor = await accept_session.get(User, admin_id)
                    assert actor is not None
                    await accept_artifact(
                        accept_session,
                        actor,
                        artifact_id=script_artifact_id,
                    )

            if ordering == "pause_first":
                pause_task = asyncio.create_task(pause_first())
                accept_task = asyncio.create_task(accept_second())
                await asyncio.wait_for(pause_locked.wait(), timeout=10)
                await asyncio.sleep(0.1)
                assert not accept_task.done()
                release_pause.set()
                await asyncio.wait_for(
                    asyncio.gather(pause_task, accept_task), timeout=10
                )
            else:
                async with sessions() as accept_session:
                    actor = await accept_session.get(User, admin_id)
                    assert actor is not None
                    await accept_artifact(
                        accept_session,
                        actor,
                        artifact_id=script_artifact_id,
                    )
                async with sessions() as pause_session:
                    parent = await pause_session.get(SkillRun, parent_id)
                    assert parent is not None
                    assert not await pause_composite_parent_for_artifacts(
                        pause_session,
                        parent_skill_run=parent,
                        source_artifact_ids=[script_artifact_id],
                    )
                    await pause_session.commit()

            async with sessions() as verify:
                parent = await verify.get(SkillRun, parent_id)
                locked_run = await verify.get(type(run), run_id)
                assert parent is not None and locked_run is not None
                assert parent.status == "running"
                assert locked_run.status == (
                    "queued" if ordering == "pause_first" else "running"
                )
        finally:
            await engine.dispose()

    asyncio.run(exercise())


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is required for the child-finish lock-order gate",
)
@pytest.mark.parametrize("competing_action", ["accept", "pause", "cancel"])
def test_postgres_child_finish_follows_global_lock_order(
    competing_action: str,
    monkeypatch,
) -> None:
    """A real child writer must not deadlock with composite/cancel transitions."""

    raw_url = os.environ["TEST_POSTGRES_URL"]
    async_url = raw_url.replace(
        "postgresql+psycopg://", "postgresql+asyncpg://"
    ).replace("postgresql://", "postgresql+asyncpg://")

    async def exercise() -> None:
        engine = create_async_engine(
            async_url,
            connect_args={
                "server_settings": {
                    "lock_timeout": "5000",
                    "statement_timeout": "15000",
                }
            },
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        suffix = uuid4().hex
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with sessions() as setup:
                from app.models import Org

                org = Org(name=f"child-finish-lock-{suffix}")
                admin = User(
                    org=org,
                    email=f"child-finish-lock-{suffix}@test.invalid",
                    hashed_password="unused",
                    display_name="Child finish lock gate",
                    role=UserRole.ADMIN,
                )
                setup.add(admin)
                await setup.commit()
                turn, run, task, parent, child, artifact, _call = (
                    await _nested_finish_approval_scope(
                        setup,
                        admin,
                        key=f"child-finish-lock-{suffix}",
                    )
                )
                thread = await setup.get(ConversationThread, turn.thread_id)
                assert thread is not None
                replay = Deliverable(
                    content_item_id=task.content_item_id,
                    thread_id=turn.thread_id,
                    turn_id=turn.id,
                    run_id=run.id,
                    skill_run_id=child.id,
                    agent_code=AgentCode.VIDEO_CREATOR.value,
                    type=DeliverableType.VIDEO_ASSET,
                    version=1,
                    status=DeliverableStatus.DRAFT,
                    payload={"source": "child-finish-lock-gate"},
                )
                setup.add(replay)
                await setup.commit()
                ids = {
                    "admin": admin.id,
                    "account": thread.account_id,
                    "thread": turn.thread_id,
                    "turn": turn.id,
                    "run": run.id,
                    "task": task.id,
                    "content": task.content_item_id,
                    "parent": parent.id,
                    "child": child.id,
                    "artifact": artifact.id,
                    "replay": replay.id,
                }

            import app.services.agent_runs as agent_runs_module
            import app.services.composite_skill_runs as composite_module
            import app.services.runtime_deliverables as deliverables_module

            first_lock_barrier = asyncio.Barrier(2)
            action_holds_root = asyncio.Event()
            release_action = asyncio.Event()
            finish_entered_root = asyncio.Event()
            finish_token = None
            finish_pid = None
            action_held_once = False
            if competing_action == "cancel":
                action_module = agent_runs_module
                action_lock_name = "lock_runtime_root_scope"
            elif competing_action == "accept":
                action_module = composite_module
                action_lock_name = "lock_runtime_root_forest"
            else:
                action_module = composite_module
                action_lock_name = "lock_runtime_root_scope"
            original_action_lock = getattr(action_module, action_lock_name)
            original_finish_lock = deliverables_module.lock_runtime_root_scope

            async def hold_action_after_first_root(*args, **kwargs):
                nonlocal action_held_once
                token = await original_action_lock(*args, **kwargs)
                if not action_held_once:
                    action_held_once = True
                    action_holds_root.set()
                    await release_action.wait()
                return token

            async def observe_finish_root(*args, **kwargs):
                nonlocal finish_pid, finish_token
                finish_pid = await args[0].scalar(select(func.pg_backend_pid()))
                finish_entered_root.set()
                finish_token = await original_finish_lock(*args, **kwargs)
                return finish_token

            monkeypatch.setattr(
                action_module,
                action_lock_name,
                hold_action_after_first_root,
            )
            monkeypatch.setattr(
                deliverables_module,
                "lock_runtime_root_scope",
                observe_finish_root,
            )

            async def finish_child() -> None:
                await first_lock_barrier.wait()
                await action_holds_root.wait()
                async with sessions() as child_session:
                    await child_session.execute(text("SET LOCAL lock_timeout = '5s'"))
                    actor = await child_session.get(User, ids["admin"])
                    content = await child_session.get(ContentItem, ids["content"])
                    child_row = await child_session.get(SkillRun, ids["child"])
                    assert actor is not None and content is not None and child_row is not None
                    runtime_scope = RuntimeScope(
                        org_id=actor.org_id,
                        user_id=actor.id,
                        account_id=ids["account"],
                        thread_id=ids["thread"],
                        turn_id=ids["turn"],
                        run_id=ids["run"],
                        task_id=ids["task"],
                        skill_run_id=ids["child"],
                    )
                    replayed = await write_runtime_deliverable(
                        child_session,
                        scope=runtime_scope,
                        content=content,
                        agent_code=AgentCode.VIDEO_CREATOR.value,
                        deliverable_type=DeliverableType.VIDEO_ASSET,
                        status=DeliverableStatus.DRAFT,
                        payload={"source": "child-finish-lock-gate"},
                    )
                    assert replayed.id == ids["replay"]
                    child_output = {
                        **dict(child_row.output_snapshot or {}),
                        "status": "completed",
                    }
                    await close_runtime_state(
                        child_session,
                        scope=RuntimeStateScope(
                            run_id=ids["run"],
                            org_id=actor.org_id,
                            account_id=ids["account"],
                            thread_id=ids["thread"],
                            turn_id=ids["turn"],
                            task_id=ids["task"],
                            skill_run_id=ids["child"],
                            content_item_id=ids["content"],
                            skill_output_snapshot=child_output,
                            nested_skill=True,
                        ),
                        status="completed",
                        message="Nested child completed",
                        commit=False,
                        prelocked=finish_token,
                    )
                    await child_session.commit()

            async def run_competing_action() -> None:
                await first_lock_barrier.wait()
                async with sessions() as competing_session:
                    await competing_session.execute(text("SET LOCAL lock_timeout = '5s'"))
                    if competing_action == "accept":
                        actor = await competing_session.get(User, ids["admin"])
                        assert actor is not None
                        await accept_artifact(
                            competing_session,
                            actor,
                            artifact_id=ids["artifact"],
                        )
                    elif competing_action == "pause":
                        parent_row = await competing_session.get(SkillRun, ids["parent"])
                        assert parent_row is not None
                        should_pause = await pause_composite_parent_for_artifacts(
                            competing_session,
                            parent_skill_run=parent_row,
                            source_artifact_ids=[ids["artifact"]],
                        )
                        if should_pause:
                            parent_row.status = "waiting_permission"
                        await competing_session.commit()
                    else:
                        await cancel_agent_run(competing_session, ids["run"])

            child_task = asyncio.create_task(finish_child(), name="finish-writer")
            competing_task = asyncio.create_task(
                run_competing_action(), name="competing-writer"
            )
            await asyncio.wait_for(action_holds_root.wait(), timeout=10)
            await asyncio.wait_for(finish_entered_root.wait(), timeout=10)
            assert finish_pid is not None
            observed_wait = False
            deadline = monotonic() + 10
            async with sessions() as monitor:
                while monotonic() < deadline:
                    waiting, wait_type = (
                        await monitor.execute(
                            text(
                                "SELECT EXISTS ("
                                "SELECT 1 FROM pg_locks "
                                "WHERE pid = :pid AND locktype = 'advisory' "
                                "AND NOT granted), "
                                "(SELECT wait_event_type FROM pg_stat_activity "
                                "WHERE pid = :pid)"
                            ),
                            {"pid": finish_pid},
                        )
                    ).one()
                    if waiting and wait_type == "Lock":
                        observed_wait = True
                        break
            assert observed_wait, "finish writer never waited on the Run advisory gate"
            assert not child_task.done()
            release_action.set()
            await asyncio.wait_for(
                asyncio.gather(child_task, competing_task),
                timeout=15,
            )

            async with sessions() as verify:
                child_row = await verify.get(SkillRun, ids["child"])
                parent_row = await verify.get(SkillRun, ids["parent"])
                run_row = await verify.get(AgentRun, ids["run"])
                artifact_row = await verify.get(Deliverable, ids["artifact"])
                assert child_row is not None and parent_row is not None
                assert run_row is not None and artifact_row is not None
                assert await verify.scalar(
                    select(func.count(Deliverable.id)).where(
                        Deliverable.skill_run_id == ids["child"],
                        Deliverable.type == DeliverableType.VIDEO_ASSET,
                    )
                ) == 1
                if competing_action == "cancel":
                    assert run_row.status == "cancelled"
                    assert parent_row.status == "cancelled"
                    assert child_row.status == "cancelled"
                    assert artifact_row.status == DeliverableStatus.PENDING_REVIEW
                elif competing_action == "accept":
                    assert run_row.status == "waiting_permission"
                    assert parent_row.status == "waiting_permission"
                    assert child_row.status == "completed"
                    assert artifact_row.status == DeliverableStatus.APPROVED
                else:
                    assert run_row.status == "waiting_permission"
                    assert parent_row.status == "waiting_permission"
                    assert child_row.status == "completed"
                    assert artifact_row.status == DeliverableStatus.PENDING_REVIEW
                public_terminal_count = await verify.scalar(
                    select(func.count(Event.id)).where(
                        Event.run_id == ids["run"],
                        Event.type.in_(
                            {
                                "turn.completed",
                                "turn.cancelled",
                                "turn.failed",
                                "turn.blocked",
                                "turn.stopped",
                            }
                        ),
                    )
                )
                assert public_terminal_count == (
                    1 if competing_action == "cancel" else 0
                )
        finally:
            await engine.dispose()

    asyncio.run(exercise())


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is required for the ordinary finish gate",
)
def test_postgres_ordinary_finish_waits_at_run_root_and_cancel_wins(
    monkeypatch,
) -> None:
    raw_url = os.environ["TEST_POSTGRES_URL"]
    async_url = raw_url.replace(
        "postgresql+psycopg://", "postgresql+asyncpg://"
    ).replace("postgresql://", "postgresql+asyncpg://")

    async def exercise() -> None:
        import app.services.agent_runs as agent_runs_module
        import app.services.composite_skill_runs as composite_module

        engine = create_async_engine(async_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        suffix = uuid4().hex
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with sessions() as setup:
                from app.models import Org

                org = Org(name=f"ordinary-finish-lock-{suffix}")
                admin = User(
                    org=org,
                    email=f"ordinary-finish-lock-{suffix}@test.invalid",
                    hashed_password="unused",
                    display_name="Ordinary finish lock gate",
                    role=UserRole.ADMIN,
                )
                setup.add(admin)
                await setup.commit()
                account, thread, turn, run = await _scope(
                    setup,
                    admin,
                    key=f"ordinary-finish-lock-{suffix}",
                    message="prepare package",
                )
                result = await SkillRuntime(
                    tool_executor=_Tools(), harness=_Harness()
                ).execute(
                    setup,
                    user=admin,
                    thread=thread,
                    turn=turn,
                    run=run,
                    skill_code="publishing_preparation",
                    capability_request=_capability_request(
                        admin=admin,
                        account=account,
                        thread=thread,
                        turn=turn,
                        run=run,
                        skill_code="publishing_preparation",
                        structured_input={},
                    ),
                )
                call = await setup.scalar(
                    select(AgentToolCall).where(
                        AgentToolCall.skill_run_id == result.skill_run_id
                    )
                )
                assert call is not None
                ids = {
                    "run": run.id,
                    "task": result.task_id,
                    "skill": result.skill_run_id,
                    "call": call.id,
                }

            barrier = asyncio.Barrier(2)
            cancel_holds_root = asyncio.Event()
            finish_entered_root = asyncio.Event()
            release_cancel = asyncio.Event()
            finish_pid = None
            original_cancel_lock = agent_runs_module.lock_runtime_root_scope
            original_finish_lock = composite_module.lock_runtime_root_scope

            async def hold_cancel(*args, **kwargs):
                token = await original_cancel_lock(*args, **kwargs)
                cancel_holds_root.set()
                await release_cancel.wait()
                return token

            async def observe_finish(*args, **kwargs):
                nonlocal finish_pid
                finish_pid = await args[0].scalar(select(func.pg_backend_pid()))
                finish_entered_root.set()
                return await original_finish_lock(*args, **kwargs)

            monkeypatch.setattr(
                agent_runs_module, "lock_runtime_root_scope", hold_cancel
            )
            monkeypatch.setattr(
                composite_module, "lock_runtime_root_scope", observe_finish
            )

            async def cancel_first() -> None:
                await barrier.wait()
                async with sessions() as cancel_session:
                    await cancel_agent_run(cancel_session, ids["run"])

            async def finish_second() -> None:
                await barrier.wait()
                await cancel_holds_root.wait()
                async with sessions() as finish_session:
                    call_row = await finish_session.get(AgentToolCall, ids["call"])
                    task_row = await finish_session.get(BrainTask, ids["task"])
                    assert call_row is not None and task_row is not None
                    approval_lock = await lock_composite_finish_approval(
                        finish_session, tool_call=call_row
                    )
                    approval_lock.tool_call.status = "success"
                    with pytest.raises(SkillApprovalConflict):
                        await finalize_skill_finish_approval(
                            finish_session,
                            tool_call=approval_lock.tool_call,
                            task=task_row,
                            approved=True,
                            comment="late approval",
                            prelocked=approval_lock.runtime_lock,
                        )
                    await finish_session.rollback()

            cancel_task = asyncio.create_task(cancel_first())
            finish_task = asyncio.create_task(finish_second())
            await asyncio.wait_for(cancel_holds_root.wait(), timeout=10)
            await asyncio.wait_for(finish_entered_root.wait(), timeout=10)
            assert finish_pid is not None
            observed_wait = False
            deadline = monotonic() + 10
            async with sessions() as monitor:
                while monotonic() < deadline:
                    waiting, wait_type = (
                        await monitor.execute(
                            text(
                                "SELECT EXISTS (SELECT 1 FROM pg_locks "
                                "WHERE pid = :pid AND locktype = 'advisory' "
                                "AND NOT granted), (SELECT wait_event_type "
                                "FROM pg_stat_activity WHERE pid = :pid)"
                            ),
                            {"pid": finish_pid},
                        )
                    ).one()
                    if waiting and wait_type == "Lock":
                        observed_wait = True
                        break
            assert observed_wait
            release_cancel.set()
            await asyncio.wait_for(
                asyncio.gather(cancel_task, finish_task), timeout=15
            )

            async with sessions() as verify:
                run_row = await verify.get(AgentRun, ids["run"])
                skill_row = await verify.get(SkillRun, ids["skill"])
                call_row = await verify.get(AgentToolCall, ids["call"])
                assert run_row is not None and run_row.status == "cancelled"
                assert skill_row is not None and skill_row.status == "cancelled"
                assert call_row is not None and call_row.status == "waiting_approval"
                assert await verify.scalar(
                    select(func.count(Event.id)).where(
                        Event.run_id == ids["run"], Event.type == "turn.cancelled"
                    )
                ) == 1
        finally:
            await engine.dispose()

    asyncio.run(exercise())


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is required for the reverse-order gate",
)
def test_postgres_reverse_deliverable_then_run_lock_order_fails() -> None:
    raw_url = os.environ["TEST_POSTGRES_URL"]
    async_url = raw_url.replace(
        "postgresql+psycopg://", "postgresql+asyncpg://"
    ).replace("postgresql://", "postgresql+asyncpg://")

    async def exercise() -> None:
        engine = create_async_engine(async_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        suffix = uuid4().hex
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with sessions() as setup:
                from app.models import Org

                org = Org(name=f"reverse-lock-{suffix}")
                admin = User(
                    org=org,
                    email=f"reverse-lock-{suffix}@test.invalid",
                    hashed_password="unused",
                    display_name="Reverse lock gate",
                    role=UserRole.ADMIN,
                )
                setup.add(admin)
                await setup.commit()
                turn, run, task, parent, child, artifact, _call = (
                    await _nested_finish_approval_scope(
                        setup, admin, key=f"reverse-lock-{suffix}"
                    )
                )
                ids = {
                    "run": run.id,
                    "turn": turn.id,
                    "task": task.id,
                    "content": task.content_item_id,
                    "parent": parent.id,
                    "child": child.id,
                    "artifact": artifact.id,
                }

            reverse_has_deliverable = asyncio.Event()
            ordered_entered = asyncio.Event()
            ordered_pid = None

            async def ordered_writer() -> None:
                nonlocal ordered_pid
                await reverse_has_deliverable.wait()
                async with sessions() as ordered:
                    ordered_pid = await ordered.scalar(select(func.pg_backend_pid()))
                    ordered_entered.set()
                    await lock_runtime_root_scope(
                        ordered,
                        run_id=ids["run"],
                        expected_turn_id=ids["turn"],
                        expected_task_id=ids["task"],
                        expected_content_item_id=ids["content"],
                        root_skill_run_id=ids["parent"],
                        child_skill_run_ids=(ids["child"],),
                        deliverable_ids=(ids["artifact"],),
                    )
                    await ordered.rollback()

            async with sessions() as reverse:
                await reverse.execute(text("SET LOCAL lock_timeout = '750ms'"))
                await reverse.scalar(
                    select(Deliverable.id)
                    .where(Deliverable.id == ids["artifact"])
                    .with_for_update()
                )
                reverse_has_deliverable.set()
                ordered_task = asyncio.create_task(ordered_writer())
                await asyncio.wait_for(ordered_entered.wait(), timeout=10)
                assert ordered_pid is not None
                observed_wait = False
                deadline = monotonic() + 10
                async with sessions() as monitor:
                    while monotonic() < deadline:
                        wait_type = await monitor.scalar(
                            text(
                                "SELECT wait_event_type FROM pg_stat_activity "
                                "WHERE pid = :pid"
                            ),
                            {"pid": ordered_pid},
                        )
                        if wait_type == "Lock":
                            observed_wait = True
                            break
                assert observed_wait
                with pytest.raises(DBAPIError, match="lock timeout"):
                    await lock_runtime_root_scope(
                        reverse,
                        run_id=ids["run"],
                        expected_turn_id=ids["turn"],
                        expected_task_id=ids["task"],
                        expected_content_item_id=ids["content"],
                        root_skill_run_id=ids["parent"],
                        child_skill_run_ids=(ids["child"],),
                        deliverable_ids=(ids["artifact"],),
                    )
                await reverse.rollback()
                await asyncio.wait_for(ordered_task, timeout=10)
        finally:
            await engine.dispose()

    asyncio.run(exercise())
