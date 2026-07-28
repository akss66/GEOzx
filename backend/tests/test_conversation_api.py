"""Additive, account-scoped main-Agent conversation API contracts."""

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.core.security import create_access_token
from app.models import (
    Account,
    AccountMembership,
    AgentRun,
    Client,
    ClientMembership,
    ConversationTurn,
    Org,
    User,
)
from app.models.enums import Platform, UserRole, WorkspaceRole


def _auth(user: User) -> dict[str, str]:
    token = create_access_token(str(user.id), user.role.value)
    return {"Authorization": f"Bearer {token}"}


async def _account(session, user: User, nickname: str) -> Account:
    account = Account(
        org_id=user.org_id,
        platform=Platform.DOUYIN,
        nickname=nickname,
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def _create_thread(client, user: User, account: Account, *, title: str = ""):
    response = await client.post(
        "/brain/conversations",
        headers=_auth(user),
        json={"account_id": account.id, "title": title},
    )
    assert response.status_code == 201
    return response.json()


async def _submit_turn(
    client,
    user: User,
    thread_id: int,
    *,
    client_message_id: str,
    message: str,
    execution_preference: str = "AUTO",
    requested_skill_code: str | None = None,
    attachment_ids: list[int] | None = None,
):
    return await client.post(
        f"/brain/conversations/{thread_id}/turns",
        headers=_auth(user),
        json={
            "client_message_id": client_message_id,
            "message": message,
            "execution_preference": execution_preference,
            "requested_skill_code": requested_skill_code,
            "attachment_ids": attachment_ids or [],
        },
    )


@pytest.mark.asyncio
async def test_every_conversation_route_is_disabled_by_default(
    client, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", False)
    headers = _auth(admin)
    requests = [
        await client.post(
            "/brain/conversations",
            headers=headers,
            json={"account_id": 1},
        ),
        await client.get("/brain/conversations/1", headers=headers),
        await client.post(
            "/brain/conversations/1/turns",
            headers=headers,
            json={"client_message_id": "disabled-1", "message": "你好"},
        ),
        await client.get("/brain/turns/1", headers=headers),
    ]

    assert [response.status_code for response in requests] == [503, 503, 503, 503]
    assert {
        response.json()["detail"]["code"] for response in requests
    } == {"MAIN_AGENT_V2_DISABLED"}


@pytest.mark.asyncio
async def test_create_and_get_thread_with_ordered_turn_history(
    client, session, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "有序历史账号")
    created = await _create_thread(
        client,
        admin,
        account,
        title="账号运营对话",
    )

    assert created["account_id"] == account.id
    assert created["org_id"] == admin.org_id
    assert created["created_by_id"] == admin.id
    assert created["title"] == "账号运营对话"
    assert created["turns"] == []

    first = await _submit_turn(
        client,
        admin,
        created["id"],
        client_message_id="history-1",
        message="第一条",
    )
    second = await _submit_turn(
        client,
        admin,
        created["id"],
        client_message_id="history-2",
        message="第二条",
    )
    assert first.status_code == 202
    assert second.status_code == 202

    response = await client.get(
        f"/brain/conversations/{created['id']}",
        headers=_auth(admin),
    )

    assert response.status_code == 200
    history = response.json()
    assert [turn["id"] for turn in history["turns"]] == [
        first.json()["turn"]["id"],
        second.json()["turn"]["id"],
    ]
    assert [turn["user_input"] for turn in history["turns"]] == [
        "第一条",
        "第二条",
    ]
    assert all(turn["projections"] == [] for turn in history["turns"])


@pytest.mark.asyncio
async def test_submit_turn_claims_one_owned_run_without_task(
    client, session, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "提交账号")
    thread = await _create_thread(client, admin, account)

    response = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="turn-api-1",
        message="查看最近七天数据",
        requested_skill_code="account_data_query",
        attachment_ids=[7, 8],
    )

    assert response.status_code == 202
    body = response.json()
    assert body["turn"]["thread_id"] == thread["id"]
    assert body["turn"]["client_message_id"] == "turn-api-1"
    assert body["run"]["thread_id"] == thread["id"]
    assert body["run"]["turn_id"] == body["turn"]["id"]
    assert body["run"]["task_id"] is None
    assert body["task_id"] is None
    assert body["projections"] == []

    run = await session.get(AgentRun, body["run"]["id"])
    assert run is not None
    assert run.task_id is None
    assert run.thread_id == thread["id"]
    assert run.turn_id == body["turn"]["id"]
    assert run.request_payload == {
        "account_id": account.id,
        "attachment_ids": [7, 8],
        "client_message_id": "turn-api-1",
        "execution_preference": "AUTO",
        "message": "查看最近七天数据",
        "requested_skill_code": "account_data_query",
        "thread_id": thread["id"],
        "turn_id": body["turn"]["id"],
    }

    turn_response = await client.get(
        f"/brain/turns/{body['turn']['id']}",
        headers=_auth(admin),
    )
    assert turn_response.status_code == 200
    assert turn_response.json() == body["turn"]


@pytest.mark.asyncio
async def test_true_duplicate_returns_the_same_turn_and_run(
    client, session, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "幂等账号")
    thread = await _create_thread(client, admin, account)
    request = {
        "client_message_id": "duplicate-1",
        "message": "体检这个账号",
        "requested_skill_code": "account_inspection",
    }

    first = await _submit_turn(client, admin, thread["id"], **request)
    repeated = await _submit_turn(client, admin, thread["id"], **request)

    assert first.status_code == 202
    assert repeated.status_code == 202
    assert repeated.json()["turn"]["id"] == first.json()["turn"]["id"]
    assert repeated.json()["run"]["id"] == first.json()["run"]["id"]
    assert (
        await session.scalar(
            select(func.count(ConversationTurn.id)).where(
                ConversationTurn.thread_id == thread["id"]
            )
        )
        == 1
    )
    assert (
        await session.scalar(
            select(func.count(AgentRun.id)).where(
                AgentRun.thread_id == thread["id"]
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_duplicate_rejects_changed_immutable_request_payload(
    client, session, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "冲突账号")
    thread = await _create_thread(client, admin, account)
    first = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="payload-conflict-1",
        message="只讨论方案",
        execution_preference="AUTO",
    )

    changed_preference = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="payload-conflict-1",
        message="只讨论方案",
        execution_preference="DISCUSS_ONLY",
    )
    changed_message = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="payload-conflict-1",
        message="直接执行",
        execution_preference="AUTO",
    )

    assert first.status_code == 202
    assert changed_preference.status_code == 409
    assert changed_message.status_code == 409
    assert changed_preference.json()["detail"]["code"] == "CLIENT_MESSAGE_CONFLICT"
    assert changed_message.json()["detail"]["code"] == "CLIENT_MESSAGE_CONFLICT"
    assert await session.scalar(select(func.count(AgentRun.id))) == 1


@pytest.mark.asyncio
async def test_same_client_message_id_across_account_threads_returns_conflict(
    client, session, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account_a = await _account(session, admin, "账号 A")
    account_b = await _account(session, admin, "账号 B")
    thread_a = await _create_thread(client, admin, account_a)
    thread_b = await _create_thread(client, admin, account_b)

    first = await _submit_turn(
        client,
        admin,
        thread_a["id"],
        client_message_id="cross-account-message",
        message="查看数据",
    )
    conflict = await _submit_turn(
        client,
        admin,
        thread_b["id"],
        client_message_id="cross-account-message",
        message="查看数据",
    )

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "CLIENT_MESSAGE_CONFLICT"
    assert (
        await session.scalar(
            select(func.count(ConversationTurn.id)).where(
                ConversationTurn.thread_id == thread_b["id"]
            )
        )
        == 0
    )
    runs = list(await session.scalars(select(AgentRun)))
    assert len(runs) == 1
    assert runs[0].thread_id == thread_a["id"]
    assert runs[0].turn_id == first.json()["turn"]["id"]


@pytest.mark.asyncio
async def test_account_scoped_member_cannot_enumerate_or_append_thread_or_turn(
    client, session, admin, member, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    workspace = Client(org_id=admin.org_id, name="隔离客户")
    account_a = Account(
        org_id=admin.org_id,
        client=workspace,
        platform=Platform.DOUYIN,
        nickname="可见账号",
    )
    account_b = Account(
        org_id=admin.org_id,
        client=workspace,
        platform=Platform.DOUYIN,
        nickname="不可见账号",
    )
    member.account_scope_mode = "selected"
    session.add_all(
        [
            workspace,
            account_a,
            account_b,
            ClientMembership(
                client=workspace,
                user=member,
                role=WorkspaceRole.OPERATOR,
            ),
            AccountMembership(user=member, account=account_a),
        ]
    )
    await session.commit()
    thread_b = await _create_thread(client, admin, account_b)
    created_turn = await _submit_turn(
        client,
        admin,
        thread_b["id"],
        client_message_id="private-turn",
        message="账号 B 私有消息",
    )
    assert created_turn.status_code == 202
    turn_id = created_turn.json()["turn"]["id"]

    responses = [
        await client.get(
            f"/brain/conversations/{thread_b['id']}",
            headers=_auth(member),
        ),
        await client.get(f"/brain/turns/{turn_id}", headers=_auth(member)),
        await _submit_turn(
            client,
            member,
            thread_b["id"],
            client_message_id="blocked-member",
            message="不应写入",
        ),
    ]

    assert [response.status_code for response in responses] == [404, 404, 404]
    assert (
        await session.scalar(
            select(func.count(ConversationTurn.id)).where(
                ConversationTurn.thread_id == thread_b["id"]
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_cross_org_user_cannot_enumerate_or_append_thread_or_turn(
    client, session, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "本组织账号")
    thread = await _create_thread(client, admin, account)
    submission = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="org-private-turn",
        message="本组织消息",
    )
    other_org = Org(name="其他组织")
    other_user = User(
        org=other_org,
        email="other-conversation-api@test.com",
        hashed_password="unused",
        display_name="其他组织管理员",
        role=UserRole.ADMIN,
    )
    session.add(other_user)
    await session.commit()
    await session.refresh(other_user)

    responses = [
        await client.get(
            f"/brain/conversations/{thread['id']}",
            headers=_auth(other_user),
        ),
        await client.get(
            f"/brain/turns/{submission.json()['turn']['id']}",
            headers=_auth(other_user),
        ),
        await _submit_turn(
            client,
            other_user,
            thread["id"],
            client_message_id="blocked-org",
            message="不应写入",
        ),
    ]

    assert [response.status_code for response in responses] == [404, 404, 404]


@pytest.mark.asyncio
async def test_legacy_brain_messages_remains_reachable_when_v2_is_disabled(
    client, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", False)
    response = await client.post(
        "/brain/messages",
        headers=_auth(admin),
        json={},
    )

    assert response.status_code == 422
    assert response.json()["detail"] != {
        "code": "MAIN_AGENT_V2_DISABLED",
        "message": "Main Agent V2 is disabled",
    }
