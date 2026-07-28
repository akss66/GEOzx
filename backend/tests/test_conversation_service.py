import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models import (
    Account,
    AccountMembership,
    Client,
    ClientMembership,
    ConversationThread,
    ConversationTurn,
    Org,
    User,
)
from app.models.enums import Platform, UserRole, WorkspaceRole
from app.schemas.conversation import (
    CreateConversationThreadRequest,
    CreateConversationTurnRequest,
)
from app.services.conversations import (
    append_conversation_turn,
    create_conversation_thread,
    get_conversation_thread,
)


async def _create_thread(session, admin, account: Account) -> ConversationThread:
    return await create_conversation_thread(
        session,
        admin,
        CreateConversationThreadRequest(
            account_id=account.id,
            title="账号运营对话",
        ),
    )


@pytest.mark.asyncio
async def test_create_and_get_thread_require_authorized_account(session, admin) -> None:
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="主账号",
    )
    session.add(account)
    await session.flush()

    thread = await _create_thread(session, admin, account)
    resolved = await get_conversation_thread(session, admin, thread.id)

    assert resolved.id == thread.id
    assert resolved.org_id == admin.org_id
    assert resolved.created_by_id == admin.id
    assert resolved.account_id == account.id


@pytest.mark.asyncio
async def test_create_thread_rejects_account_outside_user_scope(session, member) -> None:
    account = Account(
        org_id=member.org_id,
        platform=Platform.DOUYIN,
        nickname="未授权账号",
    )
    session.add(account)
    await session.flush()

    with pytest.raises(HTTPException) as exc:
        await create_conversation_thread(
            session,
            member,
            CreateConversationThreadRequest(account_id=account.id),
        )

    assert exc.value.status_code == 404
    assert await session.scalar(select(func.count(ConversationThread.id))) == 0


@pytest.mark.asyncio
async def test_append_turn_returns_existing_row_for_same_client_message(
    session, admin
) -> None:
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="幂等账号",
    )
    session.add(account)
    await session.flush()
    thread = await _create_thread(session, admin, account)
    body = CreateConversationTurnRequest(
        client_message_id="message-1",
        message="查看最近七天数据",
        execution_preference="AUTO",
    )

    first, first_created = await append_conversation_turn(
        session, admin, thread.id, body
    )
    second, second_created = await append_conversation_turn(
        session, admin, thread.id, body
    )

    assert first.id == second.id
    assert first_created is True
    assert second_created is False
    assert (
        await session.scalar(
            select(func.count(ConversationTurn.id)).where(
                ConversationTurn.thread_id == thread.id,
                ConversationTurn.client_message_id == "message-1",
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_append_turn_rejects_same_key_with_different_message(session, admin) -> None:
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="冲突账号",
    )
    session.add(account)
    await session.flush()
    thread = await _create_thread(session, admin, account)
    await append_conversation_turn(
        session,
        admin,
        thread.id,
        CreateConversationTurnRequest(
            client_message_id="message-conflict",
            message="第一条消息",
        ),
    )

    with pytest.raises(HTTPException) as exc:
        await append_conversation_turn(
            session,
            admin,
            thread.id,
            CreateConversationTurnRequest(
                client_message_id="message-conflict",
                message="不同的消息",
            ),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "CLIENT_MESSAGE_CONFLICT"


@pytest.mark.asyncio
async def test_integrity_race_returns_existing_turn_and_keeps_session_usable(
    session, admin, monkeypatch
) -> None:
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="竞争写入账号",
    )
    session.add(account)
    await session.flush()
    thread = await _create_thread(session, admin, account)
    competing = ConversationTurn(
        thread_id=thread.id,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id="race-message",
        user_input="竞争请求",
    )
    session.add(competing)
    await session.commit()

    real_scalar = session.scalar
    hid_existing_once = False

    async def stale_scalar(statement, *args, **kwargs):
        nonlocal hid_existing_once
        descriptions = getattr(statement, "column_descriptions", ())
        entity = descriptions[0].get("entity") if descriptions else None
        if entity is ConversationTurn and not hid_existing_once:
            hid_existing_once = True
            return None
        return await real_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(session, "scalar", stale_scalar)

    resolved, created = await append_conversation_turn(
        session,
        admin,
        thread.id,
        CreateConversationTurnRequest(
            client_message_id="race-message",
            message="竞争请求",
        ),
    )
    later, later_created = await append_conversation_turn(
        session,
        admin,
        thread.id,
        CreateConversationTurnRequest(
            client_message_id="after-race",
            message="保存点之后仍可写入",
        ),
    )

    assert resolved.id == competing.id
    assert created is False
    assert later.client_message_id == "after-race"
    assert later_created is True
    assert (
        await session.scalar(
            select(func.count(ConversationTurn.id)).where(
                ConversationTurn.thread_id == thread.id
            )
        )
        == 2
    )


@pytest.mark.asyncio
async def test_member_cannot_read_or_append_another_accounts_thread(
    session, admin, member
) -> None:
    client = Client(org_id=admin.org_id, name="账号隔离客户")
    account_a = Account(
        org_id=admin.org_id,
        client=client,
        platform=Platform.DOUYIN,
        nickname="账号 A",
    )
    account_b = Account(
        org_id=admin.org_id,
        client=client,
        platform=Platform.DOUYIN,
        nickname="账号 B",
    )
    member.account_scope_mode = "selected"
    session.add_all(
        [
            client,
            account_a,
            account_b,
            ClientMembership(
                client=client,
                user=member,
                role=WorkspaceRole.OPERATOR,
            ),
            AccountMembership(user=member, account=account_a),
        ]
    )
    await session.flush()
    thread_b = await _create_thread(session, admin, account_b)

    with pytest.raises(HTTPException) as read_exc:
        await get_conversation_thread(session, member, thread_b.id)
    with pytest.raises(HTTPException) as append_exc:
        await append_conversation_turn(
            session,
            member,
            thread_b.id,
            CreateConversationTurnRequest(
                client_message_id="blocked-account",
                message="不应写入账号 B",
            ),
        )

    assert read_exc.value.status_code == 404
    assert append_exc.value.status_code == 404
    assert (
        await session.scalar(
            select(func.count(ConversationTurn.id)).where(
                ConversationTurn.thread_id == thread_b.id
            )
        )
        == 0
    )


@pytest.mark.asyncio
async def test_cross_org_user_cannot_read_or_append_thread(session, admin) -> None:
    other_org = Org(name="其他组织")
    other_user = User(
        org=other_org,
        email="other-org-admin@test.com",
        hashed_password="not-used-in-service-test",
        display_name="其他组织管理员",
        role=UserRole.ADMIN,
    )
    other_account = Account(
        org=other_org,
        platform=Platform.DOUYIN,
        nickname="其他组织账号",
    )
    session.add_all([other_user, other_account])
    await session.flush()
    other_thread = await _create_thread(session, other_user, other_account)

    with pytest.raises(HTTPException) as read_exc:
        await get_conversation_thread(session, admin, other_thread.id)
    with pytest.raises(HTTPException) as append_exc:
        await append_conversation_turn(
            session,
            admin,
            other_thread.id,
            CreateConversationTurnRequest(
                client_message_id="blocked-org",
                message="不应跨组织写入",
            ),
        )

    assert read_exc.value.status_code == 404
    assert append_exc.value.status_code == 404
    assert (
        await session.scalar(
            select(func.count(ConversationTurn.id)).where(
                ConversationTurn.thread_id == other_thread.id
            )
        )
        == 0
    )
