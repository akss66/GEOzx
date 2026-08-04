"""Additive, account-scoped main-Agent conversation API contracts."""

import json
import logging
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.core.security import create_access_token
from app.models import (
    Account,
    AccountMembership,
    AgentInvocation,
    AgentRun,
    AgentToolCall,
    AuditRecord,
    BrainTask,
    Client,
    ClientMembership,
    ContentItem,
    ConversationAttachment,
    ConversationThread,
    ConversationTurn,
    Deliverable,
    Event,
    LLMCall,
    Org,
    SkillRun,
    ToolExecutionAttempt,
    User,
)
from app.models.enums import (
    AgentCode,
    AgentInvocationStatus,
    DeliverableStatus,
    DeliverableType,
    Platform,
    UserRole,
    WorkspaceRole,
)
from app.schemas.conversation import CreateConversationTurnRequest
from app.services.agent_runs import enqueue_agent_runtime
from app.services.conversations import delete_conversation_thread
from app.services.turn_execution import execute_conversation_turn
from app.worker import recover_agent_runs


@pytest.fixture(autouse=True)
def _stub_agent_runtime_queue(monkeypatch):
    async def enqueue_agent_runtime(*, run_id: int) -> None:
        del run_id

    monkeypatch.setattr(
        "app.api.conversations.enqueue_agent_runtime",
        enqueue_agent_runtime,
        raising=False,
    )
    monkeypatch.setattr(settings, "main_agent_typed_runtime_enabled", True)


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
    target_turn_id: int | None = None,
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
            "target_turn_id": target_turn_id,
        },
    )


@pytest.mark.asyncio
async def test_supplement_creates_immutable_steering_turn_without_cancelling_target(
    client, session, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "supplement-steering")
    thread = await _create_thread(client, admin, account)
    target_response = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="supplement-target",
        message="规划五条拍摄稿",
    )

    response = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="supplement-message",
        message="第一条不要讲价格",
    )

    assert response.status_code == 202
    body = response.json()
    assert body["turn"]["user_input"] == "第一条不要讲价格"
    assert body["turn"]["target_turn_id"] == target_response.json()["turn"]["id"]
    assert body["turn"]["steering_mode"] == "supplement"
    assert body["steering_explanation"] == "已补充到当前任务的要求中。"
    target_turn = await session.get(ConversationTurn, target_response.json()["turn"]["id"])
    target_run = await session.get(AgentRun, target_response.json()["run"]["id"])
    steering_run = await session.get(AgentRun, body["run"]["id"])
    assert target_turn is not None
    assert target_turn.user_input == "规划五条拍摄稿"
    assert target_turn.status == "queued"
    assert target_run is not None
    assert target_run.cancel_requested_at is None
    assert steering_run is not None
    assert steering_run.status == "completed"
    assert steering_run.phase == "completed"
    assert steering_run.started_at is not None
    assert steering_run.finished_at is not None
    control_events = list(
        await session.scalars(
            select(Event)
            .where(Event.turn_id == body["turn"]["id"])
            .order_by(Event.sequence)
        )
    )
    assert [event.type for event in control_events] == [
        "turn.received",
        "turn.completed",
    ]

    event = await session.scalar(
        select(Event).where(
            Event.turn_id == target_turn.id,
            Event.type == "turn.steered",
        )
    )
    assert event is not None
    assert event.payload == {
        "message": "已收到补充要求。",
        "metadata": {
            "category": "steering",
            "label": "supplement",
            "source_id": body["turn"]["id"],
        },
    }


@pytest.mark.asyncio
async def test_stop_marks_target_cancel_once_and_does_not_enqueue_replacement(
    client, session, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    queued_run_ids: list[int] = []

    async def capture_enqueue(*, run_id: int) -> None:
        queued_run_ids.append(run_id)

    monkeypatch.setattr("app.api.conversations.enqueue_agent_runtime", capture_enqueue)
    account = await _account(session, admin, "stop-steering")
    thread = await _create_thread(client, admin, account)
    target_response = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="stop-target",
        message="执行账号检查",
    )
    response = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="stop-message",
        message="先停一下",
    )
    first_cancelled_at = (
        await session.get(AgentRun, target_response.json()["run"]["id"])
    ).cancel_requested_at
    replay = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="stop-message",
        message="先停一下",
    )

    assert response.status_code == 202
    assert replay.status_code == 202
    body = response.json()
    assert replay.json()["turn"]["id"] == body["turn"]["id"]
    assert body["turn"]["steering_mode"] == "stop"
    assert body["turn"]["target_turn_id"] == target_response.json()["turn"]["id"]
    assert body["steering_explanation"] == "已请求停止当前任务。"
    target_run = await session.get(AgentRun, target_response.json()["run"]["id"])
    target_turn = await session.get(ConversationTurn, target_response.json()["turn"]["id"])
    steering_run = await session.get(AgentRun, body["run"]["id"])
    assert target_run is not None
    assert target_run.cancel_requested_at == first_cancelled_at
    assert first_cancelled_at is not None
    assert target_turn is not None
    assert target_turn.status == "queued"
    assert steering_run is not None
    assert steering_run.status == "completed"
    assert queued_run_ids == [target_response.json()["run"]["id"]]
    events = list(
        await session.scalars(
            select(Event).where(
                Event.turn_id == target_response.json()["turn"]["id"],
                Event.type == "turn.steered",
            )
        )
    )
    assert len(events) == 1
    assert events[0].payload["metadata"] == {
        "category": "steering",
        "label": "stop",
        "source_id": body["turn"]["id"],
    }

    public_events = await client.get(
        f"/conversation-threads/{thread['id']}/events",
        headers=_auth(admin),
    )
    assert public_events.status_code == 200
    paused = next(
        item for item in public_events.json()["data"] if item["type"] == "turn.steered"
    )
    assert paused["payload"]["metadata"] == events[0].payload["metadata"]
    assert "confidence" not in json.dumps(paused, ensure_ascii=False)
    assert "classifier" not in json.dumps(paused, ensure_ascii=False)


