"""PostgreSQL concurrency gate for weekly manual-schedule approval."""

import asyncio
import os
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.security import create_access_token
from app.db import Base, get_session
from app.main import app
from app.models import (
    AgentToolCall,
    ContentScheduleEntry,
    Deliverable,
    Org,
    SkillRun,
    User,
)
from app.models.enums import UserRole
from app.orchestrator.skill_runtime import SkillRuntime
from app.services.composite_skill_runs import lock_composite_finish_approval
from app.services.turn_interrupts import request_interrupt
from tests.test_operating_skills import (
    _AcceptingCritic,
    _capability_request,
    _Harness,
    _scope,
    _Tools,
)


def _postgres_url() -> str:
    return (
        os.environ["TEST_POSTGRES_URL"]
        .replace("postgresql+psycopg://", "postgresql+asyncpg://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is required for the weekly approval concurrency gate",
)
def test_postgres_concurrent_weekly_approval_creates_five_manual_tasks_once() -> None:
    async def exercise() -> None:
        schema = f"weekly_approval_{uuid4().hex}"
        admin_engine = create_async_engine(_postgres_url())
        engine = None
        original_override = app.dependency_overrides.get(get_session)
        try:
            async with admin_engine.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            engine = create_async_engine(
                _postgres_url(),
                connect_args={
                    "server_settings": {
                        "search_path": schema,
                        "lock_timeout": "10000",
                        "statement_timeout": "30000",
                    }
                },
            )
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

            async with sessions() as setup:
                org = Org(name=f"weekly approval {schema}")
                admin = User(
                    org=org,
                    email=f"{schema}@test.invalid",
                    hashed_password="unused",
                    display_name="Weekly approval",
                    role=UserRole.ADMIN,
                )
                setup.add(admin)
                await setup.flush()
                account, thread, turn, run = await _scope(
                    setup,
                    admin,
                    key=schema,
                    message="结合最近数据和对标内容，规划并制作下周抖音内容",
                )
                result = await SkillRuntime(
                    tool_executor=_Tools(),
                    harness=_Harness(),
                    critic=_AcceptingCritic(),
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
                        structured_input={"cycle_days": 7, "topic_count": 5},
                    ),
                )
                assert result.status == "waiting_permission"
                publishing_run = await setup.scalar(
                    select(SkillRun).where(
                        SkillRun.run_id == run.id,
                        SkillRun.skill_code == "publishing_preparation",
                    )
                )
                assert publishing_run is not None
                call = await setup.scalar(
                    select(AgentToolCall).where(
                        AgentToolCall.skill_run_id == publishing_run.id,
                        AgentToolCall.status == "waiting_approval",
                    )
                )
                assert call is not None
                artifact_id = int(call.meta["artifact_id"])
                source_artifact = await setup.get(Deliverable, artifact_id)
                assert source_artifact is not None
                artifact_version = source_artifact.version
                call_id = call.id
                token = create_access_token(str(admin.id), admin.role.value)
                approval_lock = await lock_composite_finish_approval(
                    setup,
                    tool_call=call,
                )
                await request_interrupt(
                    setup,
                    user=admin,
                    run_id=run.id,
                    kind="approval",
                    semantic_key=f"tool-approval:{call.id}",
                    public_message=f"Confirm {call.tool_name}.",
                    action_label="Confirm action",
                    response_schema={
                        "type": "object",
                        "required": ["approved"],
                        "properties": {"approved": {"type": "boolean"}},
                    },
                    skill_run_id=publishing_run.id,
                    source_type="tool_call",
                    source_id=call.id,
                    source_version=1,
                    prelocked=approval_lock.runtime_lock,
                )
                await setup.commit()

            request_session_ids: list[int] = []

            async def independent_session():
                async with sessions() as request_session:
                    request_session_ids.append(id(request_session))
                    yield request_session

            app.dependency_overrides[get_session] = independent_session
            headers = {"Authorization": f"Bearer {token}"}
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                responses = await asyncio.gather(
                    client.post(
                        f"/brain/tool-calls/{call_id}/approve",
                        headers=headers,
                        json={"approved": True, "comment": "确认安排"},
                    ),
                    client.post(
                        f"/brain/tool-calls/{call_id}/approve",
                        headers=headers,
                        json={"approved": True, "comment": "确认安排"},
                    ),
                )

            assert len(set(request_session_ids)) == 2
            assert sorted(response.status_code for response in responses) == [200, 200]
            async with sessions() as verify:
                rows = list(
                    await verify.scalars(
                        select(ContentScheduleEntry).where(
                            ContentScheduleEntry.source_artifact_id == artifact_id,
                            ContentScheduleEntry.source_artifact_version == artifact_version,
                        )
                    )
                )
                artifact = await verify.get(Deliverable, artifact_id)
                assert artifact is not None
                assert artifact.status.value == "approved"
                assert len(rows) == 5
                assert len({row.scheduled_at for row in rows}) == 5
                assert (
                    await verify.scalar(
                        select(func.count(AgentToolCall.id)).where(
                            AgentToolCall.tool_code == "platform.content_publish"
                        )
                    )
                    == 0
                )
        finally:
            if original_override is None:
                app.dependency_overrides.pop(get_session, None)
            else:
                app.dependency_overrides[get_session] = original_override
            if engine is not None:
                await engine.dispose()
            async with admin_engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await admin_engine.dispose()

    asyncio.run(exercise())
