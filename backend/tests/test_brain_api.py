import asyncio
import json

import pytest
from sqlalchemy import select

from app.llm.adapters import CompletionResult
from app.models import AgentRun, BrainTask, ContentItem, Event, GateApproval
from app.models.enums import GateStatus, GateType, UserRole
from app.schemas.brain import (
    DecisionChoice,
    DecisionRequest,
    IntentDecision,
    RuntimeNextStep,
    RuntimeToolCall,
)
from app.tools import ToolAdapter, ToolSpec
from app.tools.adapter import EmptyParams

_POSITIONING_JSON = json.dumps(
    {
        "account_persona": "露营装备测评号",
        "target_audience": "25-35 岁户外用户",
        "differentiation": ["夜间真实测试", "装备清单拆解"],
        "content_pillars": ["新品冷启动", "场景测评"],
    }
)

_SCRIPT_JSON = json.dumps(
    {
        "title": "这盏营地灯真的能救场吗",
        "hook": "停电以后，最先慌的不是人，是没电的灯。",
        "scenes": ["夜间开场", "亮度测试", "收纳对比"],
        "duration_seconds": 45,
        "bgm_suggestion": "轻快户外氛围",
    }
)

_REVIEW_JSON = json.dumps(
    {
        "period": "最近 30 天",
        "summary": "完播率下降主要集中在前三秒钩子和内容节奏。",
        "key_metrics": {"completion_rate": 0.21},
        "highlights": ["真实测评内容互动较高"],
        "issues": ["前三秒信息密度不足"],
        "optimization_suggestions": ["下一轮优先测试冲突型开场"],
    }
)


@pytest.fixture(autouse=True)
def _stub_pipeline_llm(monkeypatch):
    async def fake_chat(self, session, org_id, agent_code, messages):
        if agent_code == "01-positioning":
            content = _POSITIONING_JSON
        elif agent_code in {"06-operation", "06-operator"}:
            content = _REVIEW_JSON
        else:
            content = _SCRIPT_JSON
        return CompletionResult(content, "deepseek-chat", 10, 20, 30), 0.0

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", fake_chat)
    monkeypatch.setattr("app.config.settings.ark_api_key", "")