@pytest.mark.asyncio
async def test_replace_goal_cancels_target_and_enqueues_new_execution(
    client, session, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    queued_run_ids: list[int] = []

    async def capture_enqueue(*, run_id: int) -> None:
        queued_run_ids.append(run_id)

    monkeypatch.setattr("app.api.conversations.enqueue_agent_runtime", capture_enqueue)
    account = await _account(session, admin, "replace-steering")
    thread = await _create_thread(client, admin, account)
    target_response = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="replace-target",
        message="按品牌曝光目标规划",
    )
    response = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="replace-message",
        message="重新按获客目标规划",
    )

    assert response.status_code == 202
    body = response.json()
    assert body["turn"]["steering_mode"] == "replace_goal"
    assert body["turn"]["target_turn_id"] == target_response.json()["turn"]["id"]
    assert body["steering_explanation"] == "已按新目标创建替代任务。"
    target_turn = await session.get(ConversationTurn, target_response.json()["turn"]["id"])
    target_run = await session.get(AgentRun, target_response.json()["run"]["id"])
    replacement_run = await session.get(AgentRun, body["run"]["id"])
    assert target_turn is not None
    assert target_turn.user_input == "按品牌曝光目标规划"
    assert target_run is not None
    assert target_run.cancel_requested_at is not None
    assert replacement_run is not None
    assert replacement_run.status == "queued"
    assert queued_run_ids == [target_run.id, replacement_run.id]


@pytest.mark.asyncio
async def test_replace_enqueue_failure_returns_deferred_and_replay_retries_dispatch(
    client, session, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "replace-dispatch-deferred")
    thread = await _create_thread(client, admin, account)
    target = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="replace-deferred-target",
        message="执行旧方案",
    )
    dispatch_attempts: list[int] = []

    async def fail_enqueue(*, run_id: int) -> None:
        dispatch_attempts.append(run_id)
        raise ConnectionError("queue temporarily unavailable")

    monkeypatch.setattr("app.api.conversations.enqueue_agent_runtime", fail_enqueue)
    first = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="replace-deferred-message",
        message="重新按获客目标规划",
    )
    target_run = await session.get(AgentRun, target.json()["run"]["id"])
    assert target_run is not None
    first_cancelled_at = target_run.cancel_requested_at
    replay = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="replace-deferred-message",
        message="重新按获客目标规划",
    )

    assert first.status_code == 202
    assert replay.status_code == 202
    assert first.json()["dispatch_deferred"] is True
    assert replay.json()["dispatch_deferred"] is True
    assert first.json()["dispatch_message"] == "任务已保存，调度暂时延迟，系统将自动恢复。"
    assert replay.json()["run"]["id"] == first.json()["run"]["id"]
    replacement_run = await session.get(AgentRun, first.json()["run"]["id"])
    await session.refresh(target_run)
    assert replacement_run is not None
    assert replacement_run.status == "queued"
    assert replacement_run.phase == "queued"
    assert first_cancelled_at is not None
    assert target_run.cancel_requested_at is not None
    assert target_run.cancel_requested_at.replace(tzinfo=first_cancelled_at.tzinfo) == (
        first_cancelled_at
    )
    assert dispatch_attempts == [replacement_run.id, replacement_run.id]
    assert (
        await session.scalar(
            select(func.count(Event.id)).where(
                Event.turn_id == target.json()["turn"]["id"],
                Event.type == "turn.steered",
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_independent_query_leaves_active_target_unchanged(
    client, session, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "independent-steering")
    thread = await _create_thread(client, admin, account)
    target_response = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="independent-target",
        message="规划下周内容",
    )
    response = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="independent-message",
        message="顺便看看昨天的数据",
    )

    assert response.status_code == 202
    body = response.json()
    assert body["turn"]["steering_mode"] == "independent_query"
    assert body["turn"]["target_turn_id"] is None
    assert body["steering_explanation"] == "已作为新的独立问题处理。"
    target_run = await session.get(AgentRun, target_response.json()["run"]["id"])
    independent_run = await session.get(AgentRun, body["run"]["id"])
    assert target_run is not None
    assert target_run.cancel_requested_at is None
    assert independent_run is not None
    assert independent_run.status == "queued"


@pytest.mark.asyncio
async def test_cross_thread_target_returns_404_without_persisting_submission(
    client, session, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "cross-thread-steering")
    first_thread = await _create_thread(client, admin, account)
    second_thread = await _create_thread(client, admin, account)
    target_response = await _submit_turn(
        client,
        admin,
        first_thread["id"],
        client_message_id="cross-thread-target",
        message="执行原任务",
    )

    response = await _submit_turn(
        client,
        admin,
        second_thread["id"],
        client_message_id="cross-thread-stop",
        message="先停一下",
        target_turn_id=target_response.json()["turn"]["id"],
    )

    assert response.status_code == 404
    assert (
        await session.scalar(
            select(func.count(ConversationTurn.id)).where(
                ConversationTurn.thread_id == second_thread["id"]
            )
        )
        == 0
    )


@pytest.mark.asyncio
async def test_terminal_target_degrades_stop_to_independent_query(
    client, session, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "terminal-steering")
    thread = await _create_thread(client, admin, account)
    target_response = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="terminal-target",
        message="已完成的任务",
    )
    target_turn = await session.get(ConversationTurn, target_response.json()["turn"]["id"])
    target_run = await session.get(AgentRun, target_response.json()["run"]["id"])
    assert target_turn is not None
    assert target_run is not None
    target_turn.status = "completed"
    target_run.status = "completed"
    await session.commit()

    response = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="terminal-stop",
        message="先停一下",
        target_turn_id=target_turn.id,
    )

    assert response.status_code == 202
    body = response.json()
    assert body["turn"]["steering_mode"] == "independent_query"
    assert body["turn"]["target_turn_id"] is None
    await session.refresh(target_run)
    assert target_run.cancel_requested_at is None
    degraded_run = await session.get(AgentRun, body["run"]["id"])
    assert degraded_run is not None
    assert degraded_run.status == "queued"


