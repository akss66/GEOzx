import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

import app.models as models
from app.models.enums import Platform


def test_conversation_models_are_registered() -> None:
    assert hasattr(models, "ConversationThread")
    assert hasattr(models, "ConversationTurn")


@pytest.mark.asyncio
async def test_thread_keeps_each_turn_input_independent(session, admin) -> None:
    account = models.Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="主账号",
    )
    session.add(account)
    await session.flush()

    thread = models.ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title="账号运营对话",
    )
    first_turn = models.ConversationTurn(
        thread=thread,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id="message-1",
        user_input="查看最近七天数据",
        assistant_response="最近七天数据如下。",
        intent={"intent": "analysis", "confidence": 0.98},
    )
    second_turn = models.ConversationTurn(
        thread=thread,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id="message-2",
        user_input="制定下周内容策略",
    )
    session.add_all([thread, first_turn, second_turn])
    await session.commit()

    turns = list(
        await session.scalars(
            select(models.ConversationTurn)
            .where(models.ConversationTurn.thread_id == thread.id)
            .order_by(models.ConversationTurn.id)
        )
    )

    assert [turn.user_input for turn in turns] == [
        "查看最近七天数据",
        "制定下周内容策略",
    ]
    assert turns[0].client_message_id == "message-1"
    assert turns[1].client_message_id == "message-2"
    assert turns[0].assistant_response == "最近七天数据如下。"
    assert turns[0].intent == {"intent": "analysis", "confidence": 0.98}


@pytest.mark.asyncio
async def test_thread_requires_explicit_account_context(session, admin) -> None:
    session.add(
        models.ConversationThread(
            org_id=admin.org_id,
            created_by_id=admin.id,
            title="缺少账号的对话",
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()

    await session.rollback()


@pytest.mark.asyncio
async def test_turn_client_message_is_unique_inside_thread(session, admin) -> None:
    account = models.Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="运营主账号",
    )
    session.add(account)
    await session.flush()

    thread = models.ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title="Agent 对话",
    )
    session.add_all(
        [
            thread,
            models.ConversationTurn(
                thread=thread,
                org_id=admin.org_id,
                created_by_id=admin.id,
                client_message_id="same-message",
                user_input="第一次提交",
            ),
        ]
    )
    await session.commit()

    session.add(
        models.ConversationTurn(
            thread_id=thread.id,
            org_id=admin.org_id,
            created_by_id=admin.id,
            client_message_id="same-message",
            user_input="重复提交",
        )
    )

    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_turn_rejects_thread_from_another_org(session, admin) -> None:
    await session.execute(text("PRAGMA foreign_keys = ON"))

    account = models.Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="主账号",
    )
    other_org = models.Org(name="另一组织")
    session.add_all([account, other_org])
    await session.flush()

    thread = models.ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title="组织一对话",
    )
    session.add_all(
        [
            thread,
            models.ConversationTurn(
                thread=thread,
                org_id=other_org.id,
                created_by_id=admin.id,
                user_input="错误组织归属",
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_agent_run_rejects_thread_from_another_org(session, admin) -> None:
    await session.execute(text("PRAGMA foreign_keys = ON"))

    account = models.Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="主账号",
    )
    other_org = models.Org(name="另一组织")
    session.add_all([account, other_org])
    await session.flush()

    thread = models.ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title="组织一对话",
    )
    session.add(thread)
    await session.commit()

    session.add(
        models.AgentRun(
            org_id=other_org.id,
            requested_by_id=admin.id,
            thread_id=thread.id,
            client_message_id="cross-org-message",
            request_payload={"message": "错误组织归属"},
        )
    )

    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_agent_run_rejects_turn_from_another_thread(session, admin) -> None:
    await session.execute(text("PRAGMA foreign_keys = ON"))

    account = models.Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="主账号",
    )
    session.add(account)
    await session.flush()

    first_thread = models.ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title="第一条对话",
    )
    second_thread = models.ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title="第二条对话",
    )
    turn = models.ConversationTurn(
        thread=second_thread,
        org_id=admin.org_id,
        created_by_id=admin.id,
        user_input="第二条对话的消息",
    )
    session.add_all([first_thread, second_thread, turn])
    await session.commit()

    session.add(
        models.AgentRun(
            org_id=admin.org_id,
            requested_by_id=admin.id,
            thread_id=first_thread.id,
            turn_id=turn.id,
            client_message_id="cross-thread-message",
            request_payload={"message": "错误线程归属"},
        )
    )

    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_agent_run_rejects_turn_without_thread(session, admin) -> None:
    account = models.Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="主账号",
    )
    session.add(account)
    await session.flush()

    thread = models.ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title="正式对话",
    )
    turn = models.ConversationTurn(
        thread=thread,
        org_id=admin.org_id,
        created_by_id=admin.id,
        user_input="关联消息",
    )
    session.add_all([thread, turn])
    await session.commit()

    session.add(
        models.AgentRun(
            org_id=admin.org_id,
            requested_by_id=admin.id,
            turn_id=turn.id,
            client_message_id="turn-without-thread",
            request_payload={"message": "缺少线程的消息"},
        )
    )

    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_agent_run_persists_matching_thread_and_turn(session, admin) -> None:
    account = models.Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="主账号",
    )
    session.add(account)
    await session.flush()

    thread = models.ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title="正式对话",
    )
    turn = models.ConversationTurn(
        thread=thread,
        org_id=admin.org_id,
        created_by_id=admin.id,
        user_input="正常消息",
    )
    session.add_all([thread, turn])
    await session.commit()

    run = models.AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        thread_id=thread.id,
        turn_id=turn.id,
        client_message_id="matching-message",
        request_payload={"message": "正常消息"},
    )
    session.add(run)
    await session.commit()
    session.expunge_all()

    persisted_run = (
        await session.scalars(
            select(models.AgentRun)
            .options(
                selectinload(models.AgentRun.thread),
                selectinload(models.AgentRun.turn).selectinload(models.ConversationTurn.thread),
            )
            .where(models.AgentRun.id == run.id)
        )
    ).one()

    assert persisted_run.thread is not None
    assert persisted_run.turn is not None
    assert persisted_run.thread.id == thread.id
    assert persisted_run.turn.id == turn.id
    assert persisted_run.turn.thread.id == persisted_run.thread.id


@pytest.mark.asyncio
async def test_agent_run_allows_legacy_task_without_thread_or_turn(session, admin) -> None:
    task = models.BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="旧任务",
    )
    run = models.AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        task_id=None,
        client_message_id="legacy-message",
        request_payload={"message": "继续旧任务"},
    )
    session.add_all([task, run])
    await session.commit()
    await session.refresh(task)
    await session.refresh(run)

    assert task.id is not None
    assert run.thread_id is None
    assert run.turn_id is None