async def _token(client, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _project_bound_douyin_account(
    client,
    headers: dict[str, str],
    *,
    account_name: str = "Agent runtime account",
    project_name: str = "Agent runtime project",
) -> tuple[int, int]:
    project_id = (
        await client.post("/projects", headers=headers, json={"name": project_name})
    ).json()["id"]
    account = (
        await client.post(
            "/accounts",
            headers=headers,
            json={
                "nickname": account_name,
                "platform": "douyin",
                "external_account_id": f"open-id-{project_id}",
                "project_id": project_id,
            },
        )
    ).json()
    await client.patch(
        f"/accounts/{account['id']}/integration",
        headers=headers,
        json={
            "integration_status": "manual",
            "auth_status": "manual",
            "data_sync_status": "manual",
        },
    )
    return project_id, account["id"]


async def _authorized_douyin_account(
    client,
    headers: dict[str, str],
    name: str = "抖音账号",
) -> int:
    account = (
        await client.post(
            "/accounts",
            headers=headers,
            json={"nickname": name, "platform": "douyin", "external_account_id": "open-id"},
        )
    ).json()
    await client.patch(
        f"/accounts/{account['id']}/integration",
        headers=headers,
        json={
            "integration_status": "manual",
            "auth_status": "manual",
            "data_sync_status": "manual",
        },
    )
    return account["id"]


@pytest.mark.asyncio
async def test_brain_message_greeting_stays_in_main_agent_conversation(client, session, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    response = await client.post(
        "/brain/messages",
        headers=_auth(token),
        json={"message": "你好", "client_message_id": "message-greeting-1"},
    )

    assert response.status_code == 201
    runtime = response.json()
    assert runtime["intent"]["intent"] == "conversation"
    assert runtime["invocations"] == []
    assert runtime["pending_decisions"] == []
    completed_message = next(
        event for event in runtime["timeline"] if event["type"] == "brain.runtime.message_done"
    )
    assert completed_message["payload"]["client_message_id"] == "message-greeting-1"
    assert completed_message["payload"]["message_id"].startswith("message-greeting-1:")
    task = await session.get(BrainTask, runtime["task"]["id"])
    assert task is not None
    assert task.created_by_id == admin.id


@pytest.mark.asyncio
async def test_brain_message_client_id_is_idempotent(client, session, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    payload = {"message": "你好", "client_message_id": "idempotent-turn-1"}

    first = await client.post("/brain/messages", headers=headers, json=payload)
    repeated = await client.post("/brain/messages", headers=headers, json=payload)

    assert first.status_code == 201
    assert repeated.status_code == 201
    first_runtime = first.json()
    repeated_runtime = repeated.json()
    assert repeated_runtime["task"]["id"] == first_runtime["task"]["id"]

    task_id = first_runtime["task"]["id"]
    events = [
        event
        for event in await session.scalars(select(Event).order_by(Event.id))
        if (event.payload or {}).get("task_id") == task_id
    ]
    user_messages = [event for event in events if event.type == "brain.runtime.user_message"]
    completed = [event for event in events if event.type == "brain.runtime.message_done"]
    assert len(user_messages) == 1
    assert len(completed) == 1


@pytest.mark.asyncio
async def test_brain_message_enqueues_runtime_when_async_execution_is_enabled(
    client, session, admin, monkeypatch
):
    enqueued: list[dict] = []

    async def fake_enqueue(**payload):
        enqueued.append(payload)

    monkeypatch.setattr("app.config.settings.agent_runtime_async_enabled", True)
    monkeypatch.setattr("app.api.brain.enqueue_agent_runtime", fake_enqueue)
    token = await _token(client, "admin@test.com", "admin-pw-123")

    response = await client.post(
        "/brain/messages",
        headers=_auth(token),
        json={"message": "你好", "client_message_id": "queued-turn-1"},
    )

    assert response.status_code == 201
    runtime = response.json()
    assert runtime["status"] == "running"
    assert not any(
        event["type"] == "brain.runtime.message_done" for event in runtime["timeline"]
    )
    assert len(enqueued) == 1
    run = await session.scalar(
        select(AgentRun).where(AgentRun.client_message_id == "queued-turn-1")
    )
    assert run is not None
    assert run.status == "queued"
    assert run.task_id == runtime["task"]["id"]


@pytest.mark.asyncio
async def test_async_followup_waits_without_mutating_the_active_turn(
    client, session, admin, monkeypatch
):
    enqueued: list[dict] = []

    async def fake_enqueue(**payload):
        enqueued.append(payload)

    monkeypatch.setattr("app.config.settings.agent_runtime_async_enabled", True)
    monkeypatch.setattr("app.api.brain.enqueue_agent_runtime", fake_enqueue)
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    first = await client.post(
        "/brain/messages",
        headers=headers,
        json={"message": "你好", "client_message_id": "serialized-turn-1"},
    )
    assert first.status_code == 201
    task_id = first.json()["task"]["id"]

    followup = await client.post(
        "/brain/messages",
        headers=headers,
        json={
            "message": "谢谢",
            "client_message_id": "serialized-turn-2",
            "task_id": task_id,
        },
    )

    assert followup.status_code == 201
    assert len(enqueued) == 1
    task = await session.get(BrainTask, task_id)
    assert task is not None
    await session.refresh(task, attribute_names=["brief"])
    assert task.brief.goal == "你好"
    run = await session.scalar(
        select(AgentRun).where(
            AgentRun.client_message_id == "serialized-turn-2"
        )
    )
    assert run is not None
    assert run.status == "waiting_predecessor"
    events = [
        event
        for event in await session.scalars(select(Event).order_by(Event.id))
        if (event.payload or {}).get("task_id") == task_id
        and event.type == "brain.runtime.user_message"
    ]
    assert [event.payload["message"] for event in events] == ["你好", "谢谢"]


@pytest.mark.asyncio
async def test_stop_generation_marks_queued_run_and_aborts_worker_job(
    client, session, admin, monkeypatch
):
    aborted: list[int] = []

    async def fake_enqueue(**payload):
        return None

    async def fake_abort(run_id: int):
        aborted.append(run_id)
        return True

    monkeypatch.setattr("app.config.settings.agent_runtime_async_enabled", True)
    monkeypatch.setattr("app.api.brain.enqueue_agent_runtime", fake_enqueue)
    monkeypatch.setattr("app.api.brain.abort_agent_runtime", fake_abort)
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    await client.post(
        "/brain/messages",
        headers=headers,
        json={"message": "你好", "client_message_id": "queued-stop-1"},
    )

    stopped = await client.post(
        "/brain/generations/queued-stop-1/stop",
        headers=headers,
        json={},
    )

    assert stopped.status_code == 202
    run = await session.scalar(
        select(AgentRun).where(AgentRun.client_message_id == "queued-stop-1")
    )
    assert run is not None
    assert run.cancel_requested_at is not None
    assert aborted == [run.id]


@pytest.mark.asyncio
async def test_brain_message_continues_in_the_same_runtime_thread(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)

    first = await client.post(
        "/brain/messages",
        headers=headers,
        json={"message": "你好", "client_message_id": "conversation-turn-1"},
    )
    assert first.status_code == 201
    first_runtime = first.json()

    continued = await client.post(
        "/brain/messages",
        headers=headers,
        json={
            "message": "谢谢",
            "client_message_id": "conversation-turn-2",
            "task_id": first_runtime["task"]["id"],
        },
    )

    assert continued.status_code == 201
    runtime = continued.json()
    assert runtime["task"]["id"] == first_runtime["task"]["id"]
    assert runtime["thread_id"] == first_runtime["thread_id"]
    user_messages = [
        event for event in runtime["timeline"] if event["type"] == "brain.runtime.user_message"
    ]
    assert [event["payload"]["message"] for event in user_messages] == [
        "你好",
        "谢谢",
    ]
    assistant_message_ids = [
        event["payload"]["message_id"]
        for event in runtime["timeline"]
        if event["type"] == "brain.runtime.message_done"
    ]
    assert len(assistant_message_ids) == 2
    assert len(set(assistant_message_ids)) == 2
    assert assistant_message_ids[0].startswith("conversation-turn-1:")
    assert assistant_message_ids[1].startswith("conversation-turn-2:")


@pytest.mark.asyncio
async def test_brain_message_can_be_stopped_while_the_model_is_generating(
    client, admin, monkeypatch
):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    generation_started = asyncio.Event()

    async def fake_classify(*args, **kwargs):
        return IntentDecision(
            intent="conversation",
            confidence=1,
            reason="普通对话。",
            suggested_expert_codes=[],
            requires_account_context=False,
        )

    async def blocking_chat(self, session, org_id, agent_code, messages):
        generation_started.set()
        await asyncio.Event().wait()
        raise AssertionError("停止生成后不应继续返回模型结果")

    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.BrainIntelligence.classify", fake_classify
    )
    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", blocking_chat)

    request = asyncio.create_task(
        client.post(
            "/brain/messages",
            headers=headers,
            json={"message": "帮我想一个选题", "client_message_id": "stop-turn-1"},
        )
    )
    await asyncio.wait_for(generation_started.wait(), timeout=10)

    stopped = await client.post(
        "/brain/generations/stop-turn-1/stop",
        headers=headers,
        json={},
    )
    response = await asyncio.wait_for(request, timeout=2)

    assert stopped.status_code == 202
    assert stopped.json() == {
        "client_message_id": "stop-turn-1",
        "stop_requested": True,
    }
    assert response.status_code == 201
    runtime = response.json()
    assert runtime["status"] == "stopped"
    stopped_event = next(
        event
        for event in runtime["timeline"]
        if event["type"] == "brain.runtime.generation_stopped"
    )
    assert stopped_event["payload"]["client_message_id"] == "stop-turn-1"


@pytest.mark.asyncio
async def test_brain_message_regeneration_reuses_the_last_user_turn_without_duplication(
    client, admin
):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    first = await client.post(
        "/brain/messages",
        headers=headers,
        json={"message": "你好", "client_message_id": "original-turn"},
    )
    assert first.status_code == 201
    task_id = first.json()["task"]["id"]

    regenerated = await client.post(
        f"/brain/tasks/{task_id}/regenerate",
        headers=headers,
        json={"client_message_id": "regenerated-turn"},
    )

    assert regenerated.status_code == 201
    runtime = regenerated.json()
    user_messages = [
        event for event in runtime["timeline"] if event["type"] == "brain.runtime.user_message"
    ]
    assert [event["payload"]["message"] for event in user_messages] == ["你好"]
    regeneration = next(
        event
        for event in runtime["timeline"]
        if event["type"] == "brain.runtime.regeneration_requested"
    )
    assert regeneration["payload"]["client_message_id"] == "regenerated-turn"
    assert regeneration["payload"]["source_event_id"] == user_messages[0]["id"]
    assistant_message_ids = [
        event["payload"]["message_id"]
        for event in runtime["timeline"]
        if event["type"] == "brain.runtime.message_done"
    ]
    assert len(assistant_message_ids) == 2
    assert assistant_message_ids[-1].startswith("regenerated-turn:")


@pytest.mark.asyncio
async def test_brain_message_sends_compact_parent_thread_history_to_main_agent(
    client, admin, monkeypatch
):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    captured_messages: list[list[dict]] = []

    async def fake_chat(self, session, org_id, agent_code, messages):
        if agent_code == "00-decision":
            captured_messages.append(messages)
            content = "I remember the previous turn." if len(captured_messages) > 1 else "Hello."
        else:
            content = _SCRIPT_JSON
        return CompletionResult(content, "deepseek-chat", 10, 20, 30), 0.0

    async def fake_classify(*args, **kwargs):
        return IntentDecision(
            intent="conversation",
            confidence=1,
            reason="继续普通对话。",
            suggested_expert_codes=[],
            requires_account_context=False,
        )

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", fake_chat)
    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.BrainIntelligence.classify", fake_classify
    )

    first = await client.post(
        "/brain/messages",
        headers=headers,
        json={"message": "你好"},
    )
    first_runtime = first.json()

    continued = await client.post(
        "/brain/messages",
        headers=headers,
        json={
            "message": "我们刚才说到哪里了？",
            "task_id": first_runtime["task"]["id"],
        },
    )

    assert continued.status_code == 201
    second_turn = captured_messages[-1]
    serialized = json.dumps(second_turn, ensure_ascii=False)
    assert "你好" in serialized
    assert "Hello." in serialized
    assert "我们刚才说到哪里了？" in serialized


@pytest.mark.asyncio
async def test_brain_conversation_keeps_operations_identity_and_account_context(
    client, admin, monkeypatch
):
    from app.llm.gateway import current_llm_call_context

    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = await _authorized_douyin_account(
        client,
        headers,
        name="抖音开发测试账号",
    )
    captured_messages: list[dict] = []
    captured_contexts = []

    async def fake_chat(self, session, org_id, agent_code, messages):
        if agent_code == "00-decision":
            captured_messages.extend(messages)
            captured_contexts.append(current_llm_call_context())
            content = "我是你的新媒体运营主 Agent。"
        else:
            content = _SCRIPT_JSON
        return CompletionResult(content, "deepseek-chat", 10, 20, 30), 0.0

    async def fake_classify(*args, **kwargs):
        return IntentDecision(
            intent="conversation",
            confidence=1,
            reason="用户询问主 Agent 的能力。",
            suggested_expert_codes=[],
            requires_account_context=False,
        )

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", fake_chat)
    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.BrainIntelligence.classify", fake_classify
    )

    response = await client.post(
        "/brain/messages",
        headers=headers,
        json={
            "message": "你能干什么？",
            "account_id": account_id,
            "platform": "douyin",
        },
    )

    assert response.status_code == 201
    system_prompt = next(
        message["content"] for message in captured_messages if message["role"] == "system"
    )
    assert system_prompt.startswith("# 同舟行主 Agent：自然对话")
    assert "面向用户时统一使用“运营大脑”" in system_prompt
    assert "不是泛化生活陪聊助手" in system_prompt
    assert "账号定位" in system_prompt
    assert "内容策划" in system_prompt
    assert "发布准备" in system_prompt
    assert "运营复盘" in system_prompt
    assert "账号 ID" in system_prompt
    assert "抖音" in system_prompt
    assert captured_contexts[0].prompt_id == "main-agent.conversation"
    assert captured_contexts[0].task_id == response.json()["task"]["id"]
    assert captured_contexts[0].scope["account_id"] == account_id