@pytest.mark.asyncio
async def test_control_message_rejects_rebinding_same_client_id_to_another_target(
    client, session, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "control-target-conflict")
    thread = await _create_thread(client, admin, account)
    first_target = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="control-first-target",
        message="第一个任务",
    )
    second_target = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="control-second-target",
        message="第二个任务",
    )
    first = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="control-target-conflict-message",
        message="先停一下",
        target_turn_id=first_target.json()["turn"]["id"],
    )
    conflict = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="control-target-conflict-message",
        message="先停一下",
        target_turn_id=second_target.json()["turn"]["id"],
    )

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "CLIENT_MESSAGE_CONFLICT"
    assert (
        await session.scalar(
            select(func.count(ConversationTurn.id)).where(
                ConversationTurn.client_message_id == "control-target-conflict-message"
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_legacy_matching_turn_replays_without_applying_steering_side_effects(
    client, session, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    enqueued: list[int] = []

    async def capture_enqueue(*, run_id: int) -> None:
        enqueued.append(run_id)

    monkeypatch.setattr("app.api.conversations.enqueue_agent_runtime", capture_enqueue)
    account = await _account(session, admin, "legacy-steering-replay")
    thread = await _create_thread(client, admin, account)
    legacy_turn = ConversationTurn(
        thread_id=thread["id"],
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id="legacy-steering-message",
        user_input="先停一下",
        status="queued",
    )
    session.add(legacy_turn)
    await session.flush()
    legacy_run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        thread_id=thread["id"],
        turn_id=legacy_turn.id,
        client_message_id="legacy-steering-message",
        status="queued",
        phase="queued",
        request_payload={
            "account_id": account.id,
            "attachment_ids": [],
            "attachment_contexts": [],
            "client_message_id": "legacy-steering-message",
            "execution_preference": "AUTO",
            "message": "先停一下",
            "requested_skill_code": None,
            "thread_id": thread["id"],
            "turn_id": legacy_turn.id,
        },
    )
    session.add(legacy_run)
    await session.commit()

    response = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="legacy-steering-message",
        message="先停一下",
    )

    assert response.status_code == 202
    assert response.json()["turn"]["id"] == legacy_turn.id
    assert response.json()["run"]["id"] == legacy_run.id
    await session.refresh(legacy_turn)
    await session.refresh(legacy_run)
    assert legacy_turn.steering_mode is None
    assert legacy_turn.target_turn_id is None
    assert legacy_turn.status == "queued"
    assert legacy_run.status == "queued"
    assert legacy_run.phase == "queued"
    assert legacy_run.cancel_requested_at is None
    assert enqueued == []
    assert await session.scalar(select(func.count(Event.id))) == 0


async def _execute_queued_turn(
    session,
    user: User,
    response,
    *,
    message: str,
    requested_skill_code: str | None = None,
    execution_preference: str = "AUTO",
    attachment_ids: list[int] | None = None,
):
    payload = response.json()
    turn = await session.get(ConversationTurn, payload["turn"]["id"])
    run = await session.get(AgentRun, payload["run"]["id"])
    assert turn is not None
    assert run is not None
    result = await execute_conversation_turn(
        session,
        user,
        turn,
        run,
        CreateConversationTurnRequest(
            client_message_id=run.client_message_id,
            message=message,
            requested_skill_code=requested_skill_code,
            execution_preference=execution_preference,
            attachment_ids=attachment_ids or [],
        ),
    )
    return result, turn, run


@pytest.mark.asyncio
async def test_every_conversation_route_is_disabled_by_default(client, admin, monkeypatch) -> None:
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
    assert {response.json()["detail"]["code"] for response in requests} == {
        "MAIN_AGENT_V2_DISABLED"
    }


@pytest.mark.asyncio
async def test_typed_runtime_flag_blocks_new_conversation_routes(
    client, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    monkeypatch.setattr(settings, "main_agent_typed_runtime_enabled", False)

    response = await client.post(
        "/brain/conversations",
        headers=_auth(admin),
        json={"account_id": 1},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "MAIN_AGENT_TYPED_RUNTIME_DISABLED",
        "message": "Typed main agent runtime is disabled",
    }


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
    assert [turn["status"] for turn in history["turns"]] == ["queued", "queued"]
    assert all(turn["projections"] == [] for turn in history["turns"])
    assert all(turn["model_call_count"] == 0 for turn in history["turns"])
    assert all(turn["tool_call_count"] == 0 for turn in history["turns"])
    assert all(turn["route_ms"] is None for turn in history["turns"])
    assert all(turn["first_token_ms"] is None for turn in history["turns"])
    assert all(turn["completion_ms"] is None for turn in history["turns"])
    assert all(turn["total_ms"] is None for turn in history["turns"])


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
    assert [item["id"] for item in admin_history.json()["data"]] == [admin_thread["id"]]
    assert [item["id"] for item in member_history.json()["data"]] == [member_thread["id"]]
    assert member_history.json()["data"][0]["title"] == "运营成员自己的会话"
    assert member_history.json()["data"][0]["turn_count"] == 1


@pytest.mark.asyncio
async def test_owner_cannot_delete_conversation_with_active_runtime(
    client,
    session,
    admin,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "Active AgentRun deletion account")
    thread = await _create_thread(client, admin, account)
    submitted = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="delete-active-1",
        message="Do not delete this active run",
        requested_skill_code="account_inspection",
    )

    assert submitted.status_code == 202

    blocked = await client.delete(
        f"/brain/conversations/{thread['id']}",
        headers=_auth(admin),
    )

    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "CONVERSATION_DELETE_BLOCKED"
    assert await session.get(ConversationThread, thread["id"]) is not None


@pytest.mark.asyncio
async def test_owner_cannot_delete_conversation_with_active_skill_run(
    client,
    session,
    admin,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "Active SkillRun deletion account")
    thread = await _create_thread(client, admin, account)
    submitted = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="delete-active-skill-1",
        message="Run the account inspection",
        requested_skill_code="account_inspection",
    )
    assert submitted.status_code == 202
    await _execute_queued_turn(
        session,
        admin,
        submitted,
        message="Run the account inspection",
        requested_skill_code="account_inspection",
    )
    run = await session.get(AgentRun, submitted.json()["run"]["id"])
    skill_run = await session.scalar(select(SkillRun).where(SkillRun.thread_id == thread["id"]))
    assert run is not None
    assert skill_run is not None
    run.status = "completed"
    skill_run.status = "running"
    await session.commit()

    blocked = await client.delete(
        f"/brain/conversations/{thread['id']}",
        headers=_auth(admin),
    )

    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "CONVERSATION_DELETE_BLOCKED"
    assert await session.get(ConversationThread, thread["id"]) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("turn_status", ["running", "waiting_user"])
async def test_owner_cannot_delete_conversation_with_non_terminal_turn(
    client,
    session,
    admin,
    monkeypatch,
    turn_status,
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, f"Delete {turn_status} turn")
    thread = await _create_thread(client, admin, account)
    turn = ConversationTurn(
        thread_id=thread["id"],
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id=f"delete-{turn_status}",
        user_input="Keep this turn",
        status=turn_status,
    )
    session.add(turn)
    await session.commit()

    blocked = await client.delete(
        f"/brain/conversations/{thread['id']}",
        headers=_auth(admin),
    )

    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "CONVERSATION_DELETE_BLOCKED"
    assert await session.get(ConversationThread, thread["id"]) is not None


@pytest.mark.asyncio
async def test_owner_cannot_delete_conversation_with_unknown_tool_state(
    client,
    session,
    admin,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "Delete unknown tool state")
    thread = await _create_thread(client, admin, account)
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="Unknown tool state",
    )
    session.add(task)
    await session.flush()
    tool_call = AgentToolCall(
        org_id=admin.org_id,
        task_id=task.id,
        thread_id=thread["id"],
        tool_code="unknown.state",
        tool_name="Unknown state",
        status="provider_mystery",
        side_effect_level="read",
    )
    session.add(tool_call)
    await session.flush()
    tool_call_id = tool_call.id
    await session.commit()

    blocked = await client.delete(
        f"/brain/conversations/{thread['id']}",
        headers=_auth(admin),
    )

    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "CONVERSATION_DELETE_BLOCKED"
    assert await session.get(AgentToolCall, tool_call_id) is not None


