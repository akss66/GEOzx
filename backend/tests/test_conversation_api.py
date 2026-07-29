"""Additive, account-scoped main-Agent conversation API contracts."""

import json
import logging

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.core.security import create_access_token
from app.models import (
    Account,
    AccountMembership,
    AgentInvocation,
    AgentRun,
    BrainTask,
    Client,
    ClientMembership,
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
    Platform,
    UserRole,
    WorkspaceRole,
)


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
async def test_history_lists_only_the_current_users_selected_account_threads(
    client,
    session,
    admin,
    member,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    workspace = Client(org_id=admin.org_id, name="会话隔离客户")
    account = Account(
        org_id=admin.org_id,
        client=workspace,
        platform=Platform.DOUYIN,
        nickname="会话隔离账号",
    )
    member.account_scope_mode = "selected"
    session.add_all(
        [
            workspace,
            account,
            ClientMembership(
                client=workspace,
                user=member,
                role=WorkspaceRole.OPERATOR,
            ),
            AccountMembership(user=member, account=account),
        ]
    )
    await session.commit()
    await session.refresh(account)

    admin_thread = await _create_thread(client, admin, account)
    member_thread = await _create_thread(client, member, account)
    await _submit_turn(
        client,
        admin,
        admin_thread["id"],
        client_message_id="admin-history-1",
        message="管理员自己的会话",
        requested_skill_code="account_data_query",
    )
    await _submit_turn(
        client,
        member,
        member_thread["id"],
        client_message_id="member-history-1",
        message="运营成员自己的会话",
        requested_skill_code="account_data_query",
    )

    admin_history = await client.get(
        f"/brain/conversations?account_id={account.id}",
        headers=_auth(admin),
    )
    member_history = await client.get(
        f"/brain/conversations?account_id={account.id}",
        headers=_auth(member),
    )

    assert admin_history.status_code == 200
    assert member_history.status_code == 200
    assert [item["id"] for item in admin_history.json()["data"]] == [
        admin_thread["id"]
    ]
    assert [item["id"] for item in member_history.json()["data"]] == [
        member_thread["id"]
    ]
    assert member_history.json()["data"][0]["title"] == "运营成员自己的会话"
    assert member_history.json()["data"][0]["turn_count"] == 1


@pytest.mark.asyncio
async def test_owner_can_permanently_delete_conversation_and_execution_logs(
    client,
    session,
    admin,
    member,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "永久删除账号")
    thread = await _create_thread(client, admin, account)
    submitted = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="delete-history-1",
        message="读取账号数据",
        requested_skill_code="account_data_query",
    )
    assert submitted.status_code == 202

    denied = await client.delete(
        f"/brain/conversations/{thread['id']}",
        headers=_auth(member),
    )
    deleted = await client.delete(
        f"/brain/conversations/{thread['id']}",
        headers=_auth(admin),
    )

    assert denied.status_code == 404
    assert deleted.status_code == 204
    assert await session.get(ConversationThread, thread["id"]) is None
    assert (
        await session.scalar(
            select(func.count(ConversationTurn.id)).where(
                ConversationTurn.thread_id == thread["id"]
            )
        )
        == 0
    )
    assert (
        await session.scalar(
            select(func.count(AgentRun.id)).where(
                AgentRun.thread_id == thread["id"]
            )
        )
        == 0
    )
    assert (
        await session.scalar(
            select(func.count(SkillRun.id)).where(
                SkillRun.thread_id == thread["id"]
            )
        )
        == 0
    )
    assert (
        await session.scalar(
            select(func.count(Event.id)).where(Event.thread_id == thread["id"])
        )
        == 0
    )


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
    assert body["run"]["status"] == "completed"
    assert len(body["projections"]) == 1
    assert body["projections"][0]["type"] == "account_data"
    assert body["projections"][0]["account_id"] == account.id
    assert body["projections"][0]["turn_id"] == body["turn"]["id"]
    assert body["turn"]["projections"] == body["projections"]

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
async def test_task_free_turn_broadcasts_incremental_response_events(
    client, session, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "流式普通问答账号")
    thread = await _create_thread(client, admin, account)
    realtime_events: list[tuple[str, dict]] = []

    async def capture_realtime_event(event_type: str, payload: dict, **_kwargs) -> None:
        realtime_events.append((event_type, payload))

    monkeypatch.setattr(
        "app.orchestrator.brain_runtime.publish_realtime_event",
        capture_realtime_event,
    )

    response = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="task-free-stream-1",
        message="你好",
    )

    assert response.status_code == 202
    body = response.json()
    response_text = body["turn"]["assistant_response"]
    response_events = [
        (event_type, payload)
        for event_type, payload in realtime_events
        if event_type in {
            "brain.runtime.message_start",
            "brain.runtime.message_delta",
            "brain.runtime.message_done",
        }
    ]
    assert response_events[0][0] == "brain.runtime.message_start"
    assert response_events[-1][0] == "brain.runtime.message_done"
    response_deltas = [
        payload["delta"]
        for event_type, payload in response_events
        if event_type == "brain.runtime.message_delta"
    ]
    assert response_deltas
    assert all(len(delta) <= 2 for delta in response_deltas)
    assert "".join(
        response_deltas
    ) == response_text
    assert all(
        payload["client_message_id"] == "task-free-stream-1"
        and payload["thread_id"] == thread["id"]
        and payload["message_id"] == "task-free-stream-1:00-decision:1"
        for _, payload in response_events
    )