@pytest.mark.asyncio
async def test_brain_message_replans_after_each_expert_result(client, admin, monkeypatch):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    project_id, account_id = await _project_bound_douyin_account(client, headers)
    customer_service = (
        await client.get("/agents/08-customer-service/management", headers=headers)
    ).json()
    disabled = await client.put(
        "/agents/08-customer-service/management",
        headers=headers,
        json={**customer_service, "enabled": False},
    )
    assert disabled.status_code == 200

    async def fake_classify(*args, **kwargs):
        return IntentDecision(
            intent="workflow",
            confidence=0.96,
            reason="需要先定位再制定内容策略。",
            suggested_expert_codes=["01-positioning", "02-content-director"],
            requires_account_context=True,
        )

    available_catalogs: list[list[dict]] = []
    next_steps = iter(
        [
            RuntimeNextStep(
                action="dispatch_experts",
                expert_codes=["01-positioning"],
                rationale="先确认账号定位，再决定是否需要内容专家。",
                handoff_message="我先让账号定位专家核对当前定位。",
            ),
            RuntimeNextStep(
                action="dispatch_experts",
                expert_codes=["02-content-director"],
                rationale="定位已完成，可以继续形成内容方向。",
                handoff_message="定位已经明确，接下来交给编导文案专家。",
            ),
            RuntimeNextStep(
                action="finish",
                expert_codes=[],
                rationale="目标所需结论已经齐全。",
                handoff_message="两位专家的结论已经齐全，我来汇总本轮结果。",
            ),
        ]
    )

    async def fake_decide_next(
        self, session, org_id, goal, observations, available_experts, round_index
    ):
        available_catalogs.append([dict(item) for item in available_experts])
        return next(next_steps)

    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.BrainIntelligence.classify", fake_classify
    )
    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.BrainIntelligence.decide_next",
        fake_decide_next,
    )

    response = await client.post(
        "/brain/messages",
        headers=headers,
        json={
            "message": "分析当前账号定位并制定下周内容策略",
            "project_id": project_id,
            "account_id": account_id,
            "platform": "douyin",
        },
    )

    assert response.status_code == 201
    runtime = response.json()
    assert {row["agent_code"] for row in runtime["invocations"]} == {
        "01-positioning",
        "02-content-director",
    }
    event_types = [event["type"] for event in runtime["timeline"]]
    first_decision = event_types.index("brain.runtime.next_step")
    positioning_started = next(
        index
        for index, event in enumerate(runtime["timeline"])
        if event["type"] == "brain.runtime.subagent_started"
        and event["payload"]["agent_code"] == "01-positioning"
    )
    positioning_done = next(
        index
        for index, event in enumerate(runtime["timeline"])
        if event["type"] == "brain.runtime.subagent_completed"
        and event["payload"]["agent_code"] == "01-positioning"
    )
    next_step = event_types.index("brain.runtime.next_step", first_decision + 1)
    content_started = next(
        index
        for index, event in enumerate(runtime["timeline"])
        if event["type"] == "brain.runtime.subagent_started"
        and event["payload"]["agent_code"] == "02-content-director"
    )
    assert first_decision < positioning_started < positioning_done < next_step < content_started
    assert {
        item["code"]
        for item in available_catalogs[0]
        if item["kind"] == "expert"
    } == {
        "01-positioning",
        "02-content-director",
        "03-art-director",
        "04-video-creator",
        "05-editor",
        "06-operator",
        "07-advertiser",
    }
    assert {
        item["code"]
        for item in available_catalogs[0]
        if item["kind"] == "tool"
    } == {
        "account.data_context",
        "account.metrics_summary",
        "account.profile",
    }