@pytest.mark.asyncio
async def test_owner_cannot_delete_conversation_with_active_invocation(
    client,
    session,
    admin,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "Delete active invocation")
    thread = await _create_thread(client, admin, account)
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="Active invocation",
    )
    session.add(task)
    await session.flush()
    invocation = AgentInvocation(
        task_id=task.id,
        thread_id=thread["id"],
        step_key="active-invocation",
        agent_code=AgentCode.OPERATOR,
        agent_name="Operator",
        status=AgentInvocationStatus.RUNNING,
    )
    session.add(invocation)
    await session.commit()

    blocked = await client.delete(
        f"/brain/conversations/{thread['id']}",
        headers=_auth(admin),
    )

    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "CONVERSATION_DELETE_BLOCKED"


@pytest.mark.asyncio
async def test_owner_cannot_delete_conversation_with_dispatched_tool_attempt(
    client,
    session,
    admin,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "Delete dispatched attempt")
    thread = await _create_thread(client, admin, account)
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="Dispatched attempt",
    )
    session.add(task)
    await session.flush()
    tool_call = AgentToolCall(
        org_id=admin.org_id,
        task_id=task.id,
        thread_id=thread["id"],
        tool_code="provider.read",
        tool_name="Provider read",
        status="success",
        side_effect_level="read",
    )
    session.add(tool_call)
    await session.flush()
    session.add(
        ToolExecutionAttempt(
            tool_call_id=tool_call.id,
            attempt_no=1,
            status="dispatched",
        )
    )
    await session.commit()

    blocked = await client.delete(
        f"/brain/conversations/{thread['id']}",
        headers=_auth(admin),
    )

    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "CONVERSATION_DELETE_BLOCKED"


@pytest.mark.asyncio
async def test_owner_can_delete_empty_and_terminal_conversations(
    client,
    session,
    admin,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "Delete terminal conversations")
    empty_thread = await _create_thread(client, admin, account)
    terminal_thread = await _create_thread(client, admin, account)
    session.add(
        ConversationTurn(
            thread_id=terminal_thread["id"],
            org_id=admin.org_id,
            created_by_id=admin.id,
            client_message_id="delete-terminal",
            user_input="Completed",
            assistant_response="Done",
            status="completed",
        )
    )
    await session.commit()

    empty_deleted = await client.delete(
        f"/brain/conversations/{empty_thread['id']}",
        headers=_auth(admin),
    )
    terminal_deleted = await client.delete(
        f"/brain/conversations/{terminal_thread['id']}",
        headers=_auth(admin),
    )

    assert empty_deleted.status_code == terminal_deleted.status_code == 200
    assert empty_deleted.json()["messages_deleted"] == 0
    assert terminal_deleted.json()["messages_deleted"] == 1
    assert await session.get(ConversationThread, empty_thread["id"]) is None
    assert await session.get(ConversationThread, terminal_thread["id"]) is None


@pytest.mark.asyncio
async def test_cross_user_child_blocks_whole_conversation_delete(
    client,
    session,
    admin,
    member,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "Cross-user child")
    thread = await _create_thread(client, admin, account)
    turn = ConversationTurn(
        thread_id=thread["id"],
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id="cross-user-child",
        user_input="Do not partially delete",
        assistant_response="Done",
        status="completed",
    )
    session.add(turn)
    await session.flush()
    run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=member.id,
        thread_id=thread["id"],
        turn_id=turn.id,
        client_message_id=turn.client_message_id,
        status="completed",
        phase="completed",
        request_payload={},
    )
    session.add(run)
    await session.flush()
    turn_id = turn.id
    run_id = run.id
    await session.commit()

    blocked = await client.delete(
        f"/brain/conversations/{thread['id']}",
        headers=_auth(admin),
    )

    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "CONVERSATION_DELETE_BLOCKED"
    assert await session.get(ConversationThread, thread["id"]) is not None
    assert await session.get(ConversationTurn, turn_id) is not None
    assert await session.get(AgentRun, run_id) is not None


