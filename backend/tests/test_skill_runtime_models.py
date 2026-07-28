from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

import app.models as models
from app.models.enums import AgentCode, AgentInvocationStatus, Platform


async def _create_conversation_scope(
    session,
    admin,
    *,
    suffix: str,
) -> tuple[
    models.Account,
    models.ConversationThread,
    models.ConversationTurn,
    models.AgentRun,
]:
    account = models.Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname=f"Account {suffix}",
    )
    session.add(account)
    await session.flush()

    thread = models.ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title=f"Thread {suffix}",
    )
    turn = models.ConversationTurn(
        thread=thread,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id=f"turn-message-{suffix}",
        user_input=f"Diagnose account {suffix}",
    )
    session.add_all([thread, turn])
    await session.flush()

    run = models.AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        thread_id=thread.id,
        turn_id=turn.id,
        client_message_id=f"run-message-{suffix}",
        request_payload={"message": turn.user_input},
    )
    session.add(run)
    await session.flush()
    return account, thread, turn, run


def test_skill_runtime_model_is_registered() -> None:
    assert hasattr(models, "SkillRun")


@pytest.mark.asyncio
async def test_skill_run_completes_without_legacy_task_and_round_trips_snapshots(
    session,
    admin,
) -> None:
    _account, thread, turn, run = await _create_conversation_scope(
        session,
        admin,
        suffix="standalone",
    )
    skill_run = models.SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        task_id=None,
        idempotency_key="diagnosis:standalone:v2",
        skill_code="account.diagnosis",
        skill_version="2.1.0",
        status="completed",
        input_snapshot={"period": "last_30_days"},
        output_snapshot={"summary": "Positioning is too broad"},
        quality_score=Decimal("0.9250"),
    )
    session.add(skill_run)
    await session.commit()
    skill_run_id = skill_run.id
    session.expunge_all()

    persisted = await session.get(models.SkillRun, skill_run_id)

    assert persisted is not None
    assert persisted.task_id is None
    assert persisted.skill_code == "account.diagnosis"
    assert persisted.skill_version == "2.1.0"
    assert persisted.status == "completed"
    assert persisted.input_snapshot == {"period": "last_30_days"}
    assert persisted.output_snapshot == {"summary": "Positioning is too broad"}
    assert persisted.quality_score == Decimal("0.9250")
    assert persisted.error_code is None


@pytest.mark.asyncio
async def test_expert_and_tool_rows_are_queryable_by_skill_run_and_turn(
    session,
    admin,
) -> None:
    _account, thread, turn, run = await _create_conversation_scope(
        session,
        admin,
        suffix="provenance",
    )
    task = models.BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="Legacy task backing the specialist call",
    )
    session.add(task)
    await session.flush()
    skill_run = models.SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        task_id=task.id,
        idempotency_key="diagnosis:provenance:v1",
        skill_code="account.diagnosis",
        skill_version="1.0.0",
        status="running",
        input_snapshot={},
        output_snapshot={},
    )
    session.add(skill_run)
    await session.flush()

    invocation = models.AgentInvocation(
        task_id=task.id,
        run_id=run.id,
        skill_run_id=skill_run.id,
        thread_id=thread.id,
        turn_id=turn.id,
        agent_code=AgentCode.POSITIONING,
        agent_name="Account positioning specialist",
        status=AgentInvocationStatus.DONE,
    )
    session.add(invocation)
    await session.flush()
    tool_call = models.AgentToolCall(
        org_id=admin.org_id,
        task_id=task.id,
        invocation_id=invocation.id,
        skill_run_id=skill_run.id,
        thread_id=thread.id,
        turn_id=turn.id,
        tool_code="account.profile",
        tool_name="Account profile",
        idempotency_key="tool:provenance:profile",
        status="success",
    )
    session.add(tool_call)
    await session.commit()

    persisted_invocations = list(
        await session.scalars(
            select(models.AgentInvocation).where(
                models.AgentInvocation.skill_run_id == skill_run.id,
                models.AgentInvocation.turn_id == turn.id,
            )
        )
    )
    persisted_tools = list(
        await session.scalars(
            select(models.AgentToolCall).where(
                models.AgentToolCall.skill_run_id == skill_run.id,
                models.AgentToolCall.turn_id == turn.id,
            )
        )
    )

    assert [item.id for item in persisted_invocations] == [invocation.id]
    assert [item.id for item in persisted_tools] == [tool_call.id]
    assert persisted_invocations[0].thread_id == thread.id
    assert persisted_tools[0].thread_id == thread.id


@pytest.mark.asyncio
async def test_skill_run_rejects_duplicate_run_idempotency_key(session, admin) -> None:
    _account, thread, turn, run = await _create_conversation_scope(
        session,
        admin,
        suffix="idempotency",
    )
    common = {
        "org_id": admin.org_id,
        "thread_id": thread.id,
        "turn_id": turn.id,
        "run_id": run.id,
        "idempotency_key": "same-key",
        "skill_code": "account.diagnosis",
        "skill_version": "1.0.0",
        "status": "running",
        "input_snapshot": {},
        "output_snapshot": {},
    }
    session.add(models.SkillRun(**common))
    await session.commit()
    session.add(models.SkillRun(**common))

    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_skill_run_rejects_turn_from_another_account_thread(
    session,
    admin,
) -> None:
    await session.execute(text("PRAGMA foreign_keys = ON"))
    _account_a, thread_a, _turn_a, run_a = await _create_conversation_scope(
        session,
        admin,
        suffix="account-a",
    )
    _account_b, _thread_b, turn_b, _run_b = await _create_conversation_scope(
        session,
        admin,
        suffix="account-b",
    )
    await session.commit()

    session.add(
        models.SkillRun(
            org_id=admin.org_id,
            thread_id=thread_a.id,
            turn_id=turn_b.id,
            run_id=run_a.id,
            idempotency_key="cross-account-turn",
            skill_code="account.diagnosis",
            skill_version="1.0.0",
            status="running",
            input_snapshot={},
            output_snapshot={},
        )
    )

    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_legacy_invocation_and_tool_rows_allow_null_provenance(
    session,
    admin,
) -> None:
    task = models.BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="Legacy task",
    )
    session.add(task)
    await session.flush()
    invocation = models.AgentInvocation(
        task_id=task.id,
        agent_code=AgentCode.POSITIONING,
        agent_name="Legacy specialist",
        status=AgentInvocationStatus.DONE,
    )
    session.add(invocation)
    await session.flush()
    tool_call = models.AgentToolCall(
        org_id=admin.org_id,
        task_id=task.id,
        invocation_id=invocation.id,
        tool_code="legacy.lookup",
        tool_name="Legacy lookup",
        idempotency_key="legacy-tool",
        status="success",
    )
    session.add(tool_call)
    await session.commit()

    assert invocation.skill_run_id is None
    assert invocation.thread_id is None
    assert invocation.turn_id is None
    assert tool_call.skill_run_id is None
    assert tool_call.thread_id is None
    assert tool_call.turn_id is None