@pytest.mark.asyncio
async def test_brain_runtime_executes_scoped_tool_and_observes_result(
    client,
    admin,
    monkeypatch,
):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = await _authorized_douyin_account(client, headers, "工具测试账号")

    async def fake_classify(*args, **kwargs):
        return IntentDecision(
            intent="analysis",
            confidence=0.98,
            reason="需要读取当前账号概况。",
            requires_account_context=True,
        )

    steps = iter(
        [
            RuntimeNextStep(
                action="call_tools",
                tool_calls=[
                    RuntimeToolCall(
                        tool_code="account.profile",
                        arguments={},
                        purpose="读取当前账号公开概况",
                        idempotency_key="account-profile-round-1",
                    )
                ],
                rationale="先读取真实账号状态。",
                handoff_message="我先读取当前账号的接入状态。",
            ),
            RuntimeNextStep(
                action="finish",
                rationale="账号事实已经足够回答。",
                handoff_message="账号状态已经确认，我来汇总结论。",
            ),
        ]
    )

    async def fake_decide_next(*args, **kwargs):
        return next(steps)

    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.BrainIntelligence.classify",
        fake_classify,
    )
    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.BrainIntelligence.decide_next",
        fake_decide_next,
    )

    response = await client.post(
        "/brain/messages",
        headers=headers,
        json={
            "message": "检查当前账号接入状态",
            "account_id": account_id,
            "platform": "douyin",
        },
    )

    assert response.status_code == 201
    runtime = response.json()
    assert len(runtime["tool_calls"]) == 1
    assert runtime["tool_calls"][0]["tool_code"] == "account.profile"
    assert runtime["tool_calls"][0]["status"] == "success"
    completed = next(
        event
        for event in runtime["timeline"]
        if event["type"] == "brain.runtime.tool_completed"
    )
    assert completed["payload"]["tool_code"] == "account.profile"
    assert completed["payload"]["result"]["account_id"] == account_id


@pytest.mark.asyncio
async def test_brain_runtime_respond_completes_turn_without_dispatch(client, admin, monkeypatch):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)

    async def fake_classify(*args, **kwargs):
        return IntentDecision(
            intent="analysis",
            confidence=0.95,
            reason="当前问题可以由主 Agent 直接回答。",
            requires_account_context=False,
        )

    async def fake_decide_next(*args, **kwargs):
        return RuntimeNextStep(
            action="respond",
            rationale="无需专家或工具。",
            handoff_message="这次不需要调用专家，我先直接回答你。",
        )

    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.BrainIntelligence.classify",
        fake_classify,
    )
    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.BrainIntelligence.decide_next",
        fake_decide_next,
    )

    response = await client.post(
        "/brain/messages",
        headers=headers,
        json={"message": "现在先告诉我你的判断", "platform": "douyin"},
    )

    assert response.status_code == 201
    runtime = response.json()
    assert runtime["status"] == "completed"
    assert runtime["invocations"] == []
    assert runtime["tool_calls"] == []
    assert any(
        event["type"] == "brain.runtime.message_done"
        and event["payload"]["content"] == "这次不需要调用专家，我先直接回答你。"
        for event in runtime["timeline"]
    )


@pytest.mark.asyncio
async def test_brain_runtime_cannot_replace_required_expert_with_direct_response(
    client,
    admin,
    monkeypatch,
):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    project_id, account_id = await _project_bound_douyin_account(client, headers)

    async def fake_classify(*args, **kwargs):
        return IntentDecision(
            intent="analysis",
            confidence=0.98,
            reason="Account positioning requires the positioning expert.",
            suggested_expert_codes=["01-positioning"],
            requires_account_context=True,
        )

    decisions = iter(
        [
            RuntimeNextStep(
                action="respond",
                rationale="Incorrectly attempted a direct answer.",
                handoff_message="The main agent should not publish this analysis.",
            ),
            RuntimeNextStep(
                action="finish",
                rationale="The expert result is now available.",
                handoff_message="The main agent may now summarize the expert result.",
            ),
        ]
    )

    async def fake_decide_next(*args, **kwargs):
        return next(decisions)

    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.BrainIntelligence.classify",
        fake_classify,
    )
    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.BrainIntelligence.decide_next",
        fake_decide_next,
    )

    response = await client.post(
        "/brain/messages",
        headers=headers,
        json={
            "message": "Run an account positioning diagnosis.",
            "project_id": project_id,
            "account_id": account_id,
            "platform": "douyin",
        },
    )

    assert response.status_code == 201, response.text
    runtime = response.json()
    assert [row["agent_code"] for row in runtime["invocations"]] == ["01-positioning"]
    effective_steps = [
        event["payload"]["action"]
        for event in runtime["timeline"]
        if event["type"] == "brain.runtime.next_step"
    ]
    assert effective_steps == ["dispatch_experts", "finish"]
    assert not any(
        event["type"] == "brain.runtime.message_done"
        and event["payload"].get("content") == "The main agent should not publish this analysis."
        for event in runtime["timeline"]
    )


@pytest.mark.asyncio
async def test_brain_runtime_ask_user_reports_waiting_user(client, admin, monkeypatch):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)

    async def fake_classify(*args, **kwargs):
        return IntentDecision(
            intent="analysis",
            confidence=0.91,
            reason="还缺少目标周期。",
            requires_account_context=False,
        )

    async def fake_decide_next(*args, **kwargs):
        return RuntimeNextStep(
            action="ask_user",
            rationale="缺少执行周期。",
            handoff_message="你希望我分析最近 7 天、30 天，还是自定义周期？",
        )

    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.BrainIntelligence.classify",
        fake_classify,
    )
    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.BrainIntelligence.decide_next",
        fake_decide_next,
    )

    response = await client.post(
        "/brain/messages",
        headers=headers,
        json={"message": "分析一下近期表现", "platform": "douyin"},
    )

    assert response.status_code == 201
    runtime = response.json()
    assert runtime["status"] == "waiting_user"
    assert any(
        event["type"] == "brain.runtime.clarification_requested"
        for event in runtime["timeline"]
    )


@pytest.mark.asyncio
async def test_request_permission_still_obeys_registered_tool_policy(
    client,
    admin,
    monkeypatch,
):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = await _authorized_douyin_account(client, headers, "权限路由测试账号")

    async def fake_classify(*args, **kwargs):
        return IntentDecision(
            intent="analysis",
            confidence=0.98,
            reason="需要读取当前账号概况。",
            requires_account_context=True,
        )

    steps = iter(
        [
            RuntimeNextStep(
                action="request_permission",
                tool_calls=[
                    RuntimeToolCall(
                        tool_code="account.profile",
                        arguments={},
                        purpose="读取当前账号公开概况",
                        idempotency_key="permission-policy-account-profile",
                    )
                ],
                rationale="先经过统一工具边界。",
                handoff_message="我先检查这项工具是否需要你的确认。",
            ),
            RuntimeNextStep(
                action="finish",
                rationale="工具结果已经足够。",
                handoff_message="账号状态已经确认。",
            ),
        ]
    )

    async def fake_decide_next(*args, **kwargs):
        return next(steps)

    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.BrainIntelligence.classify",
        fake_classify,
    )
    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.BrainIntelligence.decide_next",
        fake_decide_next,
    )

    response = await client.post(
        "/brain/messages",
        headers=headers,
        json={
            "message": "检查当前账号状态",
            "account_id": account_id,
            "platform": "douyin",
        },
    )

    assert response.status_code == 201
    runtime = response.json()
    assert runtime["status"] == "completed"
    assert runtime["pending_permissions"] == []
    assert runtime["tool_calls"][0]["status"] == "success"