@pytest.mark.asyncio
async def test_owner_can_permanently_delete_conversation_and_execution_logs(
    client,
    session,
    admin,
    member,
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    monkeypatch.setattr(settings, "storage_local_dir", str(tmp_path))
    admin_headers = _auth(admin)
    member_headers = _auth(member)
    account = await _account(session, admin, "永久删除账号")
    account_id = account.id
    admin_id = admin.id
    org_id = admin.org_id
    thread = await _create_thread(client, admin, account)
    uploaded = await client.post(
        f"/brain/conversations/{thread['id']}/attachments",
        headers=admin_headers,
        files=[("files", ("private.txt", b"private conversation context", "text/plain"))],
    )
    assert uploaded.status_code == 201
    attachment_id = uploaded.json()[0]["id"]
    attachment = await session.get(ConversationAttachment, attachment_id)
    assert attachment is not None
    attachment_path = tmp_path / attachment.storage_key
    assert attachment_path.exists()
    submitted = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="delete-history-1",
        message="体检这个账号",
        requested_skill_code="account_inspection",
    )
    assert submitted.status_code == 202
    await _execute_queued_turn(
        session,
        admin,
        submitted,
        message="体检这个账号",
        requested_skill_code="account_inspection",
    )
    run = await session.get(AgentRun, submitted.json()["run"]["id"])
    assert run is not None
    assert run.task_id is not None
    task_id = run.task_id
    skill_run = await session.scalar(select(SkillRun).where(SkillRun.thread_id == thread["id"]))
    assert skill_run is not None
    content_item = ContentItem(
        account_id=account_id,
        created_by_id=admin_id,
        title="Preserved account inspection",
    )
    session.add(content_item)
    await session.flush()
    task = await session.get(BrainTask, run.task_id)
    assert task is not None
    task.content_item_id = content_item.id
    deliverable = Deliverable(
        content_item_id=content_item.id,
        thread_id=thread["id"],
        turn_id=submitted.json()["turn"]["id"],
        run_id=run.id,
        skill_run_id=skill_run.id,
        agent_code=AgentCode.OPERATOR.value,
        type=DeliverableType.REVIEW_REPORT,
        version=1,
        status=DeliverableStatus.PENDING_REVIEW,
        payload={"artifact_type": "account_inspection_report"},
    )
    approved_deliverable = Deliverable(
        content_item_id=content_item.id,
        thread_id=thread["id"],
        turn_id=submitted.json()["turn"]["id"],
        run_id=run.id,
        skill_run_id=skill_run.id,
        agent_code=AgentCode.OPERATOR.value,
        type=DeliverableType.REVIEW_REPORT,
        version=2,
        status=DeliverableStatus.APPROVED,
        payload={"artifact_type": "approved_account_inspection_report"},
    )
    session.add_all([deliverable, approved_deliverable])
    await session.flush()
    preserved_event = Event(
        type="preserved.audit",
        content_item_id=content_item.id,
        payload={"kept": True},
    )
    formal_event = Event(
        type="approval.decided",
        content_item_id=content_item.id,
        thread_id=thread["id"],
        turn_id=submitted.json()["turn"]["id"],
        run_id=run.id,
        skill_run_id=skill_run.id,
        payload={
            "approval_kind": "publish_package",
            "approved": True,
            "decided_by": admin.id,
            "comment": "must not survive",
            "title": "must not survive",
        },
    )
    technical_event = Event(
        type="agent.kernel.tool_end",
        content_item_id=content_item.id,
        thread_id=thread["id"],
        turn_id=submitted.json()["turn"]["id"],
        run_id=run.id,
        skill_run_id=skill_run.id,
        payload={"tool_code": "account.profile"},
    )
    session.add_all([preserved_event, formal_event, technical_event])
    owned_llm_call = LLMCall(
        org_id=admin.org_id,
        created_by_id=admin.id,
        task_id=task.id,
        trace_id=f"conversation-thread-{thread['id']}",
        provider="test",
        model="test-model",
        cost_usd=0.125,
    )
    shared_llm_call = LLMCall(
        org_id=admin.org_id,
        created_by_id=member.id,
        task_id=task.id,
        trace_id="shared-task-call",
        provider="test",
        model="test-model",
    )
    invocation = await session.scalar(
        select(AgentInvocation).where(AgentInvocation.thread_id == thread["id"])
    )
    if invocation is None:
        invocation = AgentInvocation(
            task_id=task.id,
            run_id=run.id,
            skill_run_id=skill_run.id,
            thread_id=thread["id"],
            turn_id=submitted.json()["turn"]["id"],
            step_key="delete-history:operator",
            agent_code=AgentCode.OPERATOR,
            agent_name="Operator",
            status=AgentInvocationStatus.DONE,
        )
        session.add(invocation)
        await session.flush()
    write_tool_call = AgentToolCall(
        org_id=admin.org_id,
        task_id=task.id,
        invocation_id=invocation.id,
        skill_run_id=skill_run.id,
        thread_id=thread["id"],
        turn_id=submitted.json()["turn"]["id"],
        tool_code="provider.publish",
        tool_name="Provider publish",
        idempotency_key="delete-write-audit",
        provider_idempotency_key="provider-delete-write-audit",
        side_effect_level="non_idempotent_write",
        status="success",
        cost=Decimal("0.25"),
    )
    session.add_all([owned_llm_call, shared_llm_call, write_tool_call])
    await session.flush()
    write_attempt = ToolExecutionAttempt(
        tool_call_id=write_tool_call.id,
        attempt_no=1,
        status="success",
        provider_idempotency_key=write_tool_call.provider_idempotency_key,
    )
    session.add(write_attempt)
    read_tool_ids = set(
        await session.scalars(
            select(AgentToolCall.id).where(
                AgentToolCall.thread_id == thread["id"],
                AgentToolCall.side_effect_level == "read",
            )
        )
    )
    content_item_id = content_item.id
    deliverable_id = deliverable.id
    approved_deliverable_id = approved_deliverable.id
    preserved_event_id = preserved_event.id
    formal_event_id = formal_event.id
    technical_event_id = technical_event.id
    owned_llm_call_id = owned_llm_call.id
    shared_llm_call_id = shared_llm_call.id
    write_tool_call_id = write_tool_call.id
    write_attempt_id = write_attempt.id
    await session.commit()

    denied = await client.delete(
        f"/brain/conversations/{thread['id']}",
        headers=member_headers,
    )
    deleted = await client.delete(
        f"/brain/conversations/{thread['id']}",
        headers=admin_headers,
    )

    assert denied.status_code == 404
    assert deleted.status_code == 200
    deletion_summary = deleted.json()
    assert deletion_summary["messages_deleted"] == 1
    assert deletion_summary["events_deleted"] >= 2
    assert deletion_summary["llm_calls_deleted"] >= 1
    assert deletion_summary["attachments_deleted"] == 1
    assert deletion_summary["draft_artifacts_deleted"] == 1
    assert deletion_summary["retained_audit_categories"] == ["approval", "cost", "publish"]
    session.expire_all()
    assert await session.get(ConversationThread, thread["id"]) is None
    assert (
        await session.scalar(
            select(func.count(ConversationTurn.id)).where(
                ConversationTurn.thread_id == thread["id"]
            )
        )
        == 0
    )
    preserved_task = await session.get(BrainTask, task_id)
    assert preserved_task is not None
    assert preserved_task.content_item_id == content_item_id
    preserved_content = await session.get(ContentItem, content_item_id)
    preserved_deliverable = await session.get(Deliverable, deliverable_id)
    retained_approved_deliverable = await session.get(Deliverable, approved_deliverable_id)
    assert preserved_content is not None
    assert preserved_deliverable is None
    assert retained_approved_deliverable is not None
    assert retained_approved_deliverable.thread_id is None
    assert retained_approved_deliverable.turn_id is None
    assert retained_approved_deliverable.run_id is None
    assert retained_approved_deliverable.skill_run_id is None

    assert (
        await session.scalar(
            select(func.count(AgentRun.id)).where(AgentRun.thread_id == thread["id"])
        )
        == 0
    )
    assert (
        await session.scalar(
            select(func.count(SkillRun.id)).where(SkillRun.thread_id == thread["id"])
        )
        == 0
    )
    assert (
        await session.scalar(select(func.count(Event.id)).where(Event.thread_id == thread["id"]))
        == 0
    )
    preserved_event_row = await session.get(Event, preserved_event_id)
    assert preserved_event_row is not None
    assert preserved_event_row.type == "preserved.audit"
    formal_event_row = await session.get(Event, formal_event_id)
    assert formal_event_row is None
    assert await session.get(Event, technical_event_id) is None
    assert await session.get(LLMCall, owned_llm_call_id) is None
    assert await session.get(LLMCall, shared_llm_call_id) is not None
    for read_tool_id in read_tool_ids:
        assert await session.get(AgentToolCall, read_tool_id) is None
    assert await session.get(AgentToolCall, write_tool_call_id) is None
    assert await session.get(ToolExecutionAttempt, write_attempt_id) is None
    assert await session.get(ConversationAttachment, attachment_id) is None
    assert not attachment_path.exists()
    audit_rows = list(
        await session.scalars(
            select(AuditRecord)
            .where(
                AuditRecord.org_id == org_id,
                AuditRecord.account_id == account_id,
            )
            .order_by(AuditRecord.category)
        )
    )
    assert [row.category for row in audit_rows] == ["approval", "cost", "publish"]
    assert all(not hasattr(row, "thread_id") for row in audit_rows)
    assert all(
        row.details.keys() <= {"approved", "amount_usd", "provider_status"} for row in audit_rows
    )


