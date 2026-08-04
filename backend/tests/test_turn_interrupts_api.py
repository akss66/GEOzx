import importlib

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import (
    Account,
    AgentRun,
    AgentToolCall,
    BrainTask,
    ConversationThread,
    ConversationTurn,
    SkillRun,
    TurnInterrupt,
)
from app.models.enums import AccountStatus, BrainTaskStatus, Platform
from app.services.turn_interrupts import request_interrupt


async def _runtime_context(session, admin, *, key: str, with_approval: bool = False):
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname=f"account-{key}",
        status=AccountStatus.ACTIVE,
        auth={"auth_status": "manual"},
    )
    session.add(account)
    await session.flush()
    thread = ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title=key,
    )
    session.add(thread)
    await session.flush()
    turn = ConversationTurn(
        thread_id=thread.id,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id=key,
        user_input=f"message-{key}",
        status="running",
    )
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title=f"task-{key}",
        status=BrainTaskStatus.RUNNING,
    )
    session.add_all([turn, task])
    await session.flush()
    run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        task_id=task.id,
        thread_id=thread.id,
        turn_id=turn.id,
        client_message_id=key,
        status="running",
        phase="running",
        request_payload={"message": turn.user_input},
    )
    session.add(run)
    await session.flush()
    skill = None
    tool = None
    if with_approval:
        skill = SkillRun(
            org_id=admin.org_id,
            thread_id=thread.id,
            turn_id=turn.id,
            run_id=run.id,
            task_id=task.id,
            idempotency_key=f"skill-{key}",
            skill_code="operation_iteration",
            skill_version=1,
            status="running",
            input_snapshot={},
            output_snapshot={},
        )
        session.add(skill)
        await session.flush()
        tool = AgentToolCall(
            org_id=admin.org_id,
            task_id=task.id,
            skill_run_id=skill.id,
            thread_id=thread.id,
            turn_id=turn.id,
            tool_code="publish_package_prepare",
            tool_name="Prepare manual publish package",
            idempotency_key=f"tool-{key}",
            side_effect_level="idempotent_write",
            status="waiting_approval",
            requires_human_confirmation=True,
            input_summary="private tool input",
            output_summary="package ready",
        )
        session.add(tool)
    await session.commit()
    return account, thread, turn, run, task, skill, tool


def test_0500_migration_parent_and_model_contract() -> None:
    migration = importlib.import_module(
        "migrations.versions.20260804_0500_turn_interrupts"
    )
    assert migration.revision == "20260804_0500"
    assert migration.down_revision == "20260804_0450"
    table = TurnInterrupt.__table__
    assert {column.name for column in table.columns} >= {
        "org_id",
        "account_id",
        "thread_id",
        "turn_id",
        "run_id",
        "skill_run_id",
        "kind",
        "status",
        "public_message",
        "response_schema",
        "semantic_key",
        "version",
        "resolution_payload",
        "resolution_hash",
        "resolution_idempotency_key",
        "resolved_by_id",
        "resolved_at",
    }
    assert {constraint.name for constraint in table.constraints} >= {
        "uq_turn_interrupts_run_semantic_key",
        "fk_turn_interrupts_thread_account_org",
        "fk_turn_interrupts_turn_thread_org",
        "fk_turn_interrupts_run_thread_turn_org",
        "fk_turn_interrupts_skill_run_scope",
    }


@pytest.mark.asyncio
async def test_pending_unique_and_lineage(session, admin) -> None:
    account, thread, turn, run, *_ = await _runtime_context(
        session, admin, key="pending-unique"
    )
    first = TurnInterrupt(
        org_id=admin.org_id,
        account_id=account.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        kind="clarification",
        status="pending",
        public_message="Please provide the missing brief.",
        response_schema={"type": "object"},
        semantic_key="missing-brief",
    )
    second = TurnInterrupt(
        org_id=admin.org_id,
        account_id=account.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        kind="manual_pause",
        status="pending",
        public_message="Paused for operator input.",
        response_schema={"type": "object"},
        semantic_key="manual-pause",
    )
    session.add(first)
    await session.commit()
    session.add(second)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_request_clarification_interrupt_pauses_original_run(session, admin) -> None:
    account, thread, turn, run, *_ = await _runtime_context(
        session, admin, key="clarification"
    )
    result = await request_interrupt(
        session,
        user=admin,
        run_id=run.id,
        kind="clarification",
        semantic_key="missing-product-facts",
        public_message="Please provide the product price and core benefit.",
        response_schema={
            "type": "object",
            "required": ["price", "benefit"],
        },
    )
    await session.commit()

    await session.refresh(run)
    await session.refresh(turn)
    assert result.interrupt.account_id == account.id
    assert result.interrupt.thread_id == thread.id
    assert result.interrupt.turn_id == turn.id
    assert result.interrupt.run_id == run.id
    assert result.interrupt.status == "pending"
    assert result.interrupt.version == 1
    assert run.status == "waiting_user"
    assert turn.status == "waiting_user"
    assert await session.scalar(
        select(func.count(AgentRun.id)).where(AgentRun.turn_id == turn.id)
    ) == 1


@pytest.mark.asyncio
async def test_non_approval_interrupt_rejects_source_version_only(session, admin) -> None:
    _account, _thread, _turn, run, *_ = await _runtime_context(
        session, admin, key="clarification-source-version"
    )

    with pytest.raises(
        ValueError,
        match="only approval interrupts may bind a source object",
    ):
        await request_interrupt(
            session,
            user=admin,
            run_id=run.id,
            kind="clarification",
            semantic_key="missing-product-facts",
            public_message="Please provide the missing product facts.",
            response_schema={"type": "object"},
            source_version=1,
        )

    assert await session.scalar(select(func.count(TurnInterrupt.id))) == 0


@pytest.mark.asyncio
async def test_request_approval_interrupt_uses_tool_as_source_only(session, admin) -> None:
    _account, _thread, turn, run, _task, skill, tool = await _runtime_context(
        session, admin, key="approval", with_approval=True
    )
    assert skill is not None and tool is not None
    result = await request_interrupt(
        session,
        user=admin,
        run_id=run.id,
        kind="approval",
        semantic_key=f"tool-approval:{tool.id}",
        public_message="Confirm the manual publishing package.",
        action_label="Confirm publishing package",
        response_schema={"type": "boolean"},
        skill_run_id=skill.id,
        source_type="tool_call",
        source_id=tool.id,
        source_version=1,
    )
    await session.commit()

    await session.refresh(run)
    await session.refresh(turn)
    await session.refresh(tool)
    assert result.interrupt.source_type == "tool_call"
    assert result.interrupt.source_id == tool.id
    assert result.interrupt.skill_run_id == skill.id
    assert run.status == "waiting_permission"
    assert turn.status == "waiting_permission"
    assert tool.status == "waiting_approval"