@pytest.mark.asyncio
async def test_unknown_explicit_skill_returns_blocked_turn_without_formal_records(
    client,
    session,
    admin,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "unsupported-explicit-skill")
    thread = await _create_thread(client, admin, account)

    response = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="unsupported-explicit-skill",
        message="Run a capability that is not in the public catalog",
        requested_skill_code="not_registered",
    )

    assert response.status_code == 202
    body = response.json()
    assert body["task_id"] is None
    assert body["run"]["status"] == "blocked"
    assert body["projections"] == [
        {
            "type": "execution_blocked",
            "skill_code": "not_registered",
            "code": "UNKNOWN_SKILL",
            "recovery_action": "请从当前公开能力目录重新选择。",
            "turn_id": body["turn"]["id"],
        }
    ]
    for model in (SkillRun, Deliverable, ContentItem, BrainTask):
        assert await session.scalar(select(func.count(model.id))) == 0


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
    skill_run = await session.scalar(
        select(SkillRun).where(SkillRun.thread_id == thread["id"])
    )
    assert skill_run is not None
    run = await session.get(AgentRun, skill_run.run_id)
    assert run is not None
    run.result_payload = {
        "projections": [
            {
                "type": "artifact",
                "artifact_id": 9001,
                "artifact_type": "account_inspection_report",
                "skill_run_id": skill_run.id,
                "account_id": account.id,
                "report": {},
            }
        ]
    }
    session.add(
        AgentInvocation(
            task_id=skill_run.task_id,
            run_id=skill_run.run_id,
            skill_run_id=skill_run.id,
            thread_id=skill_run.thread_id,
            turn_id=skill_run.turn_id,
            step_key="test-positioning",
            agent_code=AgentCode.POSITIONING,
            agent_name="账号定位专家",
            status=AgentInvocationStatus.DONE,
        )
    )
    await session.commit()
    history_response = await client.get(
        f"/brain/conversations/{thread['id']}",
        headers=_auth(admin),
    )
    assert history_response.status_code == 200
    execution = next(
        projection
        for projection in history_response.json()["turns"][0]["projections"]
        if projection["type"] == "execution_summary"
    )
    assert execution["skill_code"] == "account_inspection"
    assert execution["experts"]
    assert all(expert["agent_name"] for expert in execution["experts"])
    assert all("input_summary" not in expert for expert in execution["experts"])


@pytest.mark.asyncio
async def test_blocked_turn_still_projects_called_experts(
    client, session, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "失败溯源账号")
    thread = await _create_thread(client, admin, account)
    submitted = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="blocked-experts-1",
        message="体检这个账号",
        requested_skill_code="account_inspection",
    )
    assert submitted.status_code == 202
    skill_run = await session.scalar(
        select(SkillRun).where(SkillRun.thread_id == thread["id"])
    )
    assert skill_run is not None
    run = await session.get(AgentRun, skill_run.run_id)
    assert run is not None
    run.result_payload = {
        "projections": [
            {
                "type": "execution_blocked",
                "artifact_type": "account_inspection_report",
                "skill_run_id": skill_run.id,
                "code": "CRITIC_RETRY_EXHAUSTED",
            }
        ]
    }
    session.add(
        AgentInvocation(
            task_id=skill_run.task_id,
            run_id=skill_run.run_id,
            skill_run_id=skill_run.id,
            thread_id=skill_run.thread_id,
            turn_id=skill_run.turn_id,
            step_key="blocked-positioning",
            agent_code=AgentCode.POSITIONING,
            agent_name="账号定位专家",
            status=AgentInvocationStatus.DONE,
        )
    )
    await session.commit()

    history_response = await client.get(
        f"/brain/conversations/{thread['id']}",
        headers=_auth(admin),
    )

    assert history_response.status_code == 200
    projections = history_response.json()["turns"][0]["projections"]
    assert any(item["type"] == "execution_blocked" for item in projections)
    execution = next(
        item for item in projections if item["type"] == "execution_summary"
    )
    assert execution["experts"][0]["agent_name"] == "账号定位专家"


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
    assert all(
        "MAIN_AGENT_V2_ROLLOUT_RESTRICTED" not in str(response.json())
        for response in responses
    )
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


@pytest.mark.asyncio
async def test_feature_flag_disabled_returns_typed_turn_error_and_keeps_legacy_messages(
    client, session, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "feature-flag-disabled")
    thread = await _create_thread(client, admin, account)
    monkeypatch.setattr(settings, "main_agent_v2_enabled", False)
    turn_response = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="feature-flag-disabled-turn",
        message="禁用后新 turn 入口应阻断",
    )
    legacy_response = await client.post(
        "/brain/messages",
        headers=_auth(admin),
        json={},
    )

    assert turn_response.status_code == 503
    assert turn_response.json()["detail"] == {
        "code": "MAIN_AGENT_V2_DISABLED",
        "message": "Main Agent V2 is disabled",
    }
    assert legacy_response.status_code == 422
    assert legacy_response.json()["detail"] != turn_response.json()["detail"]