@pytest.mark.asyncio
async def test_brain_strategy_decision_is_selected_and_runtime_resumes(client, admin, monkeypatch):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = await _authorized_douyin_account(client, headers)

    async def fake_classify(*args, **kwargs):
        return IntentDecision(
            intent="workflow",
            confidence=0.94,
            reason="需要先完成账号定位。",
            suggested_expert_codes=["01-positioning"],
            requires_account_context=True,
        )

    next_steps = iter(
        [
            RuntimeNextStep(
                action="request_decision",
                expert_codes=[],
                rationale="存在两条投入不同的内容路线。",
                handoff_message="定位已经明确，接下来需要你选择内容主线。",
                decision_request=DecisionRequest(
                    id="content-direction-1",
                    title="下周优先采用哪条内容主线？",
                    summary="两条路线都符合当前定位，但增长节奏和投入不同。",
                    choices=[
                        DecisionChoice(
                            id="authority",
                            title="专业权威线",
                            description="强化参数拆解与避坑内容。",
                            benefit="稳定积累信任",
                            tradeoff="增长速度相对慢",
                            recommended=True,
                        ),
                        DecisionChoice(
                            id="conflict",
                            title="冲突测评线",
                            description="用对比和反转提升点击率。",
                            benefit="更容易获得初始播放",
                            tradeoff="需要更高素材投入",
                        ),
                    ],
                ),
            ),
            RuntimeNextStep(
                action="finish",
                expert_codes=[],
                rationale="用户已经确定内容主线。",
                handoff_message="方向已经确定，我来汇总下一步执行建议。",
            ),
        ]
    )

    async def fake_decide_next(*args, **kwargs):
        return next(next_steps)

    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.BrainIntelligence.classify", fake_classify
    )
    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.BrainIntelligence.decide_next",
        fake_decide_next,
    )

    created = await client.post(
        "/brain/messages",
        headers=headers,
        json={
            "message": "分析账号并规划下周内容",
            "account_id": account_id,
            "platform": "douyin",
        },
    )
    assert created.status_code == 201
    runtime = created.json()
    assert [row["id"] for row in runtime["pending_decisions"]] == ["content-direction-1"]

    enqueued: list[int] = []

    async def fake_enqueue(*, run_id: int):
        enqueued.append(run_id)

    monkeypatch.setattr("app.config.settings.agent_runtime_async_enabled", True)
    monkeypatch.setattr("app.api.brain.enqueue_agent_runtime", fake_enqueue)

    selected = await client.post(
        f"/brain/tasks/{runtime['task']['id']}/decisions/content-direction-1/select",
        headers=headers,
        json={"choice_id": "authority"},
    )

    assert selected.status_code == 200
    resumed = selected.json()
    assert resumed["pending_decisions"] == []
    assert resumed["status"] == "running"
    assert len(enqueued) == 1
    assert any(
        event["type"] == "brain.runtime.decision_selected"
        and event["payload"]["choice_id"] == "authority"
        for event in resumed["timeline"]
    )


@pytest.mark.asyncio
async def test_brain_strategy_decision_can_generate_a_new_option_set(client, admin, monkeypatch):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = await _authorized_douyin_account(client, headers)

    async def fake_classify(*args, **kwargs):
        return IntentDecision(
            intent="workflow",
            confidence=0.94,
            reason="需要先完成账号定位。",
            suggested_expert_codes=["01-positioning"],
            requires_account_context=True,
        )

    async def fake_decide_next(*args, **kwargs):
        return RuntimeNextStep(
            action="request_decision",
            expert_codes=[],
            rationale="存在两条投入不同的内容路线。",
            handoff_message="请选择下周内容主线。",
            decision_request=DecisionRequest(
                id="content-direction-1",
                title="下周优先采用哪条内容主线？",
                summary="两条路线都符合当前定位。",
                choices=[
                    DecisionChoice(
                        id="authority",
                        title="专业权威线",
                        description="强化参数拆解。",
                        benefit="稳定积累信任",
                        tradeoff="增长速度相对慢",
                        recommended=True,
                    ),
                    DecisionChoice(
                        id="conflict",
                        title="冲突测评线",
                        description="用对比提升点击率。",
                        benefit="更容易获得播放",
                        tradeoff="素材投入较高",
                    ),
                ],
            ),
        )

    async def fake_revise_decision(*args, **kwargs):
        return DecisionRequest(
            id="ignored-model-id",
            title="换一批后，下周优先采用哪条内容主线？",
            summary="根据你的意见重新整理了两条路线。",
            choices=[
                DecisionChoice(
                    id="series",
                    title="连续栏目线",
                    description="围绕一个主题连续更新。",
                    benefit="更容易形成用户记忆",
                    tradeoff="前期需要统一策划",
                    recommended=True,
                ),
                DecisionChoice(
                    id="qa",
                    title="用户问答线",
                    description="从真实问题切入选题。",
                    benefit="互动意愿更强",
                    tradeoff="依赖问题样本积累",
                ),
            ],
        )

    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.BrainIntelligence.classify", fake_classify
    )
    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.BrainIntelligence.decide_next",
        fake_decide_next,
    )
    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.BrainIntelligence.revise_decision",
        fake_revise_decision,
    )

    created = await client.post(
        "/brain/messages",
        headers=headers,
        json={
            "message": "分析账号并规划下周内容",
            "account_id": account_id,
            "platform": "douyin",
        },
    )
    assert created.status_code == 201
    runtime = created.json()

    revised = await client.post(
        f"/brain/tasks/{runtime['task']['id']}/decisions/content-direction-1/revise",
        headers=headers,
        json={"comment": "再给我一批更适合连续更新的方向", "request_new_options": True},
    )

    assert revised.status_code == 200
    pending = revised.json()["pending_decisions"]
    assert len(pending) == 1
    assert pending[0]["id"].startswith("content-direction-1-revision-")
    assert [choice["id"] for choice in pending[0]["choices"]] == ["series", "qa"]