@pytest.mark.asyncio
async def test_permanent_delete_does_not_claim_success_when_attachment_removal_fails(
    client,
    session,
    admin,
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    monkeypatch.setattr(settings, "storage_local_dir", str(tmp_path))
    account = await _account(session, admin, "附件删除失败账号")
    thread = await _create_thread(client, admin, account)
    uploaded = await client.post(
        f"/brain/conversations/{thread['id']}/attachments",
        headers=_auth(admin),
        files=[("files", ("private.txt", b"private", "text/plain"))],
    )
    attachment_id = uploaded.json()[0]["id"]
    attachment = await session.get(ConversationAttachment, attachment_id)
    assert attachment is not None
    attachment_path = tmp_path / attachment.storage_key

    def reject_unlink(_path, *args, **kwargs):
        del args, kwargs
        raise OSError("storage unavailable")

    monkeypatch.setattr(type(attachment_path), "unlink", reject_unlink)

    with pytest.raises(OSError, match="storage unavailable"):
        await delete_conversation_thread(session, admin, thread["id"])

    session.expire_all()
    assert await session.get(ConversationThread, thread["id"]) is not None
    assert await session.get(ConversationAttachment, attachment_id) is not None
    assert attachment_path.exists()


@pytest.mark.asyncio
async def test_submit_turn_claims_one_owned_run_without_task(
    client, session, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "提交账号")
    thread = await _create_thread(client, admin, account)
    enqueued: list[int] = []

    async def capture_enqueue(*, run_id: int) -> None:
        enqueued.append(run_id)

    async def reject_inline_execution(*_args, **_kwargs):
        raise AssertionError("conversation runtime must not execute in the HTTP request")

    monkeypatch.setattr(
        "app.api.conversations.enqueue_agent_runtime",
        capture_enqueue,
        raising=False,
    )
    monkeypatch.setattr(
        "app.api.conversations.execute_conversation_turn",
        reject_inline_execution,
        raising=False,
    )

    response = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="turn-api-1",
        message="查看最近七天数据",
        requested_skill_code="account_data_query",
        attachment_ids=[],
    )

    assert response.status_code == 202
    body = response.json()
    assert body["turn"]["thread_id"] == thread["id"]
    assert body["turn"]["client_message_id"] == "turn-api-1"
    assert body["run"]["thread_id"] == thread["id"]
    assert body["run"]["turn_id"] == body["turn"]["id"]
    assert body["run"]["task_id"] is None
    assert body["task_id"] is None
    assert body["run"]["status"] == "queued"
    assert body["run"]["phase"] == "queued"
    assert body["turn"]["assistant_response"] is None
    assert body["projections"] == []
    assert body["turn"]["projections"] == body["projections"]
    assert enqueued == [body["run"]["id"]]

    run = await session.get(AgentRun, body["run"]["id"])
    assert run is not None
    assert run.task_id is None
    assert run.thread_id == thread["id"]
    assert run.turn_id == body["turn"]["id"]
    assert run.request_payload == {
        "account_id": account.id,
        "attachment_ids": [],
        "attachment_contexts": [],
        "client_message_id": "turn-api-1",
        "execution_preference": "AUTO",
            "message": "查看最近七天数据",
            "requested_skill_code": "account_data_query",
            "target_turn_id": None,
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
async def test_duplicate_queued_turn_safely_retries_enqueue(
    client, session, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "幂等入队账号")
    thread = await _create_thread(client, admin, account)
    enqueued: list[tuple[str, int, str]] = []
    enqueue_results = [object(), None]

    class Pool:
        async def enqueue_job(self, name: str, run_id: int, *, _job_id: str):
            enqueued.append((name, run_id, _job_id))
            return enqueue_results.pop(0)

    async def get_pool():
        return Pool()

    monkeypatch.setattr(
        "app.api.conversations.enqueue_agent_runtime",
        enqueue_agent_runtime,
        raising=False,
    )
    monkeypatch.setattr("app.core.events.get_arq_pool", get_pool)

    first = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="enqueue-once-1",
        message="查看最近七天数据",
    )
    replay = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="enqueue-once-1",
        message="查看最近七天数据",
    )

    assert first.status_code == 202
    assert replay.status_code == 202
    assert first.json()["dispatch_deferred"] is False
    assert replay.json()["dispatch_deferred"] is False
    assert first.json()["dispatch_message"] is None
    assert replay.json()["dispatch_message"] is None
    assert replay.json()["run"]["id"] == first.json()["run"]["id"]
    run_id = first.json()["run"]["id"]
    assert enqueued == [
        ("execute_agent_run", run_id, f"agent-run:{run_id}"),
        ("execute_agent_run", run_id, f"agent-run:{run_id}"),
    ]
    assert await session.scalar(select(func.count(ConversationTurn.id))) == 1
    assert await session.scalar(select(func.count(AgentRun.id))) == 1