@pytest.mark.asyncio
async def test_feature_flag_turn_submission_emits_safe_route_diagnostics(
    client, session, admin, monkeypatch, caplog
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "feature-flag-diagnostics")
    thread = await _create_thread(client, admin, account)

    caplog.clear()
    caplog.set_level(logging.INFO, logger="dyflow.main_agent_v2")
    response = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="feature-flag-query-log",
        message="查看近七天数据",
        requested_skill_code="account_data_query",
    )

    assert response.status_code == 202
    body = response.json()
    skill_run_id = body["projections"][0]["skill_run_id"]
    run_event = next(
        event
        for event in (await session.scalars(select(Event).order_by(Event.id))).all()
        if event.turn_id == body["turn"]["id"]
    )
    diagnostics = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "main_agent_turn_completed"
    ]

    assert diagnostics, "expected rollout diagnostics log"
    record = diagnostics[-1]
    assert record.thread_id == thread["id"]
    assert record.turn_id == body["turn"]["id"]
    assert record.run_id == body["run"]["id"]
    assert record.mode == "query"
    assert record.skill_run_id == skill_run_id
    assert record.task_id is None
    assert record.artifact_ids == []
    assert record.status == "completed"
    custom_keys = (
        set(record.__dict__) - set(logging.makeLogRecord({}).__dict__) - {"message"}
    )
    assert custom_keys == {
        "artifact_ids",
        "event",
        "mode",
        "run_id",
        "skill_run_id",
        "status",
        "task_id",
        "thread_id",
        "turn_id",
    }
    serialized = caplog.text + str(record.__dict__) + str(run_event.payload)
    assert "查看近七天数据" not in serialized
    assert "request_payload" not in record.__dict__
    assert "result_payload" not in record.__dict__


@pytest.mark.asyncio
async def test_feature_flag_enabled_keeps_workspace_and_conversation_ownership_boundaries(
    client, session, admin, member, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "feature-flag-admin-rollout")
    thread = await _create_thread(client, admin, account)

    admin_turn = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="feature-flag-admin-turn",
        message="admin access only",
    )
    member_create = await client.post(
        "/brain/conversations",
        headers=_auth(member),
        json={"account_id": account.id, "title": "blocked"},
    )
    member_get_thread = await client.get(
        f"/brain/conversations/{thread['id']}",
        headers=_auth(member),
    )
    member_submit_turn = await _submit_turn(
        client,
        member,
        thread["id"],
        client_message_id="feature-flag-member-turn",
        message="user must stay on legacy route",
    )
    member_get_turn = await client.get(
        f"/brain/turns/{admin_turn.json()['turn']['id']}",
        headers=_auth(member),
    )
    legacy_response = await client.post(
        "/brain/messages",
        headers=_auth(member),
        json={},
    )

    assert admin_turn.status_code == 202
    assert [
        member_create.status_code,
        member_get_thread.status_code,
        member_submit_turn.status_code,
        member_get_turn.status_code,
    ] == [404, 404, 404, 404]
    assert all(
        "MAIN_AGENT_V2_ROLLOUT_RESTRICTED" not in str(response.json())
        for response in (
            member_create,
            member_get_thread,
            member_submit_turn,
            member_get_turn,
        )
    )
    assert legacy_response.status_code == 422
    assert legacy_response.json()["detail"] != member_create.json()["detail"]


@pytest.mark.asyncio
async def test_feature_flag_turn_submission_emits_json_route_diagnostics(
    client, session, admin, monkeypatch, caplog
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "feature-flag-json-diagnostics")
    thread = await _create_thread(client, admin, account)

    caplog.clear()
    caplog.set_level(logging.INFO, logger="dyflow.main_agent_v2")
    response = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="feature-flag-json-log",
        message="sensitive rollout prompt that must not be logged",
        requested_skill_code="account_data_query",
    )

    assert response.status_code == 202
    body = response.json()
    skill_run_id = body["projections"][0]["skill_run_id"]
    record = next(
        entry
        for entry in caplog.records
        if "main_agent_turn_completed" in entry.getMessage()
    )
    payload = json.loads(record.getMessage())

    assert set(payload) == {
        "artifact_ids",
        "event",
        "mode",
        "run_id",
        "skill_run_id",
        "status",
        "task_id",
        "thread_id",
        "turn_id",
    }
    assert payload == {
        "artifact_ids": [],
        "event": "main_agent_turn_completed",
        "mode": "query",
        "run_id": body["run"]["id"],
        "skill_run_id": skill_run_id,
        "status": "completed",
        "task_id": None,
        "thread_id": thread["id"],
        "turn_id": body["turn"]["id"],
    }
    assert "sensitive rollout prompt that must not be logged" not in record.getMessage()
    assert "request_payload" not in record.getMessage()
    assert "result_payload" not in record.getMessage()
