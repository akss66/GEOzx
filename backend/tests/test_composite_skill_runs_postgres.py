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
    Account,
    AgentRun,
    AgentToolCall,
    AuditRecord,
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
from app.orchestrator.skill_runtime import SkillRecoveryConflict, SkillRuntime
from app.services.agent_runs import cancel_agent_run, complete_agent_run
from app.services.artifacts import accept_artifact
from app.services.composite_skill_runs import (
    lock_composite_finish_approval,
    pause_composite_parent_for_artifacts,
)
from app.services.conversations import delete_conversation_thread
from app.services.runtime_deliverables import write_runtime_deliverable
from app.services.runtime_locking import RuntimeLockConflict, lock_runtime_root_scope
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


async def _wait_for_advisory_gate(
    sessions, pid: int, *, deadline_seconds: float = 10
) -> None:
    """Prove a backend is waiting on the production Run advisory gate."""

    deadline = monotonic() + deadline_seconds
    async with sessions() as monitor:
        while monotonic() < deadline:
            waiting, wait_type = (
                await monitor.execute(
                    text(
                        "SELECT EXISTS ("
                        "SELECT 1 FROM pg_locks "
                        "WHERE pid = :pid AND locktype = 'advisory' AND NOT granted), "
                        "(SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid)"
                    ),
                    {"pid": pid},
                )
            ).one()
            if waiting and wait_type == "Lock":
                return
            await asyncio.sleep(0.01)
    raise AssertionError("writer never waited on the Run advisory gate")