@pytest.mark.asyncio
async def test_queue_submission_failure_keeps_a_durable_queued_run(
    client, session, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "队列恢复账号")
    thread = await _create_thread(client, admin, account)

    async def fail_enqueue(*, run_id: int) -> None:
        raise ConnectionError(f"queue unavailable for run {run_id}")

    monkeypatch.setattr(
        "app.api.conversations.enqueue_agent_runtime",
        fail_enqueue,
        raising=False,
    )

    response = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="queue-failure-1",
        message="查看最近七天数据",
    )

    assert response.status_code == 202
    assert response.json()["dispatch_deferred"] is True
    assert response.json()["dispatch_message"] == "任务已保存，调度暂时延迟，系统将自动恢复。"

    run = await session.scalar(
        select(AgentRun).where(AgentRun.client_message_id == "queue-failure-1")
    )
    assert run is not None
    assert run.status == "queued"
    assert run.phase == "queued"

    enqueued: list[tuple[tuple, dict]] = []

    class RecoveryPool:
        async def enqueue_job(self, *args, **kwargs):
            enqueued.append((args, kwargs))
            return object()

    @asynccontextmanager
    async def test_session_factory():
        yield session

    monkeypatch.setattr("app.worker.async_session", test_session_factory)
    recovered = await recover_agent_runs({"redis": RecoveryPool()})

    assert recovered == 1
    assert enqueued[0][0] == ("execute_agent_run", run.id)
    assert enqueued[0][1]["_job_id"].startswith(f"agent-run:{run.id}:recovery:")


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
    result, _turn, _run = await _execute_queued_turn(
        session,
        admin,
        response,
        message="你好",
    )
    response_events = [
        (event_type, payload)
        for event_type, payload in realtime_events
        if event_type
        in {
            "brain.runtime.message_start",
            "brain.runtime.message_delta",
            "brain.runtime.message_done",
        }
    ]
    assert [event_type for event_type, _payload in response_events] == [
        "brain.runtime.message_done"
    ]
    assert response_events[0][1]["content"] == result.response
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
    await _execute_queued_turn(
        session,
        admin,
        response,
        message="Run a capability that is not in the public catalog",
        requested_skill_code="not_registered",
    )
    replay = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="unsupported-explicit-skill",
        message="Run a capability that is not in the public catalog",
        requested_skill_code="not_registered",
    )
    body = replay.json()
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
    await _execute_queued_turn(
        session,
        admin,
        first,
        message=request["message"],
        requested_skill_code=request["requested_skill_code"],
    )
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
            select(func.count(AgentRun.id)).where(AgentRun.thread_id == thread["id"])
        )
        == 1
    )
    skill_run = await session.scalar(select(SkillRun).where(SkillRun.thread_id == thread["id"]))
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
    assert all("attempt" in expert for expert in execution["experts"])
    assert all("duration_ms" in expert for expert in execution["experts"])


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
    await _execute_queued_turn(
        session,
        admin,
        submitted,
        message="体检这个账号",
        requested_skill_code="account_inspection",
    )
    skill_run = await session.scalar(select(SkillRun).where(SkillRun.thread_id == thread["id"]))
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
    execution = next(item for item in projections if item["type"] == "execution_summary")
    assert execution["experts"][0]["agent_name"] == "账号定位专家"


@pytest.mark.asyncio
async def test_turn_projects_sanitized_tool_only_execution_summary(
    client, session, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "仅工具日志账号")
    thread = await _create_thread(client, admin, account)
    submitted = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="tool-only-summary",
        message="读取账号资料",
    )
    body = submitted.json()
    turn = await session.get(ConversationTurn, body["turn"]["id"])
    assert turn is not None
    turn.route_ms = 12
    turn.first_token_ms = 120
    turn.completion_ms = 420
    turn.total_ms = 430
    turn.model_call_count = 2
    turn.tool_call_count = 1
    turn.intent = {
        "mode": "query",
        "reason": "SECRET_MODEL_REASON",
        "prompt": "SECRET_PROMPT",
        "provider_body": {"token": "sk-secret"},
    }
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="只调用工具",
    )
    session.add(task)
    await session.flush()
    tool_call = AgentToolCall(
        org_id=admin.org_id,
        task_id=task.id,
        thread_id=thread["id"],
        turn_id=body["turn"]["id"],
        tool_code="account.profile",
        tool_name="账号资料",
        status="success",
        side_effect_level="read",
        input_summary="SECRET_INPUT",
        output_summary="SECRET_OUTPUT",
        error="Traceback: SHOULD_NOT_LEAK",
        provider_idempotency_key="provider-key-MUST_NOT_LEAK",
        meta={"raw_input": "MUST_NOT_LEAK", "api_key": "sk-secret"},
        latency_ms=37,
        requires_human_confirmation=False,
    )
    session.add(tool_call)
    await session.flush()
    session.add_all(
        [
            ToolExecutionAttempt(
                tool_call_id=tool_call.id,
                attempt_no=1,
                status="failed",
                error="raw provider body MUST_NOT_LEAK",
            ),
            ToolExecutionAttempt(
                tool_call_id=tool_call.id,
                attempt_no=2,
                status="success",
            ),
        ]
    )
    await session.commit()

    history = await client.get(
        f"/brain/conversations/{thread['id']}",
        headers=_auth(admin),
    )

    summary = next(
        projection
        for projection in history.json()["turns"][0]["projections"]
        if projection["type"] == "execution_summary"
    )
    returned_turn = history.json()["turns"][0]
    assert returned_turn["route_ms"] == 12
    assert returned_turn["first_token_ms"] == 120
    assert returned_turn["completion_ms"] == 420
    assert returned_turn["total_ms"] == 430
    assert returned_turn["model_call_count"] == 2
    assert returned_turn["tool_call_count"] == 1
    assert returned_turn["intent"] == {
        "mode": "query",
        "route_source": "model",
        "skill_code": None,
    }
    assert summary["run_id"] == body["run"]["id"]
    assert summary["mode"] == "query"
    assert summary["route_source"] == "model"
    assert summary["experts"] == []
    assert summary["tools"] == [
        {
            "id": summary["tools"][0]["id"],
            "tool_code": "account.profile",
            "tool_name": "账号资料",
            "status": "success",
            "duration_ms": 37,
            "retry_count": 1,
            "requires_confirmation": False,
            "side_effect_level": "read",
        }
    ]
    serialized = json.dumps(summary, ensure_ascii=False)
    assert "SECRET_INPUT" not in serialized
    assert "SECRET_OUTPUT" not in serialized
    assert "Traceback" not in serialized
    assert "SECRET_MODEL_REASON" not in json.dumps(returned_turn, ensure_ascii=False)
    assert "SECRET_PROMPT" not in json.dumps(returned_turn, ensure_ascii=False)
    assert "MUST_NOT_LEAK" not in serialized
    assert "sk-secret" not in serialized