@pytest.mark.asyncio
async def test_brain_task_lifecycle(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = await _authorized_douyin_account(client, headers)

    draft = await client.post(
        "/brain/tasks/draft",
        headers=headers,
        json={
            "goal": "为 7 月新品做一组抖音冷启动内容",
            "platforms": ["douyin"],
            "account_ids": [account_id],
        },
    )
    assert draft.status_code == 201
    task = draft.json()
    assert task["status"] == "pending_confirmation"
    assert task["brief"]["goal"].startswith("为 7 月新品")
    assert task["plan"]["steps"][0]["agent_code"] == "01-positioning"

    listing = await client.get("/brain/tasks", headers=headers)
    assert listing.status_code == 200
    assert listing.json()[0]["id"] == task["id"]

    confirmed = await client.post(f"/brain/tasks/{task['id']}/confirm", headers=headers)
    assert confirmed.status_code == 200
    confirmed_body = confirmed.json()
    assert confirmed_body["status"] == "pending_acceptance"
    assert confirmed_body["content_item_id"] is not None
    assert "质量门" in confirmed_body["current_focus"]

    invocations = await client.get(f"/brain/tasks/{task['id']}/invocations", headers=headers)
    assert invocations.status_code == 200
    invocation_rows = invocations.json()
    assert {row["agent_code"] for row in invocation_rows} == {
        "01-positioning",
        "02-content-director",
    }
    assert all(row["status"] == "done" for row in invocation_rows)

    tool_calls = await client.get(f"/brain/tasks/{task['id']}/tool-calls", headers=headers)
    assert tool_calls.status_code == 200
    tool_rows = tool_calls.json()
    assert {row["tool_code"] for row in tool_rows} >= {
        "account_context",
        "profile_snapshot",
        "brief_builder",
        "compliance_precheck",
    }
    assert any(row["permission_mode"] == "auto" for row in tool_rows)
    assert (
        next(row for row in tool_rows if row["tool_code"] == "brief_builder")[
            "requires_human_confirmation"
        ]
        is False
    )
    assert any(row["requires_human_confirmation"] is True for row in tool_rows)

    pending_tool_approvals = await client.get(
        "/brain/tool-calls/pending-approvals", headers=headers
    )
    assert pending_tool_approvals.status_code == 200
    pending_tool_rows = pending_tool_approvals.json()
    assert any(row["task_id"] == task["id"] for row in pending_tool_rows)
    approval_id = next(
        row["id"] for row in pending_tool_rows if row["tool_code"] == "compliance_precheck"
    )

    approved_tool = await client.post(
        f"/brain/tool-calls/{approval_id}/approve",
        headers=headers,
        json={"approved": True, "comment": "确认通过"},
    )
    assert approved_tool.status_code == 200
    assert approved_tool.json()["status"] == "success"
    assert approved_tool.json()["meta"]["decision"]["approved"] is True

    after_tool_approvals = await client.get("/brain/tool-calls/pending-approvals", headers=headers)
    assert all(row["id"] != approval_id for row in after_tool_approvals.json())

    acceptances = await client.get(f"/brain/tasks/{task['id']}/acceptances", headers=headers)
    assert acceptances.status_code == 200
    acceptance_rows = acceptances.json()
    assert {row["deliverable_type"] for row in acceptance_rows} == {
        "positioning_strategy",
        "video_script",
    }
    acceptance_id = next(
        row["id"] for row in acceptance_rows if row["deliverable_type"] == "video_script"
    )

    rejudge = await client.post(
        f"/brain/tasks/{task['id']}/rejudge",
        headers=headers,
        json={"acceptance_id": acceptance_id},
    )
    assert rejudge.status_code == 200
    assert rejudge.json()["status"] == "rerun_requested"
    assert rejudge.json()["brain_rejudge_summary"]

    for row in acceptance_rows:
        accepted = await client.post(
            f"/brain/tasks/{task['id']}/accept",
            headers=headers,
            json={"acceptance_id": row["id"], "reviewer_note": "验收通过"},
        )
        assert accepted.status_code == 200
        assert accepted.json()["status"] == "approved"

    closed = await client.post(f"/brain/tasks/{task['id']}/close-memory", headers=headers)
    assert closed.status_code == 200
    assert closed.json()["closed"] is True


@pytest.mark.asyncio
async def test_brain_runtime_applies_expert_tool_permissions(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = await _authorized_douyin_account(client, headers)
    configured = await client.put(
        "/agents/02-content-director/management",
        headers=headers,
        json={
            "enabled": True,
            "responsibility": "产出可审核的抖音脚本。",
            "system_prompt": "不编造产品参数。",
            "tool_permissions": {
                "brief_builder": "auto",
                "compliance_precheck": "manual",
            },
            "quality_gates": ["topic_review", "script_compliance"],
        },
    )
    assert configured.status_code == 200

    draft = await client.post(
        "/brain/tasks/draft",
        headers=headers,
        json={
            "goal": "生成一条抖音新品脚本",
            "platforms": ["douyin"],
            "account_ids": [account_id],
        },
    )
    assert draft.status_code == 201
    task_id = draft.json()["id"]
    content_step = next(
        step
        for step in draft.json()["plan"]["steps"]
        if step["agent_code"] == "02-content-director"
    )
    assert content_step["tool_permissions"] == {
        "brief_builder": "auto",
        "compliance_precheck": "manual",
    }

    confirmed = await client.post(f"/brain/tasks/{task_id}/confirm", headers=headers)
    assert confirmed.status_code == 200
    calls = (await client.get(f"/brain/tasks/{task_id}/tool-calls", headers=headers)).json()
    brief = next(row for row in calls if row["tool_code"] == "brief_builder")
    compliance = next(row for row in calls if row["tool_code"] == "compliance_precheck")
    assert brief["permission_mode"] == "auto"
    assert brief["requires_human_confirmation"] is False
    assert compliance["permission_mode"] == "manual"
    assert compliance["requires_human_confirmation"] is True


@pytest.mark.asyncio
async def test_brain_rejects_workflow_when_main_agent_is_disabled(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = await _authorized_douyin_account(client, headers)
    management = (await client.get("/agents/00-decision/management", headers=headers)).json()
    management["enabled"] = False
    disabled = await client.put(
        "/agents/00-decision/management",
        headers=headers,
        json={
            key: management[key]
            for key in (
                "enabled",
                "responsibility",
                "system_prompt",
                "tool_permissions",
                "quality_gates",
            )
        },
    )
    assert disabled.status_code == 200

    draft = await client.post(
        "/brain/tasks/draft",
        headers=headers,
        json={
            "goal": "生成一条抖音新品脚本",
            "platforms": ["douyin"],
            "account_ids": [account_id],
        },
    )

    assert draft.status_code == 409
    assert draft.json()["detail"] == "该专家已停用"


@pytest.mark.asyncio
async def test_brain_confirm_starts_visible_langgraph_runtime(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = await _authorized_douyin_account(client, headers)

    draft = await client.post(
        "/brain/tasks/draft",
        headers=headers,
        json={
            "goal": "为抖音账号做一轮冷启动选题和脚本",
            "platforms": ["douyin"],
            "account_ids": [account_id],
        },
    )
    task_id = draft.json()["id"]

    confirmed = await client.post(f"/brain/tasks/{task_id}/confirm", headers=headers)

    assert confirmed.status_code == 200
    confirmed_body = confirmed.json()
    assert confirmed_body["runtime_mode"] == "langgraph"
    assert confirmed_body["thread_id"] == f"brain-task-{task_id}"

    runtime = await client.get(f"/brain/tasks/{task_id}/runtime", headers=headers)

    assert runtime.status_code == 200
    runtime_body = runtime.json()
    assert runtime_body["task"]["id"] == task_id
    assert runtime_body["thread_id"] == f"brain-task-{task_id}"
    assert runtime_body["status"] == "waiting_permission"
    assert runtime_body["pending_permissions"]
    assert runtime_body["next_actions"] == ["review_pending_permissions"]
    event_types = [event["type"] for event in runtime_body["timeline"]]
    assert "brain.runtime.started" in event_types
    assert "brain.runtime.plan_created" in event_types
    assert "brain.runtime.subagent_started" in event_types
    assert "brain.runtime.permission_request" in event_types


@pytest.mark.asyncio
async def test_brain_confirm_keeps_greeting_as_plain_chat(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = await _authorized_douyin_account(client, headers)

    draft = await client.post(
        "/brain/tasks/draft",
        headers=headers,
        json={"goal": "你好", "platforms": ["douyin"], "account_ids": [account_id]},
    )

    assert draft.status_code == 201
    draft_body = draft.json()
    assert draft_body["plan"]["steps"] == []
    assert draft_body["plan"]["requires_human_confirmation"] is False

    confirmed = await client.post(f"/brain/tasks/{draft_body['id']}/confirm", headers=headers)

    assert confirmed.status_code == 200
    confirmed_body = confirmed.json()
    assert confirmed_body["status"] == "completed"
    assert confirmed_body["current_focus"] == "运营大脑已完成普通对话，未启动专家工作流"

    runtime = await client.get(f"/brain/tasks/{draft_body['id']}/runtime", headers=headers)
    runtime_body = runtime.json()
    assert runtime_body["status"] == "completed"
    assert runtime_body["pending_permissions"] == []
    assert runtime_body["invocations"] == []
    assert runtime_body["tool_calls"] == []
    messages = [
        event["payload"]["content"]
        for event in runtime_body["timeline"]
        if event["type"] == "brain.runtime.message_done"
    ]
    assert messages == [
        "你好，我在。你可以直接告诉我具体运营目标，例如账号诊断、内容选题、脚本生成、"
        "发布前检查或复盘分析；只有明确进入工作流时，我才会调用专家 Agent。"
    ]


@pytest.mark.asyncio
async def test_tool_approval_records_runtime_resume_event(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = await _authorized_douyin_account(client, headers)

    draft = await client.post(
        "/brain/tasks/draft",
        headers=headers,
        json={
            "goal": "为抖音账号生成一条合规短视频脚本",
            "platforms": ["douyin"],
            "account_ids": [account_id],
        },
    )
    task_id = draft.json()["id"]
    await client.post(f"/brain/tasks/{task_id}/confirm", headers=headers)
    pending = (await client.get("/brain/tool-calls/pending-approvals", headers=headers)).json()
    approval_id = next(row["id"] for row in pending if row["task_id"] == task_id)

    approved = await client.post(
        f"/brain/tool-calls/{approval_id}/approve",
        headers=headers,
        json={"approved": True, "comment": "确认继续"},
    )

    assert approved.status_code == 200
    runtime = await client.get(f"/brain/tasks/{task_id}/runtime", headers=headers)
    event_types = [event["type"] for event in runtime.json()["timeline"]]
    assert "brain.runtime.resumed" in event_types


@pytest.mark.asyncio
async def test_smart_runtime_resumes_from_permission_with_decision_in_parent_context(
    client, admin, monkeypatch
):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    project_id, account_id = await _project_bound_douyin_account(
        client,
        headers,
        account_name="Permission resume account",
        project_name="Permission resume project",
    )
    observed_rounds: list[list[dict]] = []

    async def controlled_publish_tool(_params, _context):
        return {"prepared": True}

    async def fake_classify(*args, **kwargs):
        return IntentDecision(
            intent="action",
            confidence=0.97,
            reason="需要生成受控发布准备结果。",
            suggested_expert_codes=["06-operator"],
            requires_account_context=True,
        )

    async def fake_decide_next(
        self, session, org_id, goal, observations, available_experts, round_index
    ):
        observed_rounds.append(observations)
        if not observations:
            return RuntimeNextStep(
                action="dispatch_experts",
                expert_codes=["06-operator"],
                rationale="需要先由账号运营专家生成受控发布准备结果。",
                handoff_message="我先让账号运营专家整理发布准备项。",
            )
        if not any(item.get("kind") == "tool_permission" for item in observations):
            return RuntimeNextStep(
                action="request_permission",
                tool_calls=[
                    RuntimeToolCall(
                        tool_code="publish.prepare",
                        arguments={},
                        purpose="生成受控发布准备结果",
                        idempotency_key="permission-resume-publish-1",
                    )
                ],
                rationale="发布准备属于受控动作，需要用户确认。",
                handoff_message="生成发布准备结果前，需要你确认。",
            )
        return RuntimeNextStep(
            action="finish",
            expert_codes=[],
            rationale="权限结果已返回。",
            handoff_message="我已根据你的确认完成本轮处理。",
        )

    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.BrainIntelligence.classify", fake_classify
    )
    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence.BrainIntelligence.decide_next",
        fake_decide_next,
    )
    monkeypatch.setattr(
        "app.orchestrator.brain_runtime.build_runtime_tool_adapter",
        lambda: ToolAdapter(
            [
                ToolSpec(
                    name="publish.prepare",
                    handler=controlled_publish_tool,
                    params_model=EmptyParams,
                    allowed_roles=frozenset({UserRole.ADMIN}),
                    permission_mode="confirm",
                    scope="account",
                )
            ]
        ),
    )

    created = await client.post(
        "/brain/messages",
        headers=headers,
        json={
            "message": "整理一份抖音发布包并进入人工确认",
            "project_id": project_id,
            "account_id": account_id,
            "platform": "douyin",
        },
    )
    assert created.status_code == 201
    runtime = created.json()
    assert runtime["status"] == "waiting_permission"

    for permission in runtime["pending_permissions"]:
        approved = await client.post(
            f"/brain/tool-calls/{permission['id']}/approve",
            headers=headers,
            json={"approved": True, "comment": "允许本轮生成，不自动发布"},
        )
        assert approved.status_code == 200

    assert observed_rounds
    assert any(
        observation.get("kind") == "tool_permission"
        and observation.get("approved") is True
        and observation.get("comment") == "允许本轮生成，不自动发布"
        for observation in observed_rounds[-1]
    )


@pytest.mark.asyncio
async def test_runtime_endpoint_handles_legacy_brain_tasks(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = await _authorized_douyin_account(client, headers)
    draft = await client.post(
        "/brain/tasks/draft",
        headers=headers,
        json={
            "goal": "诊断账号完播率下降原因",
            "platforms": ["douyin"],
            "account_ids": [account_id],
        },
    )
    task_id = draft.json()["id"]

    runtime = await client.get(f"/brain/tasks/{task_id}/runtime", headers=headers)

    assert runtime.status_code == 200
    body = runtime.json()
    assert body["task"]["id"] == task_id
    assert body["status"] == "legacy"
    assert body["timeline"] == []
    assert body["pending_permissions"] == []


@pytest.mark.asyncio
async def test_brain_confirm_creates_content_item_and_pending_gate(client, admin, session):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = await _authorized_douyin_account(client, headers)

    draft = await client.post(
        "/brain/tasks/draft",
        headers=headers,
        json={
            "goal": "为露营新品生成一轮短视频脚本",
            "platforms": ["douyin"],
            "account_ids": [account_id],
        },
    )
    task_id = draft.json()["id"]

    confirmed = await client.post(f"/brain/tasks/{task_id}/confirm", headers=headers)
    assert confirmed.status_code == 200
    content_item_id = confirmed.json()["content_item_id"]

    content_item = await session.get(ContentItem, content_item_id)
    assert content_item is not None
    assert content_item.title.startswith("为露营新品")
    assert content_item.account_id == account_id

    pending_gate = await session.scalar(
        select(GateApproval).where(
            GateApproval.content_item_id == content_item_id,
            GateApproval.status == GateStatus.PENDING,
        )
    )
    assert pending_gate is not None
    assert pending_gate.gate == GateType.SCRIPT_COMPLIANCE
    approval_events = list(
        await session.scalars(
            select(Event).where(
                Event.type == "approval.requested",
                Event.content_item_id == content_item_id,
            )
        )
    )
    assert {row.payload["approval_kind"] for row in approval_events} >= {
        "gate",
        "deliverable",
        "tool_call",
    }


@pytest.mark.asyncio
async def test_brain_draft_binds_project_account_group_platforms_and_accounts(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    project_id = (
        await client.post("/projects", headers=headers, json={"name": "露营项目"})
    ).json()["id"]
    group_id = (
        await client.post(
            "/account-groups",
            headers=headers,
            json={"name": "露营账号组", "dimension": "track"},
        )
    ).json()["id"]
    douyin_account_id = (
        await client.post(
            "/accounts",
            headers=headers,
            json={"nickname": "露营一号", "platform": "douyin", "group_id": group_id},
        )
    ).json()["id"]
    await client.patch(
        f"/accounts/{douyin_account_id}/integration",
        headers=headers,
        json={
            "integration_status": "manual",
            "auth_status": "manual",
            "data_sync_status": "manual",
        },
    )
    xhs_account_id = (
        await client.post(
            "/accounts",
            headers=headers,
            json={"nickname": "小红书一号", "platform": "xiaohongshu", "group_id": group_id},
        )
    ).json()["id"]

    draft = await client.post(
        "/brain/tasks/draft",
        headers=headers,
        json={
            "goal": "为露营项目生成矩阵分发计划",
            "project_id": project_id,
            "account_group_id": group_id,
            "platforms": ["douyin"],
            "account_ids": [douyin_account_id],
        },
    )

    assert draft.status_code == 201
    brief = draft.json()["brief"]
    assert brief["project_id"] == project_id
    assert brief["project_name"] == "露营项目"
    assert brief["account_group_id"] == group_id
    assert brief["account_group_name"] == "露营账号组"
    assert brief["platforms"] == ["douyin"]
    assert brief["account_ids"] == [douyin_account_id]

    mismatch = await client.post(
        "/brain/tasks/draft",
        headers=headers,
        json={
            "goal": "平台不匹配应拒绝",
            "account_group_id": group_id,
            "platforms": ["douyin"],
            "account_ids": [xhs_account_id],
        },
    )
    assert mismatch.status_code == 400


@pytest.mark.asyncio
async def test_brain_draft_plan_selects_only_experts_required_by_the_goal(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = await _authorized_douyin_account(client, headers)

    content_task = await client.post(
        "/brain/tasks/draft",
        headers=headers,
        json={
            "goal": "为 7 月新品做一组短视频内容",
            "platforms": ["douyin"],
            "account_ids": [account_id],
        },
    )
    assert content_task.status_code == 201
    content_steps = {step["id"]: step for step in content_task.json()["plan"]["steps"]}
    assert list(content_steps) == ["step-positioning", "step-script"]
    assert content_steps["step-script"]["depends_on"] == ["step-positioning"]
    assert all(step["status"] == "planned" for step in content_steps.values())

    diagnosis_task = await client.post(
        "/brain/tasks/draft",
        headers=headers,
        json={
            "goal": "诊断账号完播率下降原因",
            "platforms": ["douyin"],
            "account_ids": [account_id],
        },
    )
    assert diagnosis_task.status_code == 201
    diagnosis_steps = {step["id"]: step for step in diagnosis_task.json()["plan"]["steps"]}
    assert list(diagnosis_steps) == ["step-positioning", "step-operation"]
    assert diagnosis_steps["step-operation"]["depends_on"] == ["step-positioning"]

    production_task = await client.post(
        "/brain/tasks/draft",
        headers=headers,
        json={
            "goal": "生成脚本和视觉提示词，制作视频素材并剪辑成片，最后准备发布",
            "platforms": ["douyin"],
            "account_ids": [account_id],
        },
    )
    assert production_task.status_code == 201
    production_steps = [step["id"] for step in production_task.json()["plan"]["steps"]]
    assert production_steps == [
        "step-positioning",
        "step-script",
        "step-art",
        "step-video",
        "step-editing",
        "step-operation",
    ]


@pytest.mark.asyncio
async def test_brain_runtime_executes_only_experts_selected_by_the_main_agent(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = await _authorized_douyin_account(client, headers)

    draft = await client.post(
        "/brain/tasks/draft",
        headers=headers,
        json={
            "goal": "诊断账号完播率下降原因",
            "platforms": ["douyin"],
            "account_ids": [account_id],
        },
    )
    task_id = draft.json()["id"]

    confirmed = await client.post(f"/brain/tasks/{task_id}/confirm", headers=headers)

    assert confirmed.status_code == 200
    invocations = (await client.get(f"/brain/tasks/{task_id}/invocations", headers=headers)).json()
    assert [row["agent_code"] for row in invocations] == ["01-positioning", "06-operator"]
    acceptances = (await client.get(f"/brain/tasks/{task_id}/acceptances", headers=headers)).json()
    assert {row["deliverable_type"] for row in acceptances} == {
        "positioning_strategy",
        "review_report",
    }


@pytest.mark.asyncio
async def test_rerun_requires_reason(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = await _authorized_douyin_account(client, headers)
    draft = await client.post(
        "/brain/tasks/draft",
        headers=headers,
        json={
            "goal": "诊断账号完播率下降原因",
            "platforms": ["douyin"],
            "account_ids": [account_id],
        },
    )
    task_id = draft.json()["id"]
    await client.post(f"/brain/tasks/{task_id}/confirm", headers=headers)
    acceptance = (await client.get(f"/brain/tasks/{task_id}/acceptances", headers=headers)).json()[
        0
    ]

    resp = await client.post(
        f"/brain/tasks/{task_id}/rerun",
        headers=headers,
        json={"acceptance_id": acceptance["id"], "reason": "", "rerun_scope": "downstream"},
    )
    assert resp.status_code == 422
