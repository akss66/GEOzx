"""PostgreSQL-only concurrency gates for composite Skill transitions."""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import BrainTask, SkillRun, User
from app.models.enums import BrainTaskStatus, DeliverableStatus, DeliverableType, UserRole
from app.orchestrator.skill_runtime import SkillRuntime
from app.services.artifacts import accept_artifact
from app.services.composite_skill_runs import pause_composite_parent_for_artifacts
from tests.test_operating_skills import _capability_request, _Harness, _scope, _Tools
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