@pytest.mark.asyncio
async def test_history_fail_closed_sanitizes_every_result_payload_projection_kind(
    client, session, admin, monkeypatch
) -> None:
    """AgentRun payloads are untrusted trace data, never a public API passthrough."""

    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "projection-security")
    thread = await _create_thread(client, admin, account)
    submitted = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="projection-security",
        message="inspect projection safety",
    )
    body = submitted.json()
    run = await session.get(AgentRun, body["run"]["id"])
    assert run is not None
    malicious = {
        "prompt": "SECRET_PROMPT",
        "api_key": "sk-secret",
        "provider_body": {"raw_output": "RAW_TOOL_OUTPUT"},
    }
    run.result_payload = {
        "projections": [
            {"type": "answer", "message": "SECRET_PROMPT", **malicious},
            {
                "type": "progress",
                "skill_run_id": 101,
                "stages": [
                    {
                        "code": "analysis",
                        "name": "Data analysis",
                        "status": "completed",
                        **malicious,
                    }
                ],
                **malicious,
            },
            {
                "type": "expert",
                "invocation": {
                    "id": 201,
                    "agent_code": "01-positioning",
                    "agent_name": "Positioning expert",
                    "status": "done",
                    "attempt": 0,
                    **malicious,
                },
                **malicious,
            },
            {
                "type": "artifact",
                "artifact_id": 301,
                "artifact_type": "account_inspection_report",
                "skill_run_id": 101,
                "account_id": account.id,
                "report": {"raw_output": "RAW_TOOL_OUTPUT", "api_key": "sk-secret"},
                **malicious,
            },
            {
                "type": "account_data",
                "account_id": account.id,
                "skill_code": "account_data_query",
                "skill_run_id": 102,
                "data": {"raw_output": "RAW_TOOL_OUTPUT", "api_key": "sk-secret"},
                **malicious,
            },
            {
                "type": "execution_blocked",
                "skill_run_id": 103,
                "skill_code": "account_inspection",
                "code": "EXECUTION_FAILED",
                "recovery_action": "Retry the account inspection.",
                **malicious,
            },
            {
                "type": "approval",
                "approval": {"id": 401, **malicious},
                **malicious,
            },
            {
                "type": "execution_summary",
                "run_id": run.id,
                "experts": [{"agent_name": "SECRET_PROMPT"}],
                "tools": [{"output": "RAW_TOOL_OUTPUT"}],
                **malicious,
            },
            {"type": "future_projection", **malicious},
        ]
    }
    await session.commit()

    history = await client.get(
        f"/brain/conversations/{thread['id']}",
        headers=_auth(admin),
    )

    assert history.status_code == 200
    projections = history.json()["turns"][0]["projections"]
    assert [item["type"] for item in projections] == [
        "progress",
        "expert",
        "artifact",
        "account_data",
        "execution_blocked",
    ]
    serialized = json.dumps(projections, ensure_ascii=False)
    assert "SECRET_PROMPT" not in serialized
    assert "sk-secret" not in serialized
    assert "RAW_TOOL_OUTPUT" not in serialized
    assert "provider_body" not in serialized
    assert all(item["turn_id"] == body["turn"]["id"] for item in projections)
    artifact_projection = next(item for item in projections if item["type"] == "artifact")
    assert "report" not in artifact_projection
    account_projection = next(item for item in projections if item["type"] == "account_data")
    assert "data" not in account_projection


@pytest.mark.asyncio
async def test_history_reconstructs_pending_approval_projection(
    client, session, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "审批恢复账号")
    thread = await _create_thread(client, admin, account)
    submitted = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="approval-history",
        message="准备发布",
    )
    body = submitted.json()
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="审批恢复",
    )
    session.add(task)
    await session.flush()
    tool_call = AgentToolCall(
        org_id=admin.org_id,
        task_id=task.id,
        thread_id=thread["id"],
        turn_id=body["turn"]["id"],
        tool_code="publish_package_prepare",
        tool_name="生成发布包并进入人工审批",
        status="waiting_approval",
        permission_mode="confirm",
        requires_human_confirmation=True,
        side_effect_level="read",
        input_summary="SAFE_INPUT_SUMMARY",
        output_summary="SAFE_OUTPUT_SUMMARY",
        meta={"secret": "MUST_NOT_LEAK"},
    )
    session.add(tool_call)
    await session.commit()

    history = await client.get(
        f"/brain/conversations/{thread['id']}",
        headers=_auth(admin),
    )

    assert history.status_code == 200
    approval = next(
        projection
        for projection in history.json()["turns"][0]["projections"]
        if projection["type"] == "approval"
    )
    assert approval == {
        "type": "approval",
        "turn_id": body["turn"]["id"],
        "approval": {
            "id": tool_call.id,
            "task_id": task.id,
            "tool_code": "publish_package_prepare",
            "tool_name": "生成发布包并进入人工审批",
            "status": "waiting_approval",
            "permission_mode": "confirm",
            "requires_human_confirmation": True,
        },
    }
    assert "MUST_NOT_LEAK" not in json.dumps(approval, ensure_ascii=False)


@pytest.mark.asyncio
async def test_turn_projects_critic_only_quality_summary(
    client, session, admin, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
    account = await _account(session, admin, "仅质量门日志账号")
    thread = await _create_thread(client, admin, account)
    submitted = await _submit_turn(
        client,
        admin,
        thread["id"],
        client_message_id="critic-only-summary",
        message="检查现有成果质量",
    )
    body = submitted.json()
    session.add(
        SkillRun(
            org_id=admin.org_id,
            thread_id=thread["id"],
            turn_id=body["turn"]["id"],
            run_id=body["run"]["id"],
            task_id=None,
            idempotency_key="critic-only-summary",
            skill_code="artifact_quality_review",
            skill_version=1,
            status="completed",
            input_snapshot={},
            output_snapshot={"raw_critic_prompt": "SHOULD_NOT_LEAK"},
            quality_score=Decimal("0.9300"),
        )
    )
    await session.commit()

    history = await client.get(
        f"/brain/conversations/{thread['id']}",
        headers=_auth(admin),
    )

    summary = next(
        projection
        for projection in history.json()["turns"][0]["projections"]
        if projection["type"] == "execution_summary"
    )
    assert summary["run_id"] == body["run"]["id"]
    assert summary["skill_code"] == "artifact_quality_review"
    assert summary["quality_score"] == 0.93
    assert summary["experts"] == []
    assert summary["tools"] == []
    assert "SHOULD_NOT_LEAK" not in json.dumps(summary, ensure_ascii=False)


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
        "MAIN_AGENT_V2_ROLLOUT_RESTRICTED" not in str(response.json()) for response in responses
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
    result, _turn, _run = await _execute_queued_turn(
        session,
        admin,
        response,
        message="查看近七天数据",
        requested_skill_code="account_data_query",
    )
    body = response.json()
    skill_run_id = result.projections[0]["skill_run_id"]
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
    custom_keys = set(record.__dict__) - set(logging.makeLogRecord({}).__dict__) - {"message"}
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
    result, _turn, _run = await _execute_queued_turn(
        session,
        admin,
        response,
        message="sensitive rollout prompt that must not be logged",
        requested_skill_code="account_data_query",
    )
    body = response.json()
    skill_run_id = result.projections[0]["skill_run_id"]
    record = next(
        entry for entry in caplog.records if "main_agent_turn_completed" in entry.getMessage()
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