async def _wait_for_database_lock(
    sessions, pid: int, *, deadline_seconds: float = 10
) -> None:
    """Prove a backend is waiting on a PostgreSQL row/transaction lock."""

    deadline = monotonic() + deadline_seconds
    async with sessions() as monitor:
        while monotonic() < deadline:
            waiting, wait_type = (
                await monitor.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_locks "
                        "WHERE pid = :pid AND NOT granted), "
                        "(SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid)"
                    ),
                    {"pid": pid},
                )
            ).one()
            if waiting and wait_type == "Lock":
                return
            await asyncio.sleep(0.01)
    raise AssertionError("writer never waited on a PostgreSQL lock")


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is required for the first-create gate",
)
def test_postgres_first_skill_create_waits_on_run_gate_against_cancel(
    monkeypatch,
) -> None:
    raw_url = os.environ["TEST_POSTGRES_URL"]
    async_url = raw_url.replace(
        "postgresql+psycopg://", "postgresql+asyncpg://"
    ).replace("postgresql://", "postgresql+asyncpg://")

    async def exercise() -> None:
        import app.orchestrator.skill_runtime as skill_runtime_module
        import app.services.agent_runs as agent_runs_module

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

                org = Org(name=f"first-create-gate-{suffix}")
                admin = User(
                    org=org,
                    email=f"first-create-gate-{suffix}@test.invalid",
                    hashed_password="unused",
                    display_name="First create gate",
                    role=UserRole.ADMIN,
                )
                setup.add(admin)
                await setup.commit()
                account, thread, turn, run = await _scope(
                    setup,
                    admin,
                    key=f"first-create-gate-{suffix}",
                    message="create first skill",
                )
                ids = {
                    "admin": admin.id,
                    "account": account.id,
                    "thread": thread.id,
                    "turn": turn.id,
                    "run": run.id,
                }

            cancel_holds_root = asyncio.Event()
            release_cancel = asyncio.Event()
            create_entered_root = asyncio.Event()
            create_pid = None
            original_cancel_lock = agent_runs_module.lock_runtime_root_scope
            original_create_lock = skill_runtime_module.lock_runtime_root_scope

            async def hold_cancel(*args, **kwargs):
                token = await original_cancel_lock(*args, **kwargs)
                cancel_holds_root.set()
                await release_cancel.wait()
                return token

            async def observe_create(*args, **kwargs):
                nonlocal create_pid
                create_pid = await args[0].scalar(select(func.pg_backend_pid()))
                create_entered_root.set()
                return await original_create_lock(*args, **kwargs)

            monkeypatch.setattr(agent_runs_module, "lock_runtime_root_scope", hold_cancel)
            monkeypatch.setattr(skill_runtime_module, "lock_runtime_root_scope", observe_create)

            async def cancel_writer() -> None:
                async with sessions() as cancel_session:
                    await cancel_agent_run(cancel_session, ids["run"])

            async def create_writer() -> None:
                await cancel_holds_root.wait()
                async with sessions() as create_session:
                    actor = await create_session.get(User, ids["admin"])
                    account_row = await create_session.get(Account, ids["account"])
                    thread_row = await create_session.get(ConversationThread, ids["thread"])
                    turn_row = await create_session.get(type(turn), ids["turn"])
                    run_row = await create_session.get(AgentRun, ids["run"])
                    assert all(
                        row is not None
                        for row in (actor, account_row, thread_row, turn_row, run_row)
                    )
                    await SkillRuntime(tool_executor=_Tools(), harness=_Harness()).execute(
                        create_session,
                        user=actor,
                        thread=thread_row,
                        turn=turn_row,
                        run=run_row,
                        skill_code="script_generation",
                        capability_request=_capability_request(
                            admin=actor,
                            account=account_row,
                            thread=thread_row,
                            turn=turn_row,
                            run=run_row,
                            skill_code="script_generation",
                            structured_input={},
                        ),
                    )

            cancel_task = asyncio.create_task(cancel_writer(), name="cancel-first")
            await asyncio.wait_for(cancel_holds_root.wait(), timeout=10)
            create_task = asyncio.create_task(create_writer(), name="first-create")
            await asyncio.wait_for(create_entered_root.wait(), timeout=10)
            assert create_pid is not None
            await _wait_for_advisory_gate(sessions, create_pid)
            assert not create_task.done()
            release_cancel.set()
            results = await asyncio.wait_for(
                asyncio.gather(cancel_task, create_task, return_exceptions=True),
                timeout=15,
            )
            assert results[0] is None
            assert isinstance(results[1], SkillRecoveryConflict)

            async with sessions() as verify:
                run_row = await verify.get(AgentRun, ids["run"])
                assert run_row is not None and run_row.status == "cancelled"
                assert await verify.scalar(
                    select(func.count(SkillRun.id)).where(SkillRun.run_id == ids["run"])
                ) == 0
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
    reason="TEST_POSTGRES_URL is required for the content/deliverable forest gate",
)
def test_postgres_accept_content_lock_and_deletion_forest_do_not_deadlock(
    monkeypatch,
) -> None:
    raw_url = os.environ["TEST_POSTGRES_URL"]
    async_url = raw_url.replace(
        "postgresql+psycopg://", "postgresql+asyncpg://"
    ).replace("postgresql://", "postgresql+asyncpg://")

    async def exercise() -> None:
        import app.services.artifacts as artifacts_module
        import app.services.conversations as conversations_module

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

                org = Org(name=f"content-deliverable-gate-{suffix}")
                admin = User(
                    org=org,
                    email=f"content-deliverable-gate-{suffix}@test.invalid",
                    hashed_password="unused",
                    display_name="Content deliverable gate",
                    role=UserRole.ADMIN,
                )
                setup.add(admin)
                await setup.commit()
                account, thread, turn, run = await _scope(
                    setup,
                    admin,
                    key=f"content-deliverable-gate-{suffix}",
                    message="accept while deleting",
                )
                run.status = "completed"
                run.phase = "completed"
                turn.status = "completed"
                content = ContentItem(
                    account_id=account.id,
                    created_by_id=admin.id,
                    title="Run-linked extra content",
                )
                setup.add(content)
                await setup.flush()
                artifact = Deliverable(
                    content_item_id=content.id,
                    thread_id=thread.id,
                    turn_id=turn.id,
                    run_id=run.id,
                    agent_code=AgentCode.CONTENT_DIRECTOR.value,
                    type=DeliverableType.VIDEO_SCRIPT,
                    version=1,
                    status=DeliverableStatus.PENDING_REVIEW,
                    payload={
                        "title": "Lock order",
                        "hook": "Start",
                        "scenes": ["one", "two", "three"],
                        "duration_seconds": 30,
                        "presentation_format": "storyboard",
                    },
                )
                runless = Deliverable(
                    content_item_id=content.id,
                    thread_id=thread.id,
                    turn_id=turn.id,
                    agent_code="runless-delete-collision",
                    type=DeliverableType.VIDEO_SCRIPT,
                    version=2,
                    status=DeliverableStatus.DRAFT,
                    payload={"title": "Runless collision"},
                )
                setup.add_all([artifact, runless])
                await setup.commit()
                ids = {
                    "admin": admin.id,
                    "thread": thread.id,
                    "run": run.id,
                    "content": content.id,
                    "artifact": artifact.id,
                    "runless": runless.id,
                }

            accept_holds_content = asyncio.Event()
            release_accept = asyncio.Event()
            delete_entered_forest = asyncio.Event()
            delete_pid = None
            original_latest = artifacts_module._require_latest_artifact_version
            original_forest = conversations_module.lock_runtime_root_forest

            async def hold_after_content_lock(*args, **kwargs):
                version = await original_latest(*args, **kwargs)
                accept_holds_content.set()
                await release_accept.wait()
                return version

            async def observe_delete_forest(*args, **kwargs):
                nonlocal delete_pid
                delete_pid = await args[0].scalar(select(func.pg_backend_pid()))
                delete_entered_forest.set()
                return await original_forest(*args, **kwargs)

            monkeypatch.setattr(
                artifacts_module,
                "_require_latest_artifact_version",
                hold_after_content_lock,
            )
            monkeypatch.setattr(
                conversations_module,
                "lock_runtime_root_forest",
                observe_delete_forest,
            )

            async def accept_writer():
                async with sessions() as accept_session:
                    actor = await accept_session.get(User, ids["admin"])
                    assert actor is not None
                    return await accept_artifact(
                        accept_session,
                        actor,
                        artifact_id=ids["artifact"],
                    )

            async def delete_writer():
                await accept_holds_content.wait()
                async with sessions() as delete_session:
                    actor = await delete_session.get(User, ids["admin"])
                    assert actor is not None
                    return await delete_conversation_thread(
                        delete_session,
                        actor,
                        ids["thread"],
                    )

            accept_task = asyncio.create_task(
                accept_writer(), name="accept-content-first"
            )
            await asyncio.wait_for(accept_holds_content.wait(), timeout=10)
            delete_task = asyncio.create_task(
                delete_writer(), name="delete-forest-second"
            )
            await asyncio.wait_for(delete_entered_forest.wait(), timeout=10)
            assert delete_pid is not None
            await _wait_for_database_lock(sessions, delete_pid)
            assert not delete_task.done()
            release_accept.set()
            accepted, deletion_summary = await asyncio.wait_for(
                asyncio.gather(accept_task, delete_task), timeout=15
            )
            assert accepted.version == 1
            assert deletion_summary.messages_deleted == 1

            async with sessions() as verify:
                assert await verify.get(ConversationThread, ids["thread"]) is None
                assert await verify.get(AgentRun, ids["run"]) is None
                retained = await verify.get(Deliverable, ids["artifact"])
                assert retained is not None
                assert retained.version == 1
                assert retained.status == DeliverableStatus.APPROVED
                assert retained.run_id is None
                assert retained.thread_id is None
                assert await verify.get(Deliverable, ids["runless"]) is None
                assert await verify.get(ContentItem, ids["content"]) is not None
        finally:
            await engine.dispose()

    asyncio.run(exercise())


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is required for the nested-create gate",
)
def test_postgres_nested_child_create_has_no_post_commit_skill_flush_before_pause_or_cancel_gate(
    monkeypatch,
) -> None:
    raw_url = os.environ["TEST_POSTGRES_URL"]
    async_url = raw_url.replace(
        "postgresql+psycopg://", "postgresql+asyncpg://"
    ).replace("postgresql://", "postgresql+asyncpg://")

    async def exercise() -> None:
        import app.orchestrator.skill_runtime as skill_runtime_module
        import app.services.composite_skill_runs as composite_module

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

                org = Org(name=f"nested-create-gate-{suffix}")
                admin = User(
                    org=org,
                    email=f"nested-create-gate-{suffix}@test.invalid",
                    hashed_password="unused",
                    display_name="Nested create gate",
                    role=UserRole.ADMIN,
                )
                setup.add(admin)
                await setup.commit()
                account, thread, turn, run = await _scope(
                    setup,
                    admin,
                    key=f"nested-create-gate-{suffix}",
                    message="create nested child",
                )
                content = ContentItem(
                    account_id=account.id,
                    created_by_id=admin.id,
                    title="Nested child content",
                )
                setup.add(content)
                await setup.flush()
                task = BrainTask(
                    org_id=admin.org_id,
                    created_by_id=admin.id,
                    content_item_id=content.id,
                    title="Nested child task",
                    status=BrainTaskStatus.RUNNING,
                )
                setup.add(task)
                await setup.flush()
                run.task_id = task.id
                run.status = "running"
                turn.status = "running"
                parent = SkillRun(
                    org_id=admin.org_id,
                    thread_id=thread.id,
                    turn_id=turn.id,
                    run_id=run.id,
                    task_id=task.id,
                    idempotency_key=f"parent-{suffix}",
                    skill_code="operation_iteration",
                    skill_version=1,
                    status="running",
                    input_snapshot={},
                    output_snapshot={},
                )
                setup.add(parent)
                await setup.commit()
                ids = {
                    "admin": admin.id,
                    "account": account.id,
                    "thread": thread.id,
                    "turn": turn.id,
                    "run": run.id,
                    "task": task.id,
                    "parent": parent.id,
                }

            pause_holds_root = asyncio.Event()
            release_pause = asyncio.Event()
            child_entered_root = asyncio.Event()
            child_pid = None
            pause_held_once = False
            original_pause_lock = composite_module.lock_runtime_root_scope
            original_child_lock = skill_runtime_module.lock_runtime_root_scope

            async def hold_pause(*args, **kwargs):
                nonlocal pause_held_once
                token = await original_pause_lock(*args, **kwargs)
                if not pause_held_once:
                    pause_held_once = True
                    pause_holds_root.set()
                    await release_pause.wait()
                return token

            async def observe_child(*args, **kwargs):
                nonlocal child_pid
                child_pid = await args[0].scalar(select(func.pg_backend_pid()))
                child_entered_root.set()
                return await original_child_lock(*args, **kwargs)

            monkeypatch.setattr(composite_module, "lock_runtime_root_scope", hold_pause)
            monkeypatch.setattr(skill_runtime_module, "lock_runtime_root_scope", observe_child)

            async def pause_writer() -> None:
                async with sessions() as pause_session:
                    parent_row = await pause_session.get(SkillRun, ids["parent"])
                    assert parent_row is not None
                    assert not await pause_composite_parent_for_artifacts(
                        pause_session,
                        parent_skill_run=parent_row,
                        source_artifact_ids=[],
                    )
                    await pause_session.commit()

            async def child_writer() -> int:
                nonlocal_child = {"execute_returned": False}

                class ObservedRuntime(SkillRuntime):
                    async def execute(self, *args, **kwargs):
                        result = await super().execute(*args, **kwargs)
                        if kwargs.get("parent_skill_run_id") is not None:
                            nonlocal_child["execute_returned"] = True
                        return result

                await pause_holds_root.wait()
                async with sessions() as child_session:
                    original_flush = child_session.flush

                    async def guarded_flush(*args, **kwargs):
                        if nonlocal_child["execute_returned"]:
                            raise AssertionError(
                                "nested wrapper flushed after child execute committed"
                            )
                        return await original_flush(*args, **kwargs)

                    monkeypatch.setattr(child_session, "flush", guarded_flush)
                    actor = await child_session.get(User, ids["admin"])
                    account_row = await child_session.get(Account, ids["account"])
                    thread_row = await child_session.get(ConversationThread, ids["thread"])
                    turn_row = await child_session.get(type(turn), ids["turn"])
                    run_row = await child_session.get(AgentRun, ids["run"])
                    parent_row = await child_session.get(SkillRun, ids["parent"])
                    assert all(
                        row is not None
                        for row in (
                            actor,
                            account_row,
                            thread_row,
                            turn_row,
                            run_row,
                            parent_row,
                        )
                    )
                    runtime = ObservedRuntime(tool_executor=_Tools(), harness=_Harness())
                    result = await runtime._execute_child_skill(
                        child_session,
                        user=actor,
                        thread=thread_row,
                        turn=turn_row,
                        run=run_row,
                        parent_skill_run=parent_row,
                        skill_code="script_generation",
                        capability_request=_capability_request(
                            admin=actor,
                            account=account_row,
                            thread=thread_row,
                            turn=turn_row,
                            run=run_row,
                            skill_code="script_generation",
                            structured_input={},
                        ),
                        lease_owner=f"nested-create-{suffix}",
                    )
                    return result.skill_run_id

            pause_task = asyncio.create_task(pause_writer(), name="pause-root")
            await asyncio.wait_for(pause_holds_root.wait(), timeout=10)
            child_task = asyncio.create_task(child_writer(), name="nested-child-create")
            await asyncio.wait_for(child_entered_root.wait(), timeout=10)
            assert child_pid is not None
            await _wait_for_advisory_gate(sessions, child_pid)
            assert not child_task.done()
            release_pause.set()
            _pause_result, child_id = await asyncio.wait_for(
                asyncio.gather(pause_task, child_task), timeout=15
            )

            async with sessions() as verify:
                child = await verify.get(SkillRun, child_id)
                assert child is not None and child.status == "completed"
                assert child.output_snapshot["composite_parent_skill_run_id"] == ids["parent"]
                artifacts = list(
                    await verify.scalars(
                        select(Deliverable)
                        .where(Deliverable.skill_run_id == child_id)
                        .order_by(Deliverable.id)
                    )
                )
                assert len(artifacts) == 1
                assert artifacts[0].version == 1
        finally:
            await engine.dispose()

    asyncio.run(exercise())


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is required for the complete-rebind gate",
)
def test_postgres_complete_rebind_waits_on_run_gate_against_cancel(
    monkeypatch,
) -> None:
    raw_url = os.environ["TEST_POSTGRES_URL"]
    async_url = raw_url.replace(
        "postgresql+psycopg://", "postgresql+asyncpg://"
    ).replace("postgresql://", "postgresql+asyncpg://")

    async def exercise() -> None:
        import app.services.agent_runs as agent_runs_module

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

                org = Org(name=f"complete-rebind-gate-{suffix}")
                admin = User(
                    org=org,
                    email=f"complete-rebind-gate-{suffix}@test.invalid",
                    hashed_password="unused",
                    display_name="Complete rebind gate",
                    role=UserRole.ADMIN,
                )
                setup.add(admin)
                await setup.commit()
                account, _thread, _turn, run = await _scope(
                    setup,
                    admin,
                    key=f"complete-rebind-gate-{suffix}",
                    message="complete rebind",
                )
                content = ContentItem(
                    account_id=account.id,
                    created_by_id=admin.id,
                    title="Complete rebind content",
                )
                setup.add(content)
                await setup.flush()
                target_task = BrainTask(
                    org_id=admin.org_id,
                    created_by_id=admin.id,
                    content_item_id=content.id,
                    title="Complete rebind target",
                    status=BrainTaskStatus.RUNNING,
                )
                setup.add(target_task)
                await setup.commit()
                ids = {"run": run.id, "task": target_task.id}

            cancel_holds_root = asyncio.Event()
            release_cancel = asyncio.Event()
            complete_entered_root = asyncio.Event()
            complete_pid = None
            original_lock = agent_runs_module.lock_runtime_root_scope

            async def checkpoint_lock(*args, **kwargs):
                nonlocal complete_pid
                if kwargs.get("transition_task_id") is not None:
                    complete_pid = await args[0].scalar(select(func.pg_backend_pid()))
                    complete_entered_root.set()
                    return await original_lock(*args, **kwargs)
                token = await original_lock(*args, **kwargs)
                cancel_holds_root.set()
                await release_cancel.wait()
                return token

            monkeypatch.setattr(agent_runs_module, "lock_runtime_root_scope", checkpoint_lock)

            async def cancel_writer() -> None:
                async with sessions() as cancel_session:
                    await cancel_agent_run(cancel_session, ids["run"])

            async def complete_writer() -> None:
                await cancel_holds_root.wait()
                async with sessions() as complete_session:
                    await complete_agent_run(
                        complete_session,
                        ids["run"],
                        task_id=ids["task"],
                        status="completed",
                    )

            cancel_task = asyncio.create_task(cancel_writer(), name="cancel-rebind")
            await asyncio.wait_for(cancel_holds_root.wait(), timeout=10)
            complete_task = asyncio.create_task(complete_writer(), name="complete-rebind")
            await asyncio.wait_for(complete_entered_root.wait(), timeout=10)
            assert complete_pid is not None
            await _wait_for_advisory_gate(sessions, complete_pid)
            assert not complete_task.done()
            release_cancel.set()
            results = await asyncio.wait_for(
                asyncio.gather(cancel_task, complete_task, return_exceptions=True),
                timeout=15,
            )
            assert results[0] is None
            assert isinstance(results[1], RuntimeLockConflict)

            async with sessions() as verify:
                run_row = await verify.get(AgentRun, ids["run"])
                assert run_row is not None and run_row.status == "cancelled"
                assert run_row.task_id is None
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
    reason="TEST_POSTGRES_URL is required for the terminal/delete forest gate",
)
def test_postgres_terminal_close_and_conversation_delete_serialize_on_run_forest(
    monkeypatch,
) -> None:
    raw_url = os.environ["TEST_POSTGRES_URL"]
    async_url = raw_url.replace(
        "postgresql+psycopg://", "postgresql+asyncpg://"
    ).replace("postgresql://", "postgresql+asyncpg://")

    async def exercise() -> None:
        import app.services.conversations as conversations_module
        import app.services.runtime_state as runtime_state_module

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

                org = Org(name=f"terminal-delete-gate-{suffix}")
                admin = User(
                    org=org,
                    email=f"terminal-delete-gate-{suffix}@test.invalid",
                    hashed_password="unused",
                    display_name="Terminal delete gate",
                    role=UserRole.ADMIN,
                )
                setup.add(admin)
                await setup.commit()
                account, thread, turn, run = await _scope(
                    setup,
                    admin,
                    key=f"terminal-delete-gate-{suffix}",
                    message="terminal then delete",
                )
                content = ContentItem(
                    account_id=account.id,
                    created_by_id=admin.id,
                    title="Retained terminal artifact",
                )
                setup.add(content)
                await setup.flush()
                task = BrainTask(
                    org_id=admin.org_id,
                    created_by_id=admin.id,
                    content_item_id=content.id,
                    title="Terminal delete task",
                    status=BrainTaskStatus.RUNNING,
                )
                setup.add(task)
                await setup.flush()
                run.task_id = task.id
                run.status = "running"
                turn.status = "running"
                skill = SkillRun(
                    org_id=admin.org_id,
                    thread_id=thread.id,
                    turn_id=turn.id,
                    run_id=run.id,
                    task_id=task.id,
                    idempotency_key=f"terminal-delete-{suffix}",
                    skill_code="script_generation",
                    skill_version=1,
                    status="running",
                    input_snapshot={},
                    output_snapshot={},
                )
                setup.add(skill)
                await setup.flush()
                retained = Deliverable(
                    content_item_id=content.id,
                    thread_id=thread.id,
                    turn_id=turn.id,
                    run_id=run.id,
                    skill_run_id=skill.id,
                    agent_code=AgentCode.CONTENT_DIRECTOR.value,
                    type=DeliverableType.VIDEO_SCRIPT,
                    version=1,
                    status=DeliverableStatus.APPROVED,
                    payload={"title": "retained"},
                )
                approval_event = Event(
                    type="approval.decided",
                    org_id=admin.org_id,
                    account_id=account.id,
                    thread_id=thread.id,
                    turn_id=turn.id,
                    run_id=run.id,
                    skill_run_id=skill.id,
                    payload={"approved": True, "approval_kind": "terminal-delete-gate"},
                )
                setup.add_all([retained, approval_event])
                await setup.commit()
                ids = {
                    "admin": admin.id,
                    "org": admin.org_id,
                    "account": account.id,
                    "thread": thread.id,
                    "turn": turn.id,
                    "run": run.id,
                    "task": task.id,
                    "content": content.id,
                    "skill": skill.id,
                    "retained": retained.id,
                }

            terminal_holds_root = asyncio.Event()
            release_terminal = asyncio.Event()
            delete_entered_forest = asyncio.Event()
            delete_pid = None
            original_terminal_lock = runtime_state_module.lock_runtime_root_scope
            original_delete_forest = conversations_module.lock_runtime_root_forest
            original_add_audit = conversations_module._add_minimal_audit_records

            async def hold_terminal(*args, **kwargs):
                token = await original_terminal_lock(*args, **kwargs)
                terminal_holds_root.set()
                await release_terminal.wait()
                return token

            async def observe_delete(*args, **kwargs):
                nonlocal delete_pid
                delete_pid = await args[0].scalar(select(func.pg_backend_pid()))
                delete_entered_forest.set()
                return await original_delete_forest(*args, **kwargs)

            def assert_unique_terminal_event(*args, **kwargs):
                events = kwargs["events"]
                assert sum(event.type == "turn.completed" for event in events) == 1
                return original_add_audit(*args, **kwargs)

            monkeypatch.setattr(runtime_state_module, "lock_runtime_root_scope", hold_terminal)
            monkeypatch.setattr(conversations_module, "lock_runtime_root_forest", observe_delete)
            monkeypatch.setattr(
                conversations_module,
                "_add_minimal_audit_records",
                assert_unique_terminal_event,
            )

            async def terminal_writer() -> None:
                async with sessions() as terminal_session:
                    await close_runtime_state(
                        terminal_session,
                        scope=RuntimeStateScope(
                            run_id=ids["run"],
                            org_id=ids["org"],
                            account_id=ids["account"],
                            thread_id=ids["thread"],
                            turn_id=ids["turn"],
                            task_id=ids["task"],
                            skill_run_id=ids["skill"],
                            content_item_id=ids["content"],
                            skill_output_snapshot={"status": "completed"},
                        ),
                        status="completed",
                        message="Terminal writer completed",
                    )

            async def delete_writer():
                await terminal_holds_root.wait()
                async with sessions() as delete_session:
                    actor = await delete_session.get(User, ids["admin"])
                    assert actor is not None
                    return await delete_conversation_thread(
                        delete_session, actor, ids["thread"]
                    )

            terminal_task = asyncio.create_task(terminal_writer(), name="terminal-close")
            await asyncio.wait_for(terminal_holds_root.wait(), timeout=10)
            delete_task = asyncio.create_task(delete_writer(), name="conversation-delete")
            await asyncio.wait_for(delete_entered_forest.wait(), timeout=10)
            assert delete_pid is not None
            await _wait_for_advisory_gate(sessions, delete_pid)
            assert not delete_task.done()
            release_terminal.set()
            _terminal_result, deletion_summary = await asyncio.wait_for(
                asyncio.gather(terminal_task, delete_task), timeout=15
            )
            assert "approval" in deletion_summary.retained_audit_categories

            async with sessions() as verify:
                assert await verify.get(ConversationThread, ids["thread"]) is None
                assert await verify.get(AgentRun, ids["run"]) is None
                retained_row = await verify.get(Deliverable, ids["retained"])
                assert retained_row is not None
                assert retained_row.version == 1
                assert retained_row.run_id is None
                assert retained_row.skill_run_id is None
                assert await verify.scalar(
                    select(func.count(AuditRecord.id)).where(
                        AuditRecord.org_id == ids["org"],
                        AuditRecord.category == "approval",
                        AuditRecord.action == "terminal-delete-gate",
                    )
                ) == 1
        finally:
            await engine.dispose()

    asyncio.run(exercise())


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
                replay_rows = list(
                    await verify.scalars(
                        select(Deliverable).where(
                            Deliverable.skill_run_id == ids["child"],
                            Deliverable.type == DeliverableType.VIDEO_ASSET,
                        )
                    )
                )
                assert len(replay_rows) == 1
                assert replay_rows[0].version == 1
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
